"""Audit meaningful race-strategy diversity from stored dry-race stint data.

This is an audit-only replacement for the old pit-lap-location diversity proxy.
A race's strategy diversity is measured by the number of distinct tyre-compound
sequences used by drivers in that race, e.g. M-H and S-M-H count as different
patterns while repeated M-H occurrences count once.

The audit reports the distribution of strategy-pattern counts and evaluates
which minimum-diversity thresholds would actually filter races. It does not
modify production code or the database.
"""
from __future__ import annotations

import argparse
import csv
import os
from collections import defaultdict

from sqlalchemy import create_engine

from audit_race_strategy_tyre_model_walkforward_v2 import load_rows, load_meta

DRY_COMPOUNDS = {"SOFT", "MEDIUM", "HARD"}
DEFAULT_THRESHOLDS = (1, 2, 3, 4, 5, 6)


def _stint_number(stint_key: str) -> int:
    parts = stint_key.split(":")
    if len(parts) != 3:
        raise ValueError(f"Unexpected stint key format: {stint_key}")
    return int(parts[2])


def build_strategy_sequences(rows):
    """Return distinct dry compound sequences for each race."""
    by_driver: dict[tuple[int, int], dict[int, str]] = defaultdict(dict)
    for row in rows:
        if row.compound not in DRY_COMPOUNDS:
            continue
        key = (row.race_id, row.driver_id)
        by_driver[key][_stint_number(row.stint_key)] = row.compound

    race_sequences: dict[int, set[tuple[str, ...]]] = defaultdict(set)
    for (race_id, _driver_id), stints in by_driver.items():
        if not stints:
            continue
        sequence = tuple(stints[n] for n in sorted(stints))
        race_sequences[race_id].add(sequence)
    return race_sequences


def build_race_diversity(rows):
    """Return race_id -> number of distinct observed strategy sequences."""
    return {
        race_id: len(sequences)
        for race_id, sequences in build_strategy_sequences(rows).items()
    }


def threshold_summary(diversity: dict[int, int], thresholds=DEFAULT_THRESHOLDS):
    total = len(diversity)
    return [
        {
            "min_strategy_patterns": threshold,
            "races_with_data": total,
            "eligible_races": sum(count >= threshold for count in diversity.values()),
            "excluded_races": sum(count < threshold for count in diversity.values()),
            "eligible_pct": (100.0 * sum(count >= threshold for count in diversity.values()) / total) if total else 0.0,
        }
        for threshold in thresholds
    ]


def run(db, start_year: int, end_year: int, thresholds=DEFAULT_THRESHOLDS):
    rows = load_rows(db, start_year, end_year)
    metas = load_meta(db, start_year, end_year)
    diversity = build_race_diversity(rows)
    sequences = build_strategy_sequences(rows)

    per_race = []
    for meta in metas:
        patterns = sorted(sequences.get(meta.race_id, set()))
        per_race.append(
            {
                "race_id": meta.race_id,
                "season_year": meta.season_year,
                "race_date": meta.race_date,
                "era": meta.era,
                "strategy_pattern_count": len(patterns),
                "strategy_patterns": "|".join("-".join(p) for p in patterns),
            }
        )

    summary = threshold_summary(diversity, thresholds)
    return per_race, summary


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
    parser.add_argument("--csv", default="race_strategy_diversity_v1.csv")
    parser.add_argument("--summary-csv", default="race_strategy_diversity_thresholds_v1.csv")
    args = parser.parse_args()

    url = os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is not set")

    db = create_engine(url).connect()
    try:
        per_race, summary = run(db, args.start_year, args.end_year)
    finally:
        db.close()

    write_csv(args.csv, per_race)
    write_csv(args.summary_csv, summary)

    print("=== STRATEGY DIVERSITY AUDIT V1 ===")
    counts = [r["strategy_pattern_count"] for r in per_race if r["strategy_pattern_count"] > 0]
    print(f"races_with_observed_strategy_patterns={len(counts)}")
    print(f"min_patterns={min(counts) if counts else None} max_patterns={max(counts) if counts else None}")
    print("\nthreshold | eligible | excluded | eligible_pct")
    for row in summary:
        print(
            f"{row['min_strategy_patterns']:>9} | "
            f"{row['eligible_races']:>8} | "
            f"{row['excluded_races']:>8} | "
            f"{row['eligible_pct']:>11.2f}"
        )
    print(f"\nWrote {args.csv} and {args.summary_csv}")
    print("Audit only; production code and database were not modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
