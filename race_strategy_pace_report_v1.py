"""Inspect real DB race-pace normalization coverage.

Usage:
    python race_strategy_pace_report_v1.py --era era2_18inch_groundeffect
"""

from __future__ import annotations

import argparse

from race_strategy_pace_db_adapter_v1 import load_race_pace_observations
from race_strategy_pace_normalization_v1 import normalize_race_pace


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect race-pace normalization coverage")
    parser.add_argument("--era", default=None)
    parser.add_argument("--start-year", type=int, default=2018)
    parser.add_argument("--end-year", type=int, default=2026)
    args = parser.parse_args()

    from app.database import SessionLocal

    db = SessionLocal()
    try:
        raw, warnings = load_race_pace_observations(
            db,
            era=args.era,
            start_year=args.start_year,
            end_year=args.end_year,
        )
    finally:
        db.close()

    normalized = normalize_race_pace(raw)
    race_count = len({row.race_id for row in normalized})
    driver_race_count = len(normalized)

    print("Race Pace Normalization v1 — DB Inspection")
    print("=" * 48)
    print()
    print("Observation coverage")
    print(f"  clean race laps : {len(raw)}")
    print(f"  races           : {race_count}")
    print(f"  driver-races    : {driver_race_count}")
    if args.era:
        print(f"  era filter      : {args.era}")
    print()
    print("Normalization")
    print("  reference       : competitive-lap median per race")
    print("  target signal   : driver median minus same-race reference")
    print("  leakage status  : no finishing outcome used")
    if warnings:
        print()
        print("Warnings")
        for warning in warnings:
            print(f"  - {warning}")


if __name__ == "__main__":
    main()
