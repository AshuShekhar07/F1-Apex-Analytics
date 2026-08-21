import os
import fastf1
import pandas as pd
from sqlalchemy import create_engine, text

engine = create_engine(os.environ["DATABASE_URL"])

TARGETS = [
    (2020, 4, "FP1"),
    (2026, 5, "FP1"),
    (2026, 7, "FP1"),
    (2026, 7, "FP2"),
]

def to_seconds(td):
    if pd.isna(td):
        return None
    return td.total_seconds()

with engine.connect() as conn:
    for year, round_num, sess_type in TARGETS:
        print(f"\n=== {year} Round {round_num} {sess_type} ===")
        session = fastf1.get_session(year, round_num, sess_type)
        session.load(telemetry=False, weather=False, messages=False)

        row = conn.execute(text("""
            SELECT s.id AS session_id, r.id AS race_id
            FROM sessions s JOIN races r ON r.id = s.race_id
            WHERE r.season_year = :y AND r.round_number = :rd AND s.session_type = :st
        """), {"y": year, "rd": round_num, "st": sess_type}).mappings().first()

        if row is None:
            print(f"  session not found in DB, skipping")
            continue
        session_id, race_id = row["session_id"], row["race_id"]

        abbr_to_entry = {}
        for _, res_row in session.results.iterrows():
            num = int(res_row['DriverNumber'])
            entry_id = conn.execute(text("""
                SELECT id FROM race_entries WHERE race_id = :r AND car_number = :num
            """), {"r": race_id, "num": num}).scalar()
            if entry_id is None:
                print(f"    WARNING: no existing race_entry for car #{num} in race {race_id}, skipping")
                continue
            abbr_to_entry[res_row['Abbreviation']] = entry_id

        inserted = 0
        for _, lap in session.laps.iterrows():
            entry_id = abbr_to_entry.get(lap['Driver'])
            if entry_id is None:
                continue
            exists = conn.execute(text("""
                SELECT 1 FROM laps WHERE session_id = :s AND race_entry_id = :e AND lap_number = :n
            """), {"s": session_id, "e": entry_id, "n": int(lap['LapNumber'])}).scalar()
            if exists:
                continue
            conn.execute(text("""
                INSERT INTO laps (session_id, race_entry_id, lap_number, lap_time,
                                   sector_1_time, sector_2_time, sector_3_time, tire_compound)
                VALUES (:s, :e, :n, :lt, :s1, :s2, :s3, :c)
            """), {
                "s": session_id, "e": entry_id, "n": int(lap['LapNumber']),
                "lt": to_seconds(lap['LapTime']), "s1": to_seconds(lap['Sector1Time']),
                "s2": to_seconds(lap['Sector2Time']), "s3": to_seconds(lap['Sector3Time']),
                "c": lap.get('Compound')
            })
            inserted += 1
        conn.commit()
        print(f"  inserted {inserted} laps")
