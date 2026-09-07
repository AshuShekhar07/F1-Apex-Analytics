import os
import time
import fastf1
import pandas as pd
from sqlalchemy import create_engine, text

engine = create_engine(os.environ["DATABASE_URL"])

with engine.connect() as conn:
    sessions = conn.execute(text("""
        SELECT s.id AS session_id, r.season_year, r.round_number, r.id AS race_id
        FROM sessions s
        JOIN races r ON r.id = s.race_id
        LEFT JOIN race_results rr ON rr.session_id = s.id
        WHERE s.session_type = 'S'
          AND rr.id IS NULL
          AND r.race_date <= CURRENT_DATE
        GROUP BY s.id, r.season_year, r.round_number, r.id
        ORDER BY r.season_year, r.round_number
    """)).mappings().all()

print(f"{len(sessions)} Sprint sessions need results backfill")

with engine.connect() as conn:
    for i, s in enumerate(sessions):
        year, rnd = s["season_year"], s["round_number"]
        entry_count = conn.execute(text(
            "SELECT COUNT(*) FROM race_entries WHERE race_id = :rid"
        ), {"rid": s["race_id"]}).scalar()
        if entry_count == 0:
            print(f"  [{i+1}/{len(sessions)}] {year} R{rnd} Sprint: race has NO backfilled data yet "
                  f"(any session type) -- needs a full backfill_history.py run first, skipping")
            continue

        try:
            session = fastf1.get_session(year, rnd, "S")
            session.load(laps=False, telemetry=False, weather=False, messages=False)

            winners = session.results[session.results['Position'] == 1]
            winner_time = winners.iloc[0]['Time'] if len(winners) > 0 else None

            inserted = 0
            for _, row in session.results.iterrows():
                name = row['FullName']
                if not name or name.strip().lower() in ('none none', 'nan nan', ''):
                    print(f"    WARNING: unresolved driver identity for car #{row['DriverNumber']}, skipping")
                    continue

                entry_id = conn.execute(text("""
                    SELECT re.id FROM race_entries re
                    JOIN drivers d ON d.id = re.driver_id
                    WHERE re.race_id = :rid AND d.name = :name
                """), {"rid": s["race_id"], "name": name}).scalar()

                if entry_id is None:
                    print(f"    WARNING: no existing race_entry for {name} in race {s['race_id']}, skipping")
                    continue

                gap_seconds = None
                gap_display = None
                if row['Position'] == 1:
                    gap_seconds = 0.0
                    gap_display = 'Winner'
                elif pd.notna(row['Time']):
                    gap_seconds = row['Time'].total_seconds()
                    gap_display = f"+{gap_seconds:.3f}s"
                elif pd.notna(row.get('Status')):
                    gap_display = row['Status']

                conn.execute(text("""
                    INSERT INTO race_results (session_id, race_entry_id, finishing_position, points, status,
                                               gap_to_winner_seconds, gap_to_winner_display)
                    VALUES (:s, :e, :p, :pts, :st, :gs, :gd)
                    ON CONFLICT DO NOTHING
                """), {
                    "s": s["session_id"], "e": entry_id,
                    "p": int(row['Position']) if pd.notna(row['Position']) else None,
                    "pts": float(row['Points']), "st": row['Status'],
                    "gs": gap_seconds, "gd": gap_display
                })
                inserted += 1
            conn.commit()
            print(f"  [{i+1}/{len(sessions)}] {year} R{rnd} Sprint: inserted {inserted} results")
        except Exception as e:
            print(f"  [{i+1}/{len(sessions)}] {year} R{rnd} Sprint: FAILED ({e})")
        time.sleep(0.3)

print("Done.")
