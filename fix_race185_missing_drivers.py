import os
import time
import fastf1
import pandas as pd
from sqlalchemy import create_engine, text

engine = create_engine(os.environ["DATABASE_URL"])

RACE_ID = 185
YEAR = 2026
ROUND = 12

# name -> (driver_id, real car_number from FastF1, team name)
MISSING_DRIVERS = {
    "Lando Norris": (24, 1, "McLaren"),
    "Liam Lawson": (44, 30, "Red Bull Racing"),
    "Franco Colapinto": (59, 43, "Alpine"),
    "Oliver Bearman": (55, 87, "Haas F1 Team"),
}

SESSION_TYPES = ["FP1", "SQ", "S", "Q", "R"]

def to_seconds(td):
    if pd.isna(td):
        return None
    return td.total_seconds()

with engine.connect() as conn:
    # resolve team_ids by name
    team_ids = {}
    for name, (did, num, team_name) in MISSING_DRIVERS.items():
        tid = conn.execute(text("SELECT id FROM teams WHERE name = :n"), {"n": team_name}).scalar()
        if tid is None:
            print(f"  ERROR: team '{team_name}' not found for {name}, skipping")
            continue
        team_ids[name] = tid

    # create missing race_entries (only if not already present)
    entry_ids = {}
    for name, (did, num, team_name) in MISSING_DRIVERS.items():
        existing = conn.execute(text("""
            SELECT id FROM race_entries WHERE race_id = :rid AND driver_id = :did
        """), {"rid": RACE_ID, "did": did}).scalar()
        if existing:
            entry_ids[name] = existing
            print(f"  {name}: race_entry already exists (id={existing})")
            continue
        eid = conn.execute(text("""
            INSERT INTO race_entries (race_id, driver_id, team_id, car_number, role)
            VALUES (:rid, :did, :tid, :num, 'race_driver') RETURNING id
        """), {"rid": RACE_ID, "did": did, "tid": team_ids[name], "num": num}).scalar()
        entry_ids[name] = eid
        print(f"  {name}: created race_entry id={eid} (car #{num})")
    conn.commit()

    for sess_type in SESSION_TYPES:
        print(f"\n=== {sess_type} ===")
        try:
            session = fastf1.get_session(YEAR, ROUND, sess_type)
            session.load(laps=True, telemetry=False, weather=False, messages=False)
        except Exception as e:
            print(f"  session unavailable: {e}")
            continue

        session_id = conn.execute(text("""
            SELECT id FROM sessions WHERE race_id = :rid AND session_type = :st
        """), {"rid": RACE_ID, "st": sess_type}).scalar()
        if session_id is None:
            print(f"  no {sess_type} session row in DB, skipping")
            continue

        abbr_map = {}
        for name, (did, num, _) in MISSING_DRIVERS.items():
            if name not in entry_ids:
                continue
            row = session.results[session.results['DriverNumber'] == str(num)]
            if len(row) == 0:
                print(f"    {name}: not in this session's results, skipping")
                continue
            row = row.iloc[0]
            abbr_map[row['Abbreviation']] = entry_ids[name]

            # race_results / qualifying_results insertion, mirroring backfill_history.py logic
            if sess_type == 'Q':
                exists = conn.execute(text("""
                    SELECT 1 FROM qualifying_results WHERE session_id=:s AND race_entry_id=:e
                """), {"s": session_id, "e": entry_ids[name]}).scalar()
                if not exists:
                    conn.execute(text("""
                        INSERT INTO qualifying_results (session_id, race_entry_id, q1_time, q2_time, q3_time, final_position)
                        VALUES (:s, :e, :q1, :q2, :q3, :p)
                    """), {
                        "s": session_id, "e": entry_ids[name],
                        "q1": to_seconds(row.get('Q1')), "q2": to_seconds(row.get('Q2')), "q3": to_seconds(row.get('Q3')),
                        "p": int(row['Position']) if pd.notna(row['Position']) else None
                    })
                    print(f"    {name}: inserted qualifying_results")

            if sess_type in ('R', 'S'):
                exists = conn.execute(text("""
                    SELECT 1 FROM race_results WHERE session_id=:s AND race_entry_id=:e
                """), {"s": session_id, "e": entry_ids[name]}).scalar()
                if not exists:
                    gap_seconds, gap_display = None, None
                    if row['Position'] == 1:
                        gap_seconds, gap_display = 0.0, 'Winner'
                    elif pd.notna(row['Time']):
                        gap_seconds = row['Time'].total_seconds()
                        gap_display = f"+{gap_seconds:.3f}s"
                    elif pd.notna(row.get('Status')):
                        gap_display = row['Status']
                    conn.execute(text("""
                        INSERT INTO race_results (session_id, race_entry_id, finishing_position, points, status,
                                                   gap_to_winner_seconds, gap_to_winner_display)
                        VALUES (:s, :e, :p, :pts, :st, :gs, :gd)
                    """), {
                        "s": session_id, "e": entry_ids[name],
                        "p": int(row['Position']) if pd.notna(row['Position']) else None,
                        "pts": float(row['Points']), "st": row['Status'],
                        "gs": gap_seconds, "gd": gap_display
                    })
                    print(f"    {name}: inserted race_results (P{row['Position']}, {row['Points']}pts)")

            # starting grid position (R session only)
            if sess_type == 'R':
                grid_pos = int(row['GridPosition']) if pd.notna(row['GridPosition']) else None
                if grid_pos is not None:
                    conn.execute(text("""
                        UPDATE race_results SET starting_grid_position = :gp
                        WHERE session_id = :s AND race_entry_id = :e
                    """), {"gp": grid_pos, "s": session_id, "e": entry_ids[name]})

        conn.commit()

        # laps, matched by Abbreviation now that we have it for these 4
        if sess_type != 'SQ' or True:  # laps apply to any session type that has them
            try:
                lap_count = 0
                for _, lap in session.laps.iterrows():
                    eid = abbr_map.get(lap['Driver'])
                    if eid is None:
                        continue
                    exists = conn.execute(text("""
                        SELECT 1 FROM laps WHERE session_id=:s AND race_entry_id=:e AND lap_number=:n
                    """), {"s": session_id, "e": eid, "n": int(lap['LapNumber'])}).scalar()
                    if exists:
                        continue
                    conn.execute(text("""
                        INSERT INTO laps (session_id, race_entry_id, lap_number, lap_time,
                                           sector_1_time, sector_2_time, sector_3_time, tire_compound, position)
                        VALUES (:s, :e, :n, :lt, :s1, :s2, :s3, :c, :pos)
                    """), {
                        "s": session_id, "e": eid, "n": int(lap['LapNumber']),
                        "lt": to_seconds(lap['LapTime']), "s1": to_seconds(lap['Sector1Time']),
                        "s2": to_seconds(lap['Sector2Time']), "s3": to_seconds(lap['Sector3Time']),
                        "c": lap.get('Compound'),
                        "pos": int(lap['Position']) if pd.notna(lap.get('Position')) else None
                    })
                    lap_count += 1
                conn.commit()
                print(f"    inserted {lap_count} laps")
            except Exception as e:
                print(f"    laps unavailable/error: {e}")

print("\nDone.")
