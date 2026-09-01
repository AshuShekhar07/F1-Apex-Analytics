import os
import time
import fastf1
import pandas as pd
from sqlalchemy import create_engine, text

engine = create_engine(os.environ["DATABASE_URL"])

with engine.connect() as conn:
    sessions = conn.execute(text("""
        SELECT DISTINCT s.id AS session_id, r.season_year, r.round_number
        FROM sessions s
        JOIN races r ON r.id = s.race_id
        JOIN race_results rr ON rr.session_id = s.id
        WHERE s.session_type = 'R'
          AND rr.starting_grid_position IS NULL
          AND r.race_date <= CURRENT_DATE
        ORDER BY r.season_year, r.round_number
    """)).mappings().all()

print(f"{len(sessions)} race sessions need grid-position backfill")

with engine.connect() as conn:
    for i, s in enumerate(sessions):
        year, rnd = s["season_year"], s["round_number"]
        try:
            session = fastf1.get_session(year, rnd, "R")
            session.load(laps=False, telemetry=False, weather=False, messages=False)

            updated = 0
            for _, row in session.results.iterrows():
                name = row['FullName']
                if not name or name.strip().lower() in ('none none', 'nan nan', ''):
                    continue  # unresolved identity -- skip, don't guess
                grid_pos = int(row['GridPosition']) if pd.notna(row['GridPosition']) else None
                if grid_pos is None:
                    continue

                result = conn.execute(text("""
                    UPDATE race_results rr
                    SET starting_grid_position = :gp
                    FROM race_entries re, drivers d
                    WHERE rr.race_entry_id = re.id
                      AND re.driver_id = d.id
                      AND d.name = :name
                      AND rr.session_id = :sid
                """), {"gp": grid_pos, "name": name, "sid": s["session_id"]})
                updated += result.rowcount
            conn.commit()
            print(f"  [{i+1}/{len(sessions)}] {year} R{rnd}: updated {updated} rows")
        except Exception as e:
            print(f"  [{i+1}/{len(sessions)}] {year} R{rnd}: FAILED ({e})")
        time.sleep(0.3)

print("Done.")
