"""Tyre-model sensitivity using observed strategy-pattern diversity.

Audit-only. Reuses the corrected sensitivity scoring implementation but replaces
its old pit-lap diversity proxy with the number of distinct observed compound
sequences in each race. This makes the diversity axis materially testable.
"""
from __future__ import annotations

import argparse
import csv
import os
from collections import defaultdict

from sqlalchemy import create_engine

from audit_race_strategy_tyre_model_sensitivity_v2 import (
    _score_config,
    _summary_for_config,
    load_meta,
    load_rows,
)

DEFAULT_MIN_STRATEGY_PATTERNS_GRID = (2, 3, 4, 5)
DEFAULT_MIN_STINT_AGE_SPAN_GRID = (3, 4, 5, 6)
DEFAULT_MIN_TRAINING_RACES_GRID = (10, 15, 20)


def strategy_pattern_diversity(rows):
    """Return count of distinct dry-race compound sequences per race."""
    by_driver: dict[tuple[int, int], dict[str, tuple[int, str]]] = defaultdict(dict)
    for row in rows:
        key = (row.race_id, row.driver_id)
        by_driver[key][row.stint_key] = (row.start_lap, row.compound)

    patterns: dict[int, set[tuple[str, ...]]] = defaultdict(set)
    for (race_id, _driver_id), stints in by_driver.items():
        ordered = [compound for _, compound in sorted(stints.values(), key=lambda item: item[0])]
        if ordered:
            patterns[race_id].add(tuple(ordered))
    return {race_id: len(sequences) for race_id, sequences in patterns.items()}


def run_sensitivity(
    db,
    start_year: int,
    end_year: int,
    pattern_grid=DEFAULT_MIN_STRATEGY_PATTERNS_GRID,
    age_grid=DEFAULT_MIN_STINT_AGE_SPAN_GRID,
    training_grid=DEFAULT_MIN_TRAINING_RACES_GRID,
):
    rows = load_rows(db, start_year, end_year)
    metas = load_meta(db, start_year, end_year)
    rows_by_race = defaultdict(list)
    for row in rows:
        rows_by_race[row.race_id].append(row)
    diversity = strategy_pattern_diversity(rows)

    training_cache = {}
    target_cache = {}
    results = []
    total = len(pattern_grid) * len(age_grid) * len(training_grid)
    completed = 0

    for min_patterns in pattern_grid:
        for age_span in age_grid:
            for min_training in training_grid:
                completed += 1
                race_scores, stability = _score_config(
                    rows,
                    metas,
                    rows_by_race,
                    diversity,
                    min_patterns,
                    age_span,
                    min_training,
                    training_cache,
                    target_cache,
                )
                result = _summary_for_config(
                    race_scores,
                    stability,
                    min_patterns,
                    age_span,
                    min_training,
                )
                result["min_strategy_patterns"] = result.pop("min_unique_pit_laps")
                results.append(result)
                print(
                    f"Progress: {completed}/{total} configs | "
                    f"patterns={min_patterns} age_span={age_span} min_train={min_training}",
                    flush=True,
                )
    return results


def write_csv(path, rows):
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
    parser.add_argument("--csv", default="tyre_model_sensitivity_v3.csv")
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

    print("=== TYRE MODEL SENSITIVITY V3 ===")
    print(f"configurations={len(results)}")
    print(
        "Grid: min_strategy_patterns="
        f"{DEFAULT_MIN_STRATEGY_PATTERNS_GRID}, "
        f"min_stint_age_span={DEFAULT_MIN_STINT_AGE_SPAN_GRID}, "
        f"min_training_races={DEFAULT_MIN_TRAINING_RACES_GRID}"
    )
    print("Scoring: lap points -> equal-weight stints across compounds -> equal-weight races")
    print("Diversity: distinct observed compound sequences per dry race")
    for row in results:
        print(
            f"patterns={row['min_strategy_patterns']:>2} "
            f"age_span={row['min_stint_age_span']:>2} "
            f"min_train={row['min_training_races']:>2} "
            f"v2_rmse={row['v2_rmse']!s:>18} "
            f"flat_rmse={row['flat_rmse']!s:>18} "
            f"v2_vs_flat_%={row['v2_vs_flat_improvement_pct']!s:>10} "
            f"v2_corr={row['v2_correlation']!s:>10} "
            f"races={row['v2_target_races']:>3} "
            f"low_conf={row['low_confidence_stability_rows']:>3}"
        )
    print(f"\nWrote {args.csv}")
    print("Audit only; production simulator/calibration were not modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
