"""Fast sensitivity sweep using corrected equal-stint race aggregation.

Audit-only. This is the v2 counterpart to the earlier sensitivity sweep. The
key correction is that each target race averages every valid held-out stint
across all compounds, rather than averaging compound-level summaries. Target
races are then weighted equally in the final aggregate.

The parameter grid is unchanged from sensitivity v1 so results remain directly
comparable, subject to the corrected scoring definition.
"""
from __future__ import annotations

import argparse
import csv
import os
from collections import defaultdict

from sqlalchemy import create_engine

from audit_race_strategy_tyre_model_v2 import COMPOUNDS, LapRow
from audit_race_strategy_tyre_model_sensitivity_v1 import _fit_training_models
from audit_race_strategy_tyre_model_walkforward_v2 import (
    _field_relative_rows,
    _low_confidence,
    _race_diversity,
    load_meta,
    load_rows,
)
from audit_race_strategy_tyre_model_walkforward_v3 import (
    _aggregate_stint_scores,
    _score_stints,
)

DEFAULT_MIN_UNIQUE_PIT_LAPS_GRID = (3, 4, 5)
DEFAULT_MIN_STINT_AGE_SPAN_GRID = (3, 4, 5, 6)
DEFAULT_MIN_TRAINING_RACES_GRID = (10, 15, 20)


def _score_from_relative(relative_rows, slopes_by_compound):
    return _aggregate_stint_scores(
        _score_stints(relative_rows, slopes_by_compound)
    )


def _score_config(
    rows: list[LapRow],
    metas,
    rows_by_race: dict[int, list[LapRow]],
    diversity: dict[int, int],
    min_unique_pit_laps: int,
    min_stint_age_span: int,
    min_training_races: int,
    training_cache: dict,
    target_cache: dict,
):
    race_scores: dict[str, list[dict]] = defaultdict(list)
    stability: list[dict] = []

    for target in metas:
        training = [
            m for m in metas
            if m.era == target.era and m.race_date < target.race_date
        ]
        prior_same_era_races = len(training)
        if prior_same_era_races < min_training_races:
            continue

        train_ids = frozenset(
            m.race_id
            for m in training
            if diversity.get(m.race_id, 0) >= min_unique_pit_laps
        )
        if not train_ids:
            continue

        training_key = (train_ids, min_stint_age_span)
        cached_training = training_cache.get(training_key)
        if cached_training is None:
            train_rows = [
                row
                for race_id in train_ids
                for row in rows_by_race.get(race_id, [])
                if row.era == target.era
            ]
            cached_training = _fit_training_models(
                train_rows,
                set(train_ids),
                min_stint_age_span,
            )
            training_cache[training_key] = cached_training

        _, fits = cached_training
        target_key = (target.race_id, min_stint_age_span)
        if target_key not in target_cache:
            target_cache[target_key] = _field_relative_rows(
                rows_by_race.get(target.race_id, []),
                {target.race_id},
                min_stint_age_span,
            )
        target_relative = target_cache[target_key]

        model_slopes: dict[str, dict[str, float]] = {
            "raw": {},
            "v2": {},
            "flat": {},
        }

        for compound in COMPOUNDS:
            fit = fits[compound]
            v2_slope = fit["v2_slope"]
            if v2_slope is None:
                continue

            raw_slope = fit["raw_slope"]
            model_slopes["v2"][compound] = v2_slope
            model_slopes["raw"][compound] = raw_slope if raw_slope is not None else 0.0
            model_slopes["flat"][compound] = 0.0

            usable_races = fit["v2_races"]
            stability.append(
                {
                    "target_race_id": target.race_id,
                    "target_year": target.season_year,
                    "era": target.era,
                    "compound": compound,
                    "prior_same_era_races": prior_same_era_races,
                    "compound_usable_training_races": usable_races,
                    "low_confidence_training": _low_confidence(usable_races),
                    "slope": v2_slope,
                    "ci_low": None,
                    "ci_high": None,
                    "n_training_stints": fit["v2_stints"],
                    "n_training_laps": fit["v2_laps"],
                }
            )

        for model, slopes in model_slopes.items():
            if not slopes:
                continue
            score = _score_from_relative(target_relative, slopes)
            if score.rmse is None:
                continue
            race_scores[model].append(
                {
                    "target_race_id": target.race_id,
                    "race_score_correlation": score.correlation,
                    "race_score_rmse": score.rmse,
                    "stint_count": score.n_stints,
                    "scored_points": score.n_points,
                }
            )

    return race_scores, stability


def _overall_model_scores(race_scores, model: str):
    rows = race_scores.get(model, [])
    correlations = [
        row["race_score_correlation"]
        for row in rows
        if row["race_score_correlation"] is not None
    ]
    rmses = [row["race_score_rmse"] for row in rows]
    return {
        "overall_correlation": sum(correlations) / len(correlations) if correlations else None,
        "overall_rmse": sum(rmses) / len(rmses) if rmses else None,
        "target_races": len(rmses),
    }


def _summary_for_config(
    race_scores,
    stability,
    min_unique_pit_laps: int,
    min_stint_age_span: int,
    min_training_races: int,
):
    overall = {
        model: _overall_model_scores(race_scores, model)
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
    rows_by_race: dict[int, list[LapRow]] = defaultdict(list)
    for row in rows:
        rows_by_race[row.race_id].append(row)
    diversity = _race_diversity(rows)

    training_cache: dict = {}
    target_cache: dict = {}
    results: list[dict] = []

    total = (
        len(min_unique_pit_laps_grid)
        * len(min_stint_age_span_grid)
        * len(min_training_races_grid)
    )
    completed = 0

    for min_unique_pit_laps in min_unique_pit_laps_grid:
        for min_stint_age_span in min_stint_age_span_grid:
            for min_training_races in min_training_races_grid:
                completed += 1
                race_scores, stability = _score_config(
                    rows,
                    metas,
                    rows_by_race,
                    diversity,
                    min_unique_pit_laps,
                    min_stint_age_span,
                    min_training_races,
                    training_cache,
                    target_cache,
                )
                results.append(
                    _summary_for_config(
                        race_scores,
                        stability,
                        min_unique_pit_laps,
                        min_stint_age_span,
                        min_training_races,
                    )
                )
                print(
                    f"Progress: {completed}/{total} configs | "
                    f"diversity={min_unique_pit_laps} age_span={min_stint_age_span} "
                    f"min_train={min_training_races}",
                    flush=True,
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
    parser.add_argument("--csv", default="tyre_model_sensitivity_v2.csv")
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

    print("=== TYRE MODEL SENSITIVITY V2 ===")
    print(f"configurations={len(results)}")
    print(
        "Scoring: lap points -> equal-weight stints across compounds -> equal-weight races"
    )
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
