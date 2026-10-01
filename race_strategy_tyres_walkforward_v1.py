"""Leakage-safe walk-forward validation of tyre-degradation calibration.

Training for each target year uses only earlier races in the same regulation era.
Target stints are never used to fit degradation. The target stint's own first
two representative laps are used only to construct post-hoc observed deltas for
scoring, matching the calibration definition.

This script validates the degradation input; it does not modify the simulator.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from statistics import mean, median
from typing import Iterable

from dotenv import load_dotenv
from sqlalchemy import create_engine

from race_strategy_calibration_v1 import TyreCalibrationObservation, calibrate_tyre_degradation
from race_strategy_data_adapter_v1 import load_tyre_observations


def _stint_rows(rows: Iterable[TyreCalibrationObservation]) -> dict[str, list[TyreCalibrationObservation]]:
    grouped: dict[str, list[TyreCalibrationObservation]] = {}
    for row in rows:
        grouped.setdefault(row.stint_key, []).append(row)
    return grouped


def _observed_slope(rows: list[TyreCalibrationObservation]) -> float | None:
    if len(rows) < 3:
        return None
    ordered = sorted(rows, key=lambda r: r.tyre_age_laps)
    xs = [float(r.tyre_age_laps) for r in ordered]
    ys = [float(r.lap_time_delta_seconds) for r in ordered]
    x_bar = mean(xs)
    y_bar = mean(ys)
    denom = sum((x - x_bar) ** 2 for x in xs)
    if denom <= 1e-12:
        return None
    return sum((x - x_bar) * (y - y_bar) for x, y in zip(xs, ys)) / denom


def validate_target_year(
    observations: tuple[TyreCalibrationObservation, ...],
    *,
    target_year: int,
    era: str,
) -> dict[str, float | int]:
    training = [
        r for r in observations
        if r.season_year is not None
        and r.regulation_era is not None
        and r.season_year < target_year
        and r.regulation_era == era
    ]
    target = [
        r for r in observations
        if r.season_year is not None
        and r.regulation_era is not None
        and r.season_year == target_year
        and r.regulation_era == era
    ]

    if not training or not target:
        return {
            "target_year": target_year,
            "training_observations": len(training),
            "target_observations": len(target),
            "scored_stints": 0,
            "slope_mae_seconds_per_lap": float("nan"),
            "slope_baseline_mae_seconds_per_lap": float("nan"),
            "slope_improvement_pct": float("nan"),
            "prediction_mae_seconds": float("nan"),
            "baseline_mae_seconds": float("nan"),
            "prediction_mae_improvement_pct": float("nan"),
        }

    calibrated, warnings = calibrate_tyre_degradation(training)
    target_groups = _stint_rows(target)

    slope_errors: list[float] = []
    slope_baseline_errors: list[float] = []
    prediction_errors: list[float] = []
    baseline_errors: list[float] = []
    scored_stints = 0

    for stint_key, rows in target_groups.items():
        compound = rows[0].compound.upper()
        dist = calibrated.get(compound)
        if dist is None:
            continue
        observed_slope = _observed_slope(rows)
        if observed_slope is None:
            continue

        scored_stints += 1
        slope_errors.append(abs(observed_slope - dist.mean))
        slope_baseline_errors.append(abs(observed_slope))

        for row in rows:
            predicted_delta = dist.mean * row.tyre_age_laps
            actual_delta = row.lap_time_delta_seconds
            prediction_errors.append(abs(actual_delta - predicted_delta))
            baseline_errors.append(abs(actual_delta))

    return {
        "target_year": target_year,
        "training_observations": len(training),
        "target_observations": len(target),
        "scored_stints": scored_stints,
        "slope_mae_seconds_per_lap": mean(slope_errors) if slope_errors else float("nan"),
        "slope_baseline_mae_seconds_per_lap": (
            mean(slope_baseline_errors) if slope_baseline_errors else float("nan")
        ),
        "slope_improvement_pct": (
            100.0 * (mean(slope_baseline_errors) - mean(slope_errors)) / mean(slope_baseline_errors)
            if slope_baseline_errors and mean(slope_baseline_errors) > 0 else float("nan")
        ),
        "prediction_mae_seconds": mean(prediction_errors) if prediction_errors else float("nan"),
        "baseline_mae_seconds": mean(baseline_errors) if baseline_errors else float("nan"),
        "prediction_mae_improvement_pct": (
            100.0 * (mean(baseline_errors) - mean(prediction_errors)) / mean(baseline_errors)
            if baseline_errors and mean(baseline_errors) > 0 else float("nan")
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate tyre degradation walk-forward")
    parser.add_argument("--start-target-year", type=int, default=2019)
    parser.add_argument("--end-target-year", type=int, default=2025)
    parser.add_argument("--min-stint-laps", type=int, default=5)
    args = parser.parse_args()

    load_dotenv(dotenv_path=Path.cwd() / ".env")
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")

    engine = create_engine(database_url)
    with engine.connect() as db:
        rows, warnings = load_tyre_observations(
            db,
            start_year=2018,
            end_year=args.end_target_year,
            min_stint_laps=args.min_stint_laps,
        )
        for warning in warnings:
            print("WARNING:", warning)

        eras = sorted({r.regulation_era for r in rows})
        print("=== LEAKAGE-SAFE TYRE DEGRADATION WALK-FORWARD ===")
        print("target_year regulation_era training_obs target_obs scored_stints slope_MAE_s_per_lap slope_baseline_MAE_s_per_lap slope_improvement_pct baseline_MAE_s prediction_MAE_s prediction_improvement_pct")

        for era in eras:
            for year in range(args.start_target_year, args.end_target_year + 1):
                result = validate_target_year(rows, target_year=year, era=era)
                print(
                    f"{year:4d} {era:28s} "
                    f"{result['training_observations']:12d} {result['target_observations']:10d} "
                    f"{result['scored_stints']:13d} "
                    f"{result['slope_mae_seconds_per_lap']:16.5f} "
                    f"{result['slope_baseline_mae_seconds_per_lap']:23.5f} "
                    f"{result['slope_improvement_pct']:21.2f} "
                    f"{result['baseline_mae_seconds']:13.5f} "
                    f"{result['prediction_mae_seconds']:14.5f} "
                    f"{result['prediction_mae_improvement_pct']:24.2f}"
                )

        print("\nNo simulator integration is performed by this validation.")


if __name__ == "__main__":
    main()
