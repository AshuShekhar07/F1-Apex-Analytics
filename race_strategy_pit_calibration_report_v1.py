"""CLI inspection report for calibrated total pit-lane loss."""

from __future__ import annotations

import argparse
import os

from dotenv import load_dotenv
from sqlalchemy import create_engine

from race_strategy_pit_calibration_v1 import calibrate_total_pit_lane_from_db


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect FastF1 total pit-lane calibration")
    parser.add_argument("--era", default=None)
    parser.add_argument("--start-year", type=int, default=2018)
    parser.add_argument("--end-year", type=int, default=2026)
    args = parser.parse_args()

    if args.start_year > args.end_year:
        raise SystemExit("--start-year cannot be greater than --end-year")
    load_dotenv()
    url = os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is not set")

    engine = create_engine(url)
    with engine.connect() as db:
        result = calibrate_total_pit_lane_from_db(
            db,
            era=args.era,
            start_year=args.start_year,
            end_year=args.end_year,
        )

    print("Race Pit-Lane Calibration v1")
    print("============================")
    print(f"era          : {args.era or 'all'}")
    print(f"observations : {result.observations}")
    print(f"mean seconds : {result.total_pit_lane_seconds.mean:.3f}")
    print(f"std seconds  : {result.total_pit_lane_seconds.std:.3f}")
    print(f"bounds       : {result.total_pit_lane_seconds.lower:.1f}..{result.total_pit_lane_seconds.upper:.1f}")
    for warning in result.warnings:
        print(f"warning      : {warning}")


if __name__ == "__main__":
    main()
