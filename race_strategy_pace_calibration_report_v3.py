"""Inspect hierarchical race-pace calibration v3 against the project DB."""
from __future__ import annotations

import argparse
import os

from dotenv import load_dotenv
from sqlalchemy import create_engine

from race_strategy_pace_v3_db_adapter import load_pace_observations
from race_strategy_pace_calibration_v3 import build_residual_observations, walk_forward_validate


def main() -> None:
    parser = argparse.ArgumentParser(description='Race pace calibration v3 report')
    parser.add_argument('--era', default='era2_18inch_groundeffect')
    parser.add_argument('--start-year', type=int, default=2018)
    parser.add_argument('--end-year', type=int, default=2026)
    args = parser.parse_args()
    if args.start_year > args.end_year:
        raise SystemExit('--start-year cannot be greater than --end-year')

    load_dotenv()
    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        raise SystemExit('DATABASE_URL is not set')

    engine = create_engine(database_url)
    with engine.connect() as db:
        observations, warnings = load_pace_observations(db, era=args.era, start_year=args.start_year, end_year=args.end_year)

    residuals = build_residual_observations(observations)
    metrics = walk_forward_validate(residuals, min_train_races=3)

    print('Race Pace Calibration v3')
    print('========================')
    print(f'era          : {args.era}')
    print(f'lap rows     : {len(observations)}')
    print(f'driver-races : {len(residuals)}')
    print(f'years        : {args.start_year}-{args.end_year}')
    print(f'W-F scored   : {metrics["predictions_scored"]}')
    print(f'W-F coverage : {metrics["coverage_rate"]:.3f}')
    print(f'W-F MAE      : {metrics["mae_seconds"]:.3f} s')
    for warning in warnings:
        print(f'warning      : {warning}')
    print('leakage      : training restricted to years before each held-out year')
    print('target       : absolute clean-race lap pace, not finishing position')


if __name__ == '__main__':
    main()
