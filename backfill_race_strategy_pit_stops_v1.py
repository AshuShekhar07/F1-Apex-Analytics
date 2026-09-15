"""Backfill reconstructed FastF1 pit-lane observations into the strategy DB.

Usage:
    python backfill_race_strategy_pit_stops_v1.py --start-year 2018 --end-year 2026

This script is intentionally write-scoped to the dedicated
``race_strategy_pit_stops`` table. It does not modify races, laps, results, or
existing strategy data. Apply ``migration_race_strategy_pit_stops_v1.sql``
first.
"""

from __future__ import annotations

import argparse
import os

from sqlalchemy import create_engine, text
from dotenv import load_dotenv

from race_strategy_pit_ingestion_v1 import (
    PitIngestionConfig,
    extract_pit_stops_from_fastf1_session,
    filter_pit_stop_outliers,
    fastf1_session_loader,
)

CACHE_DIR = "/home/ashushekhar07/projects/F1-Apex-Analytics/cache"


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill FastF1 pit-stop observations")
    parser.add_argument("--start-year", type=int, default=2018)
    parser.add_argument("--end-year", type=int, default=2026)
    parser.add_argument("--cache-dir", default=CACHE_DIR)
    args = parser.parse_args()

    if args.start_year > args.end_year:
        raise SystemExit("--start-year cannot be greater than --end-year")

    load_dotenv()
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is not set")

    engine = create_engine(database_url)
    config = PitIngestionConfig()

    with engine.begin() as conn:
        races = conn.execute(
            text("""
                SELECT id, season_year, round_number
                FROM races
                WHERE season_year BETWEEN :start_year AND :end_year
                  AND race_date IS NOT NULL
                ORDER BY season_year, round_number
            """),
            {"start_year": args.start_year, "end_year": args.end_year},
        ).mappings().all()

        for race in races:
            year = int(race["season_year"])
            round_number = int(race["round_number"])
            race_id = int(race["id"])
            print(f"{year} R{round_number}: loading FastF1")
            try:
                session = fastf1_session_loader(year, round_number, args.cache_dir)
                raw = extract_pit_stops_from_fastf1_session(session)
                stops, removed = filter_pit_stop_outliers(raw, config=config)
            except Exception as exc:
                print(f"  unavailable: {exc}")
                continue

            inserted = 0
            for stop in stops:
                entry_id = conn.execute(
                    text("""
                        SELECT id
                        FROM race_entries
                        WHERE race_id = :race_id AND car_number = :car_number
                        ORDER BY id
                        LIMIT 1
                    """),
                    {
                        "race_id": race_id,
                        "car_number": int(stop.driver) if stop.driver.isdigit() else -1,
                    },
                ).scalar()

                if entry_id is None:
                    # FastF1 Driver is normally an abbreviation. Fall back to
                    # matching by driver abbreviation/name is schema-specific,
                    # so unresolved stops are skipped rather than misassigned.
                    print(f"  unresolved entry for driver {stop.driver} lap {stop.pit_lap}, skipped")
                    continue

                result = conn.execute(
                    text("""
                        INSERT INTO race_strategy_pit_stops (
                            race_id,
                            race_entry_id,
                            pit_lap,
                            pit_in_time_seconds,
                            pit_out_time_seconds,
                            total_pit_lane_seconds,
                            source
                        ) VALUES (
                            :race_id,
                            :entry_id,
                            :pit_lap,
                            :pit_in,
                            :pit_out,
                            :total,
                            'fastf1'
                        )
                        ON CONFLICT (race_id, race_entry_id, pit_lap, source) DO NOTHING
                    """),
                    {
                        "race_id": race_id,
                        "entry_id": int(entry_id),
                        "pit_lap": stop.pit_lap,
                        "pit_in": stop.pit_in_time_seconds,
                        "pit_out": stop.pit_out_time_seconds,
                        "total": stop.total_pit_lane_seconds,
                    },
                )
                inserted += int(result.rowcount or 0)

            print(f"  reconstructed={len(raw)} kept={len(stops)} removed={removed} inserted={inserted}")


if __name__ == "__main__":
    main()
