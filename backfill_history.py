import os
import time
import fastf1
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()
engine = create_engine(os.getenv('DATABASE_URL'))
fastf1.Cache.enable_cache('fastf1_cache')

SESSION_TYPES = ['FP1', 'FP2', 'FP3', 'Q', 'R']

def to_seconds(value):
    return value.total_seconds() if pd.notna(value) else None

def get_or_create_track(conn, name, country):
    tid = conn.execute(text("SELECT id FROM tracks WHERE name = :name"), {"name": name}).scalar()
    if tid is None:
        tid = conn.execute(text("INSERT INTO tracks (name, country) VALUES (:name, :country) RETURNING id"),
                            {"name": name, "country": country}).scalar()
    return tid

def get_or_create_race(conn, track_id, year, round_num, race_date):
    rid = conn.execute(text("SELECT id FROM races WHERE season_year = :y AND round_number = :r"),
                        {"y": year, "r": round_num}).scalar()
    if rid is None:
        rid = conn.execute(text("""
            INSERT INTO races (track_id, season_year, round_number, race_date)
            VALUES (:t, :y, :r, :d) RETURNING id
        """), {"t": track_id, "y": year, "r": round_num, "d": race_date}).scalar()
    return rid

def get_or_create_team(conn, name):
    tid = conn.execute(text("SELECT id FROM teams WHERE name = :name"), {"name": name}).scalar()
    if tid is None:
        tid = conn.execute(text("INSERT INTO teams (name) VALUES (:name) RETURNING id"), {"name": name}).scalar()
    return tid

def get_or_create_driver(conn, name, number):
    did = conn.execute(text("SELECT id FROM drivers WHERE name = :name"), {"name": name}).scalar()
    if did is None:
        did = conn.execute(text("INSERT INTO drivers (name, permanent_number) VALUES (:n, :num) RETURNING id"),
                            {"n": name, "num": number}).scalar()
    return did

def get_or_create_entry(conn, race_id, driver_id, team_id, car_number):
    eid = conn.execute(text("""
        SELECT id FROM race_entries WHERE race_id = :r AND driver_id = :d
    """), {"r": race_id, "d": driver_id}).scalar()
    if eid is None:
        eid = conn.execute(text("""
            INSERT INTO race_entries (race_id, driver_id, team_id, car_number)
            VALUES (:r, :d, :t, :c) RETURNING id
        """), {"r": race_id, "d": driver_id, "t": team_id, "c": car_number}).scalar()
    return eid

with engine.connect() as conn:
    for year in range(2018, 2027):
        print(f"\n=== {year} ===")
        try:
            schedule = fastf1.get_event_schedule(year)
        except Exception as e:
            print(f"  Could not get schedule: {e}")
            continue

        for _, event in schedule.iterrows():
            if event['EventFormat'] == 'testing':
                continue

            round_num = int(event['RoundNumber'])
            track_id = get_or_create_track(conn, event['Location'], event['Country'])
            race_id = get_or_create_race(conn, track_id, year, round_num, event['EventDate'].date())
            conn.commit()

            for sess_type in SESSION_TYPES:
                existing = conn.execute(text("""
                    SELECT id FROM sessions WHERE race_id = :r AND session_type = :s
                """), {"r": race_id, "s": sess_type}).scalar()
                if existing:
                    continue  # already ingested this session

                try:
                    session = fastf1.get_session(year, round_num, sess_type)
                    session.load()
                except Exception as e:
                    print(f"  Round {round_num} {sess_type}: unavailable ({e})")
                    continue

                driver_id_map = {}
                for _, row in session.results.iterrows():
                    team_id = get_or_create_team(conn, row['TeamName'])
                    driver_id = get_or_create_driver(conn, row['FullName'], int(row['DriverNumber']))
                    entry_id = get_or_create_entry(conn, race_id, driver_id, team_id, int(row['DriverNumber']))
                    driver_id_map[row['Abbreviation']] = entry_id
                conn.commit()

                session_id = conn.execute(text("""
                    INSERT INTO sessions (race_id, session_type, start_time)
                    VALUES (:r, :s, :t) RETURNING id
                """), {"r": race_id, "s": sess_type, "t": session.date}).scalar()
                conn.commit()

                # Laps + sector times (all session types)
                lap_count = 0
                for _, lap in session.laps.iterrows():
                    entry_id = driver_id_map.get(lap['Driver'])
                    if entry_id is None:
                        continue
                    conn.execute(text("""
                        INSERT INTO laps (session_id, race_entry_id, lap_number, lap_time,
                                           sector_1_time, sector_2_time, sector_3_time, tire_compound)
                        VALUES (:s, :e, :n, :lt, :s1, :s2, :s3, :c)
                    """), {
                        "s": session_id, "e": entry_id, "n": int(lap['LapNumber']),
                        "lt": to_seconds(lap['LapTime']), "s1": to_seconds(lap['Sector1Time']),
                        "s2": to_seconds(lap['Sector2Time']), "s3": to_seconds(lap['Sector3Time']),
                        "c": lap['Compound']
                    })
                    lap_count += 1
                conn.commit()

                # Qualifying-specific results
                if sess_type == 'Q':
                    for _, row in session.results.iterrows():
                        entry_id = driver_id_map.get(row['Abbreviation'])
                        if entry_id is None:
                            continue
                        conn.execute(text("""
                            INSERT INTO qualifying_results (session_id, race_entry_id, q1_time, q2_time, q3_time, final_position)
                            VALUES (:s, :e, :q1, :q2, :q3, :p)
                        """), {
                            "s": session_id, "e": entry_id,
                            "q1": to_seconds(row.get('Q1')), "q2": to_seconds(row.get('Q2')),
                            "q3": to_seconds(row.get('Q3')), "p": int(row['Position']) if pd.notna(row['Position']) else None
                        })
                    conn.commit()

                # Race results (wins/podiums derive from this later via query)
                if sess_type == 'R':
                    for _, row in session.results.iterrows():
                        entry_id = driver_id_map.get(row['Abbreviation'])
                        if entry_id is None:
                            continue
                        conn.execute(text("""
                            INSERT INTO race_results (session_id, race_entry_id, finishing_position, points, status)
                            VALUES (:s, :e, :p, :pts, :st)
                        """), {
                            "s": session_id, "e": entry_id,
                            "p": int(row['Position']) if pd.notna(row['Position']) else None,
                            "pts": float(row['Points']), "st": row['Status']
                        })
                    conn.commit()

                print(f"  Round {round_num} {sess_type}: {len(driver_id_map)} entries, {lap_count} laps")
                time.sleep(1)
