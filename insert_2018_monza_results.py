import os
import fastf1
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from fastf1_cache import fastf1_cache_dir

load_dotenv()
engine = create_engine(os.getenv('DATABASE_URL'))
fastf1.Cache.enable_cache(fastf1_cache_dir())

with engine.connect() as conn:
    race_id = conn.execute(text("""
        SELECT id FROM races WHERE season_year = 2018 AND round_number = 14
    """)).scalar()

    session_id = conn.execute(text("""
        SELECT id FROM sessions WHERE race_id = :r AND session_type = 'R'
    """), {"r": race_id}).scalar()

    if session_id is None:
        session_id = conn.execute(text("""
            INSERT INTO sessions (race_id, session_type, start_time)
            VALUES (:r, 'R', NULL) RETURNING id
        """), {"r": race_id}).scalar()
        conn.commit()
        print(f"Created session row id={session_id}")
    else:
        print(f"Using existing session row id={session_id}")

    session = fastf1.get_session(2018, 14, 'Race')
    session.load(laps=False, telemetry=False, weather=False, messages=False)

    inserted = 0
    for _, row in session.results.iterrows():
        entry_row = conn.execute(text("""
            SELECT re.id FROM race_entries re
            JOIN drivers d ON d.id = re.driver_id
            WHERE re.race_id = :r AND d.name = :name
        """), {"r": race_id, "name": row['FullName']}).fetchone()

        if entry_row is None:
            print(f"  [WARN] No race_entry found for {row['FullName']} ({row['Abbreviation']}) -- skipping")
            continue

        entry_id = entry_row[0]

        conn.execute(text("""
            INSERT INTO race_results (session_id, race_entry_id, finishing_position, points, status)
            VALUES (:s, :e, :pos, :pts, :status)
        """), {
            "s": session_id,
            "e": entry_id,
            "pos": int(row['Position']) if pd.notna(row['Position']) else None,
            "pts": float(row['Points']) if pd.notna(row['Points']) else 0,
            "status": row['Status'],
        })
        inserted += 1

    conn.commit()
    print(f"Inserted {inserted} race_results rows for 2018 Monza.")
