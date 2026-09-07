import os
import time

import fastf1
from sqlalchemy import create_engine, text

engine = create_engine(os.environ["DATABASE_URL"])

THROTTLE_SECONDS = 0.5

with engine.connect() as conn:
    races = conn.execute(text("""
        SELECT
            r.id AS race_id,
            r.season_year,
            r.round_number
        FROM races r
        WHERE r.winner_time_seconds IS NULL
          AND r.race_date <= CURRENT_DATE
        ORDER BY r.season_year, r.round_number
    """)).mappings().all()

total = len(races)
print(f"Pending winner-time races: {total}", flush=True)

for i, race in enumerate(races, start=1):
    year = race["season_year"]
    rnd = race["round_number"]

    print(f"[{i}/{total}] {year} R{rnd}: START", flush=True)

    try:
        session = fastf1.get_session(year, rnd, "R")
        session.load(
            laps=False,
            telemetry=False,
            weather=False,
            messages=False,
        )

        winners = session.results[
            session.results["Position"] == 1
        ]

        if winners.empty:
            print(
                f"[{i}/{total}] {year} R{rnd}: FAILED - no winner found",
                flush=True,
            )
            continue

        winner_time = winners.iloc[0]["Time"]

        if winner_time is None or str(winner_time) == "NaT":
            print(
                f"[{i}/{total}] {year} R{rnd}: "
                f"FAILED - winner time unavailable",
                flush=True,
            )
            continue

        seconds = round(winner_time.total_seconds(), 3)

        with engine.begin() as conn:
            updated = conn.execute(
                text("""
                    UPDATE races
                    SET winner_time_seconds = :seconds
                    WHERE id = :race_id
                      AND winner_time_seconds IS NULL
                """),
                {
                    "seconds": seconds,
                    "race_id": race["race_id"],
                },
            ).rowcount

        if updated:
            print(
                f"[{i}/{total}] {year} R{rnd}: "
                f"OK ({seconds:.3f}s)",
                flush=True,
            )
        else:
            print(
                f"[{i}/{total}] {year} R{rnd}: "
                f"SKIPPED - already populated",
                flush=True,
            )

    except Exception as e:
        msg = str(e).lower()

        if "rate" in msg and "limit" in msg:
            print(
                f"[{i}/{total}] {year} R{rnd}: "
                f"RATE LIMIT - STOPPING",
                flush=True,
            )
            break

        print(
            f"[{i}/{total}] {year} R{rnd}: FAILED - {e}",
            flush=True,
        )

    time.sleep(THROTTLE_SECONDS)

print("Winner-time backfill finished.", flush=True)
