import os
import time

import fastf1
import pandas as pd
from sqlalchemy import create_engine, text

engine = create_engine(os.environ["DATABASE_URL"])

THROTTLE_SECONDS = 0.5


def to_seconds(td):
    if pd.isna(td):
        return None
    return td.total_seconds()


with engine.connect() as conn:
    races = conn.execute(text("""
        SELECT
            r.id AS race_id,
            r.season_year,
            r.round_number,
            s.id AS session_id
        FROM races r
        JOIN sessions s
          ON s.race_id = r.id
         AND s.session_type = 'R'
        WHERE r.season_year BETWEEN 2018 AND 2026
          AND r.race_date <= CURRENT_DATE
          AND (
                EXISTS (
                    SELECT 1
                    FROM laps l
                    WHERE l.session_id = s.id
                      AND l.position IS NULL
                )
                OR NOT EXISTS (
                    SELECT 1
                    FROM laps l
                    WHERE l.session_id = s.id
                )
          )
        ORDER BY r.season_year, r.round_number
    """)).mappings().all()


total = len(races)
total_updated = 0
total_inserted = 0
total_failed = 0

print(f"Pending races: {total}", flush=True)

for i, race in enumerate(races, start=1):
    year = race["season_year"]
    round_num = race["round_number"]
    session_id = race["session_id"]
    race_id = race["race_id"]

    print(f"[{i}/{total}] {year} R{round_num}: START", flush=True)

    try:
        session = fastf1.get_session(year, round_num, "R")
        session.load(
            telemetry=False,
            weather=False,
            messages=False,
        )
    except Exception as e:
        msg = str(e)

        if "rate" in msg.lower() and "limit" in msg.lower():
            print(
                f"[{i}/{total}] {year} R{round_num}: RATE LIMIT - STOPPING",
                flush=True,
            )
            break

        print(
            f"[{i}/{total}] {year} R{round_num}: FAILED FastF1 - {e}",
            flush=True,
        )
        total_failed += 1
        time.sleep(THROTTLE_SECONDS)
        continue

    try:
        with engine.begin() as conn:
            driver_to_entry = {}

            for _, result in session.results.iterrows():
                if pd.isna(result.get("DriverNumber")):
                    continue

                car_number = int(result["DriverNumber"])

                entry_id = conn.execute(
                    text("""
                        SELECT id
                        FROM race_entries
                        WHERE race_id = :race_id
                          AND car_number = :car_number
                          AND role = 'race_driver'
                    """),
                    {
                        "race_id": race_id,
                        "car_number": car_number,
                    },
                ).scalar()

                if entry_id is not None:
                    driver_to_entry[result["Abbreviation"]] = entry_id

            updated = 0
            inserted = 0

            for _, lap in session.laps.iterrows():
                driver = lap["Driver"]
                lap_number = lap["LapNumber"]
                position = lap["Position"]

                if pd.isna(lap_number) or pd.isna(position):
                    continue

                entry_id = driver_to_entry.get(driver)

                if entry_id is None:
                    continue

                lap_number = int(lap_number)
                position = int(position)

                existing_id = conn.execute(
                    text("""
                        SELECT id, position
                        FROM laps
                        WHERE session_id = :session_id
                          AND race_entry_id = :entry_id
                          AND lap_number = :lap_number
                    """),
                    {
                        "session_id": session_id,
                        "entry_id": entry_id,
                        "lap_number": lap_number,
                    },
                ).first()

                if existing_id is not None:
                    if existing_id.position is None:
                        conn.execute(
                            text("""
                                UPDATE laps
                                SET position = :position
                                WHERE id = :id
                                  AND position IS NULL
                            """),
                            {
                                "position": position,
                                "id": existing_id.id,
                            },
                        )
                        updated += 1
                else:
                    conn.execute(
                        text("""
                            INSERT INTO laps (
                                session_id,
                                race_entry_id,
                                lap_number,
                                lap_time,
                                sector_1_time,
                                sector_2_time,
                                sector_3_time,
                                tire_compound,
                                position
                            )
                            VALUES (
                                :session_id,
                                :entry_id,
                                :lap_number,
                                :lap_time,
                                :s1,
                                :s2,
                                :s3,
                                :compound,
                                :position
                            )
                        """),
                        {
                            "session_id": session_id,
                            "entry_id": entry_id,
                            "lap_number": lap_number,
                            "lap_time": to_seconds(lap["LapTime"]),
                            "s1": to_seconds(lap["Sector1Time"]),
                            "s2": to_seconds(lap["Sector2Time"]),
                            "s3": to_seconds(lap["Sector3Time"]),
                            "compound": lap.get("Compound"),
                            "position": position,
                        },
                    )
                    inserted += 1

        total_updated += updated
        total_inserted += inserted

        print(
            f"[{i}/{total}] {year} R{round_num}: "
            f"OK updated={updated} inserted={inserted}",
            flush=True,
        )

    except Exception as e:
        msg = str(e)

        if "rate" in msg.lower() and "limit" in msg.lower():
            print(
                f"[{i}/{total}] {year} R{round_num}: "
                f"RATE LIMIT DB/processing - STOPPING",
                flush=True,
            )
            break

        total_failed += 1
        print(
            f"[{i}/{total}] {year} R{round_num}: DB ERROR - {e}",
            flush=True,
        )

    time.sleep(THROTTLE_SECONDS)


print("\n==============================")
print(f"TOTAL UPDATED:  {total_updated}")
print(f"TOTAL INSERTED: {total_inserted}")
print(f"TOTAL FAILED:   {total_failed}")
print("==============================")
