"""Read-only audit for the race-strategy tyre degradation calibration.

This audit intentionally does not change the production calibration.  The
current calibration fits within-stint lap-time slopes, but its regression slope
is constrained to be non-negative.  That can hide a physically backwards raw
signal by turning negative slopes into zero.  This script exposes the raw slope
statistics so the model can be judged before we use them for strategy selection.

Usage:
    python audit_race_strategy_tyre_model_v1.py --start-year 2018 --end-year 2025
"""

from __future__ import annotations

import argparse
import os
from collections import defaultdict
from math import isfinite
from statistics import median

from sqlalchemy import create_engine, text

from race_strategy_data_adapter_v1 import load_tyre_observations


COMPOUNDS = ("SOFT", "MEDIUM", "HARD")


def _raw_ols_slope(rows):
    if len(rows) < 3:
        return None
    x = [float(r.tyre_age_laps) for r in rows]
    y = [float(r.lap_time_delta_seconds) for r in rows]
    mean_x = sum(x) / len(x)
    mean_y = sum(y) / len(y)
    denom = sum((value - mean_x) ** 2 for value in x)
    if denom <= 1e-12:
        return None
    return sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y)) / denom


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = (len(ordered) - 1) * q
    lo = int(index)
    hi = min(lo + 1, len(ordered) - 1)
    frac = index - lo
    return ordered[lo] + (ordered[hi] - ordered[lo]) * frac


def audit_rows(observations):
    grouped = defaultdict(list)
    for obs in observations:
        grouped[obs.stint_key].append(obs)

    slopes = defaultdict(list)
    for rows in grouped.values():
        rows = sorted(rows, key=lambda row: row.tyre_age_laps)
        slope = _raw_ols_slope(rows)
        if slope is not None:
            slopes[rows[0].compound.upper()].append(slope)

    report = {}
    for compound in COMPOUNDS:
        values = slopes[compound]
        negative = [value for value in values if value < 0]
        nonpositive = [value for value in values if value <= 0]
        report[compound] = {
            "stints": len(values),
            "negative_slope_stints": len(negative),
            "negative_slope_rate": (len(negative) / len(values)) if values else None,
            "median_raw_slope": median(values) if values else None,
            "p25_raw_slope": _percentile(values, 0.25) if values else None,
            "p75_raw_slope": _percentile(values, 0.75) if values else None,
            "min_raw_slope": min(values) if values else None,
            "max_raw_slope": max(values) if values else None,
            "nonpositive_slope_stints": len(nonpositive),
        }

    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-year", type=int, default=2018)
    parser.add_argument("--end-year", type=int, default=2025)
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is not set")

    db = create_engine(database_url).connect()
    try:
        observations, warnings = load_tyre_observations(
            db,
            start_year=args.start_year,
            end_year=args.end_year,
        )
    finally:
        db.close()

    print("=== TYRE MODEL AUDIT V1 ===")
    print(f"years={args.start_year}-{args.end_year}")
    print(f"observations={len(observations)}")
    print(f"stints={len({obs.stint_key for obs in observations})}")
    for warning in warnings:
        print(f"WARNING: {warning}")

    report = audit_rows(observations)
    for compound in COMPOUNDS:
        row = report[compound]
        print(f"\n{compound}")
        print(f"  stints={row['stints']}")
        print(f"  negative_slope_stints={row['negative_slope_stints']}")
        print(f"  negative_slope_rate={row['negative_slope_rate']}")
        print(f"  median_raw_slope={row['median_raw_slope']}")
        print(f"  p25_raw_slope={row['p25_raw_slope']}")
        print(f"  p75_raw_slope={row['p75_raw_slope']}")
        print(f"  min_raw_slope={row['min_raw_slope']}")
        print(f"  max_raw_slope={row['max_raw_slope']}")
        print(f"  nonpositive_slope_stints={row['nonpositive_slope_stints']}")

    print("\nInterpretation guardrails:")
    print("- Negative slopes are diagnostic evidence, not automatically a coding bug: fuel burn and driver behaviour remain confounders.")
    print("- A large negative-slope rate or nonpositive median means the current raw lap-time signal is not a trustworthy degradation proxy.")
    print("- Do not promote a new tyre calibration until the raw signal has a defensible explanation and passes walk-forward tests.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
