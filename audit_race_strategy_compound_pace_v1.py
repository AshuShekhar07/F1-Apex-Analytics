"""Audit historical compound pace offsets with chronological, pre-year calibration.

Research-only: this script does not modify the simulator. For each target year it
fits compound offsets using only observations from earlier years in the same
regulation era, then reports the frozen coefficients. This is a calibration
stability audit, not a predictive validation score.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine

from race_strategy_compound_calibration_v1 import calibrate_compound_pace
from race_strategy_compound_db_adapter_v1 import load_compound_pace_observations


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit leakage-safe compound pace calibration")
    parser.add_argument("--start-year", type=int, default=2019)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument("--min-age", type=int, default=1)
    parser.add_argument("--max-age", type=int, default=3)
    parser.add_argument("--min-groups", type=int, default=50)
    parser.add_argument("--max-lap-distance", type=int, default=3)
    args = parser.parse_args()

    load_dotenv(dotenv_path=Path.cwd() / ".env")
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")

    engine = create_engine(database_url)
    with engine.connect() as db:
        observations, warnings = load_compound_pace_observations(
            db,
            start_year=2018,
            end_year=args.end_year - 1,
            min_age=args.min_age,
            max_age=args.max_age,
        )
        if warnings:
            print("Loader warnings:")
            for warning in warnings:
                print(f"  - {warning}")

        years = range(args.start_year, args.end_year + 1)
        print("=== LEAKAGE-SAFE COMPOUND PACE CALIBRATION AUDIT ===")
        print("target_year regulation_era training_observations training_groups soft_delta_ms medium_delta_ms hard_delta_ms")

        eras = sorted({r.regulation_era for r in observations})
        for era in eras:
            for year in years:
                train = [r for r in observations if r.regulation_era == era and r.season_year < year]
                try:
                    result = calibrate_compound_pace(train, max_lap_distance=args.max_lap_distance)
                except ValueError as exc:
                    print(f"{year:4d} {era:28s} ERROR {exc}")
                    continue
                if result.groups < args.min_groups:
                    print(f"{year:4d} {era:28s} {result.observations:20d} {result.groups:13d} insufficient_groups")
                    continue
                print(
                    f"{year:4d} {era:28s} {result.observations:20d} {result.groups:13d} "
                    f"{result.offsets_seconds['SOFT']*1000:13.3f} {result.offsets_seconds['MEDIUM']*1000:14.3f} "
                    f"{result.offsets_seconds['HARD']*1000:12.3f}"
                )

        print("\nFull-history era estimates:")
        for era in eras:
            rows = [r for r in observations if r.regulation_era == era]
            try:
                result = calibrate_compound_pace(rows, max_lap_distance=args.max_lap_distance)
            except ValueError as exc:
                print(f"  {era}: ERROR {exc}")
                continue
            print(
                f"  {era}: n={result.observations}, groups={result.groups}, "
                f"SOFT={result.offsets_seconds['SOFT']*1000:.3f}ms, "
                f"MEDIUM=0.000ms, HARD={result.offsets_seconds['HARD']*1000:.3f}ms"
            )

        print("\nNo simulator integration is performed by this audit.")


if __name__ == "__main__":
    main()
