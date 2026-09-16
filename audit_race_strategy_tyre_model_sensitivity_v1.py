"""Sensitivity sweep for the leakage-safe tyre-model walk-forward audit.

This is audit-only. It loads the source rows once, then reruns the existing
walk-forward fit/score logic across a small grid of modelling thresholds.

The purpose is to test whether the observed v2 improvement over the flat
baseline is robust to reasonable choices of:
  - minimum race strategy diversity
  - minimum stint age span
  - minimum prior same-era training races

No production simulator or calibration code is modified.
"""
from __future__ import annotations

import argparse
import csv
import os
from collections import defaultdict

from sqlalchemy import create_engine

from audit_race_strategy_tyre_model_v2 import COMPOUNDS, LapRow
from audit_race_strategy_tyre_model_walkforward_v2 import (
    _low_confidence,
    _overall_race_scores,
    _race_diversity,
    fit_models,
    load_meta,
    load_rows,
    score_target,
)

DEFAULT_MIN_UNIQUE_PIT_LAPS_GRID = (3, 4, 5)
DEFAULT_MIN_STINT_AGE_SPAN_GRID = (3, 4, 5, 6)
DEFAULT_MIN_TRAINING_RACES_GRID = (10, 15, 20)


def _score_config(
    rows: list[LapRow],
    metas,
    min_unique_pit_laps: int,
    min_stint_age_span: int,
    min_training_races: int,
):
    diversity = _race_diversity(rows)
    by_race: dict[int, list[LapRow]] = defaultdict(list)
    for row in rows:
        by_race[row.race_id].append(row)

    summary: list[dict] = []
    stability: list[dict] = []

    for target in metas:
        training = [
            m
            for m in metas
            if m.era == target.era and m.race_date < target.race_date
        ]
        prior_same_era_races = len(training)
        if prior_same_era_races < min_training_races:
            continue

        train_ids = {
            m.race_id
            for m in training
            if diversity.get(m.race_id, 0) >= min_unique_pit_laps
        }
        if not train_ids:
            continue

        train_rows = [
            r for r in rows if r.race_id in train_ids and r.era == target.era
        ]
        target_rows = by_race.get(target.race_id, [])

        for compound in COMPOUNDS:
            v2, raw = fit_models(
                train_rows,
                train_ids,
                compound,
                min_stint_age_span,
            )
            if v2.slope is None:
                continue

            compound_usable_training_races = v2.n_races
            low_confidence = _low_confidence(compound_usable_training_races)
            stability.append(
                {
                    "target_race_id": target.race_id,
                    "target_year": target.season_year,
                    "era": target.era,
                    "compound": compound,
                    "prior_same_era_races": prior_same_era_races,
                    "compound_usable_training_races": compound_usable_training_races,
                    "low_confidence_training": low_confidence,
                    "slope": v2.slope,
                    "ci_low": v2.ci_low,
                    "ci_high": v2.ci_high,
                    "n_training_stints": v2.n_stints,
                    "n_training_laps": v2.n_laps,
                }
            )

            scores = {
                "raw": score_target(
                    target_rows,
                    compound,
                    raw.slope if raw.slope is not None else 0.0,
                    min_stint_age_span,
                ),
                "v2": score_target(
                    target_rows,
                    compound,
                    v2.slope,
                    min_stint_age_span,
                ),
                "flat": score_target(
                    target_rows,
                    compound,
                    0.0,
                    min_stint_age_span,
                ),
            }
            for model, score in scores.items():
                summary.append(
                    {
                        "target_race_id": target.race_id,
                        "target_year": target.season_year,
                        "era": target.era,
                        "compound": compound,
                        "model": model,
                        "race_score_correlation": score.correlation,
                        "race_score_rmse": score.rmse,
                        "stint_count": score.n_stints,
                        "scored_points": score.n_points,
                        "compound_usable_training_races": compound_usable_training_races,
                        "low_confidence_training": low_confidence,
                    }
                )

    return summary, stability


def _summary_for_config(
    summary: list[dict],
    stability: list[dict],
    min_unique_pit_laps: int,
    min_stint_age_span: int,
    min_training_races: int,
):
    overall = {
        model: _overall_race_scores(summary, model)
        for model in ("raw", "v2", "flat")
    }
    v2_rmse = overall["v2"]["overall_rmse"]
    flat_rmse = overall["flat"]["overall_rmse"]
    raw_rmse = overall["raw"]["overall_rmse"]
    improvement_vs_flat = (
        ((flat_rmse - v2_rmse) / flat_rmse) * 100.0
        if v2_rmse is not None and flat_rmse not in (None, 0)
        else None
    )
    improvement_vs_raw = (
        ((raw_rmse - v2_rmse) / raw_rmse) * 100.0
        if v2_rmse is not None and raw_rmse not in (None, 0)
        else None
    )
    low_confidence_rows = [
        row for row in stability if row["low_confidence_training"]
    ]
    return {
        "min_unique_pit_laps": min_unique_pit_laps,
        "min_stint_age_span": min_stint_age_span,
        "min_training_races": min_training_races,
        "v2_rmse": v2_rmse,
        "flat_rmse": flat_rmse,
        "raw_rmse": raw_rmse,
        "v2_correlation": overall["v2"]["overall_correlation"],
        "raw_correlation": overall["raw"]["overall_correlation"],
        "v2_vs_flat_improvement_pct": improvement_vs_flat,
        "v2_vs_raw_improvement_pct": improvement_vs_raw,
        "v2_target_races": overall["v2"]["target_races"],
        "flat_target_races": overall["flat"]["target_races"],
        "raw_target_races": overall["raw"]["target_races"],
        "stability_rows": len(stability),
        "low_confidence_stability_rows": len(low_confidence_rows),
    }


def run_sensitivity(
    db,
    start_year: int,
    end_year: int,
    min_unique_pit_laps_grid: tuple[int, ...] = DEFAULT_MIN_UNIQUE_PIT_LAPS_GRID,
    min_stint_age_span_grid: tuple[int, ...] = DEFAULT_MIN_STINT_AGE_SPAN_GRID,
    min_training_races_grid: tuple[int, ...] = DEFAULT_MIN_TRAINING_RACES_GRID,
):
    rows = load_rows(db, start_year, end_year)
    metas = load_meta(db, start_year, end_year)
    results: list[dict] = []

    for min_unique_pit_laps in min_unique_pit_laps_grid:
        for min_stint_age_span in min_stint_age_span_grid:
            for min_training_races in min_training_races_grid:
                summary, stability = _score_config(
                    rows,
                    metas,
                    min_unique_pit_laps,
                    min_stint_age_span,
                    min_training_races,
                )
                results.append(
                    _summary_for_config(
                        summary,
                        stability,
                        min_unique_pit_laps,
                        min_stint_age_span,
                        min_training_races,
                    )
                )
    return results


def write_csv(path: str, rows: list[dict]):
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-year", type=int, default=2018)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument("--csv", default="tyre_model_sensitivity_v1.csv")
    args = parser.parse_args()

    url = os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is not set")

    db = create_engine(url).connect()
    try:
        results = run_sensitivity(db, args.start_year, args.end_year)
    finally:
        db.close()

    write_csv(args.csv, results)

    print("=== TYRE MODEL SENSITIVITY V1 ===")
    print(f"configurations={len(results)}")
    print(
        "Grid: min_unique_pit_laps="
        f"{DEFAULT_MIN_UNIQUE_PIT_LAPS_GRID}, "
        f"min_stint_age_span={DEFAULT_MIN_STINT_AGE_SPAN_GRID}, "
        f"min_training_races={DEFAULT_MIN_TRAINING_RACES_GRID}"
    )
    print(
        "\nconfig | diversity | age_span | min_train | v2_rmse | flat_rmse | "
        "v2_vs_flat_% | v2_corr | races | low_conf"
    )
    for row in results:
        print(
            f"{row['min_unique_pit_laps']:>6} | "
            f"{row['min_unique_pit_laps']:>9} | "
            f"{row['min_stint_age_span']:>8} | "
            f"{row['min_training_races']:>9} | "
            f"{row['v2_rmse']!s:>7} | "
            f"{row['flat_rmse']!s:>9} | "
            f"{row['v2_vs_flat_improvement_pct']!s:>12} | "
            f"{row['v2_correlation']!s:>7} | "
            f"{row['v2_target_races']:>5} | "
            f"{row['low_confidence_stability_rows']:>8}"
        )

    print(f"\nWrote {args.csv}")
    print("Production simulator/calibration were not modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
