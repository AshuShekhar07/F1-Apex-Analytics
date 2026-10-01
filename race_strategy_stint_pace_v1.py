"""Leakage-safe direct stint/lap pace model for the race strategy simulator.

This is intentionally not a standalone "tyre degradation coefficient". It predicts
within-race lap-pace adjustments from compound, tyre age and race progression, while
the existing historical pace model supplies the driver's baseline absolute pace.

Training is strictly chronological by race date/era. Production simulator integration
is deliberately deferred until this held-out validation clears the acceptance gate.
"""
from __future__ import annotations

import argparse
import csv
import os
from collections import defaultdict
from dataclasses import dataclass
from math import isfinite, sqrt
from pathlib import Path
from statistics import median
from typing import Iterable

import numpy as np
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from race_strategy_pace_calibration_v3 import build_residual_observations, predict_target_pace
from race_strategy_pace_v3_db_adapter import load_pace_observations


COMPOUNDS = ("SOFT", "MEDIUM", "HARD")


@dataclass(frozen=True)
class StintPaceRow:
    race_id: int
    race_date: str
    track_id: int
    season_year: int
    era: str
    driver_id: int
    team_id: int
    compound: str
    lap_number: int
    total_laps: int
    tyre_age: int
    lap_time: float
    driver_race_median: float


@dataclass(frozen=True)
class PaceModel:
    era: str
    coefficients: tuple[float, ...]
    ridge_alpha: float
    training_rows: int
    training_races: int


@dataclass(frozen=True)
class RaceScore:
    race_id: int
    year: int
    era: str
    drivers_scored: int
    baseline_mae: float
    model_mae: float
    improvement_pct: float


def _features(compound: str, tyre_age: int, lap_number: int, total_laps: int) -> list[float]:
    progress = max(0.0, min(1.0, lap_number / max(1, total_laps)))
    age = max(0.0, min(float(tyre_age), 35.0))
    age_scaled = age / 10.0
    return [
        1.0,
        float(compound == "SOFT"),
        float(compound == "HARD"),
        age_scaled,
        age_scaled * age_scaled,
        float(compound == "SOFT") * age_scaled,
        float(compound == "HARD") * age_scaled,
        progress - 0.5,
        (progress - 0.5) ** 2,
        age_scaled * (progress - 0.5),
    ]


def _fit_ridge(rows: Iterable[StintPaceRow], *, era: str, ridge_alpha: float = 4.0) -> PaceModel:
    grouped: dict[tuple[int, int], list[StintPaceRow]] = defaultdict(list)
    for row in rows:
        grouped[(row.race_id, row.driver_id)].append(row)

    x_rows: list[list[float]] = []
    y_rows: list[float] = []
    weights: list[float] = []

    for group_rows in grouped.values():
        if len(group_rows) < 5:
            continue
        baseline = median(row.lap_time for row in group_rows)
        weight = 1.0 / len(group_rows)
        for row in group_rows:
            x_rows.append(_features(row.compound, row.tyre_age, row.lap_number, row.total_laps))
            y_rows.append(row.lap_time - baseline)
            weights.append(weight)

    if len(x_rows) < 50:
        raise ValueError(f"Insufficient training rows for era={era}: n={len(x_rows)}")

    x = np.asarray(x_rows, dtype=float)
    y = np.asarray(y_rows, dtype=float)
    w = np.sqrt(np.asarray(weights, dtype=float))
    xw = x * w[:, None]
    yw = y * w

    reg = np.eye(x.shape[1], dtype=float) * float(ridge_alpha)
    reg[0, 0] = 0.0
    beta = np.linalg.solve(xw.T @ xw + reg, xw.T @ yw)

    race_count = len({row.race_id for row in rows})
    return PaceModel(
        era=era,
        coefficients=tuple(float(v) for v in beta),
        ridge_alpha=float(ridge_alpha),
        training_rows=len(x_rows),
        training_races=race_count,
    )


def _predict_adjustment(model: PaceModel, row: StintPaceRow) -> float:
    return float(np.dot(np.asarray(_features(row.compound, row.tyre_age, row.lap_number, row.total_laps)), np.asarray(model.coefficients)))


def _query_rows(db, start_year: int, end_year: int) -> list[dict]:
    result = db.execute(
        text(
            """
            SELECT
                r.id AS race_id,
                r.race_date,
                r.track_id,
                r.season_year,
                r.regulation_era AS era,
                re.driver_id,
                re.team_id,
                rs.compound,
                rs.start_lap,
                rs.end_lap,
                l.lap_number,
                l.lap_time,
                COALESCE(t.total_race_laps, MAX(l.lap_number) OVER (PARTITION BY r.id)) AS total_laps
            FROM laps l
            JOIN sessions s ON s.id = l.session_id AND s.session_type = 'R'
            JOIN races r ON r.id = s.race_id
            JOIN race_entries re ON re.id = l.race_entry_id
            JOIN race_stints rs
              ON rs.race_id = r.id
             AND rs.race_entry_id = l.race_entry_id
             AND l.lap_number BETWEEN rs.start_lap AND rs.end_lap
            JOIN tracks t ON t.id = r.track_id
            JOIN session_weather sw ON sw.session_id = s.id
            WHERE r.race_date IS NOT NULL
              AND r.season_year BETWEEN :start_year AND :end_year
              AND r.regulation_era IS NOT NULL
              AND sw.rainfall = FALSE
              AND l.lap_time IS NOT NULL
              AND l.is_valid = TRUE
              AND l.lap_time BETWEEN 40.0 AND 180.0
              AND rs.compound IN ('SOFT', 'MEDIUM', 'HARD')
            ORDER BY r.race_date, r.id, re.driver_id, l.lap_number
            """
        ),
        {"start_year": start_year, "end_year": end_year},
    ).mappings().all()
    return [dict(row) for row in result]


def build_rows(raw_rows: list[dict], *, min_tyre_age: int = 2) -> tuple[StintPaceRow, ...]:
    grouped: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for row in raw_rows:
        start_lap = int(row["start_lap"])
        end_lap = int(row["end_lap"])
        lap = int(row["lap_number"])
        if lap <= start_lap or lap >= end_lap:
            continue
        age = lap - start_lap
        if age < min_tyre_age:
            continue
        grouped[(int(row["race_id"]), int(row["driver_id"]))].append(row)

    out: list[StintPaceRow] = []
    for rows in grouped.values():
        median_lap = median(float(row["lap_time"]) for row in rows)
        for row in rows:
            out.append(
                StintPaceRow(
                    race_id=int(row["race_id"]),
                    race_date=str(row["race_date"]),
                    track_id=int(row["track_id"]),
                    season_year=int(row["season_year"]),
                    era=str(row["era"]),
                    driver_id=int(row["driver_id"]),
                    team_id=int(row["team_id"]),
                    compound=str(row["compound"]).upper(),
                    lap_number=int(row["lap_number"]),
                    total_laps=int(row["total_laps"]),
                    tyre_age=int(row["lap_number"]) - int(row["start_lap"]),
                    lap_time=float(row["lap_time"]),
                    driver_race_median=float(median_lap),
                )
            )
    return tuple(out)


def _base_pace_predictions(
    pace_rows,
    *,
    target_year: int,
    target_era: str,
    target_track_id: int,
    driver_team: dict[int, int],
) -> dict[int, float]:
    residuals = build_residual_observations(
        row for row in pace_rows if row.season_year < target_year and row.regulation_era == target_era
    )
    predictions: dict[int, float] = {}
    for driver_id, team_id in driver_team.items():
        try:
            pred = predict_target_pace(
                residuals,
                target_track_id=target_track_id,
                target_era=target_era,
                target_driver_key=str(driver_id),
                target_team_key=str(team_id),
                as_of_year=target_year,
                min_track_races=1,
                min_team_races=1,
                min_driver_races=1,
            )
        except ValueError:
            continue
        predictions[driver_id] = pred.mean_seconds
    return predictions


def validate_walk_forward(
    rows: tuple[StintPaceRow, ...],
    pace_rows,
    *,
    min_training_races: int = 15,
    ridge_alpha: float = 4.0,
) -> list[RaceScore]:
    races = sorted(
        {(
            row.race_id,
            row.race_date,
            row.season_year,
            row.era,
            row.track_id,
            row.total_laps,
        ) for row in rows},
        key=lambda value: (value[1], value[0]),
    )
    by_race: dict[int, list[StintPaceRow]] = defaultdict(list)
    for row in rows:
        by_race[row.race_id].append(row)

    results: list[RaceScore] = []
    for target_race_id, target_date, target_year, target_era, target_track, _ in races:
        prior_races = [
            race_id
            for race_id, race_date, _, era, _, _ in races
            if era == target_era and (race_date, race_id) < (target_date, target_race_id)
        ]
        if len(prior_races) < min_training_races:
            continue

        train_rows = tuple(row for race_id in prior_races for row in by_race[race_id])
        model = _fit_ridge(train_rows, era=target_era, ridge_alpha=ridge_alpha)

        target_rows = by_race[target_race_id]
        driver_team: dict[int, int] = {}
        for row in target_rows:
            driver_team[row.driver_id] = row.team_id
        base_predictions = _base_pace_predictions(
            pace_rows,
            target_year=target_year,
            target_era=target_era,
            target_track_id=target_track,
            driver_team=driver_team,
        )

        baseline_errors: list[float] = []
        model_errors: list[float] = []
        for row in target_rows:
            base = base_predictions.get(row.driver_id)
            if base is None:
                continue
            baseline_errors.append(abs(row.lap_time - base))
            predicted = base + _predict_adjustment(model, row)
            model_errors.append(abs(row.lap_time - predicted))

        if not baseline_errors:
            continue
        baseline_mae = sum(baseline_errors) / len(baseline_errors)
        model_mae = sum(model_errors) / len(model_errors)
        results.append(
            RaceScore(
                race_id=target_race_id,
                year=target_year,
                era=target_era,
                drivers_scored=len({row.driver_id for row in target_rows if row.driver_id in base_predictions}),
                baseline_mae=baseline_mae,
                model_mae=model_mae,
                improvement_pct=100.0 * (baseline_mae - model_mae) / baseline_mae,
            )
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Leakage-safe direct stint pace walk-forward")
    parser.add_argument("--start-year", type=int, default=2018)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument("--min-training-races", type=int, default=15)
    parser.add_argument("--min-tyre-age", type=int, default=2)
    parser.add_argument("--ridge-alpha", type=float, default=4.0)
    parser.add_argument("--csv", default="stint_pace_walkforward_v1.csv")
    args = parser.parse_args()

    load_dotenv(dotenv_path=Path.cwd() / ".env")
    url = os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is not set")

    db = create_engine(url).connect()
    try:
        raw = _query_rows(db, args.start_year, args.end_year)
        rows = build_rows(raw, min_tyre_age=args.min_tyre_age)
        pace_rows, warnings = load_pace_observations(
            db, start_year=args.start_year, end_year=args.end_year - 1
        )
    finally:
        db.close()

    for warning in warnings:
        print("WARNING:", warning)

    results = validate_walk_forward(
        rows,
        pace_rows,
        min_training_races=args.min_training_races,
        ridge_alpha=args.ridge_alpha,
    )

    with open(args.csv, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "race_id", "year", "era", "drivers_scored",
                "baseline_mae", "model_mae", "improvement_pct",
            ],
        )
        writer.writeheader()
        writer.writerows(
            {
                "race_id": r.race_id,
                "year": r.year,
                "era": r.era,
                "drivers_scored": r.drivers_scored,
                "baseline_mae": r.baseline_mae,
                "model_mae": r.model_mae,
                "improvement_pct": r.improvement_pct,
            }
            for r in results
        )

    if not results:
        print("No target races were scoreable.")
        return 0

    mean_base = sum(r.baseline_mae for r in results) / len(results)
    mean_model = sum(r.model_mae for r in results) / len(results)
    improvements = [r.improvement_pct for r in results]
    print("=== LEAKAGE-SAFE DIRECT STINT PACE WALK-FORWARD ===")
    print(f"target_races={len(results)}")
    print(f"mean_baseline_mae={mean_base:.6f}")
    print(f"mean_model_mae={mean_model:.6f}")
    print(f"mean_improvement_pct={sum(improvements)/len(improvements):.3f}")
    print(f"positive_race_rate={sum(v > 0 for v in improvements)/len(improvements):.3f}")
    print(f"median_improvement_pct={median(improvements):.3f}")
    print(f"total_driver_race_coverage={sum(r.drivers_scored for r in results)}")
    print(f"Wrote {args.csv}")
    print("Production simulator integration is intentionally disabled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
