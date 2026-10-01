"""Traffic effect validation v2 from completed race checkpoints.

Research-only post-hoc analysis. It reads the persisted traffic_checkpoints_v1
artifacts produced by the telemetry audit, so it can reconstruct 100/150/200 m
classifications and matching exactly as the audit did without fetching FastF1
telemetry again.

No database or simulator state is modified.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from audit_race_strategy_traffic_v1 import (
    TrafficExposure,
    _classify,
    _match_exposures,
    _load_checkpoint,
)

DEFAULT_CHECKPOINT_DIR = "traffic_checkpoints_v1"
DEFAULT_THRESHOLDS = (100.0, 150.0, 200.0)


def load_exposures(checkpoint_dir: str) -> tuple[list[TrafficExposure], int]:
    root = Path(checkpoint_dir)
    if not root.exists():
        raise SystemExit(f"Checkpoint directory not found: {root}")

    exposures: list[TrafficExposure] = []
    checkpoint_count = 0
    bad: list[str] = []

    for path in sorted(root.glob("*.json")):
        if path.name.endswith(".tmp"):
            continue
        try:
            rows, _delta = _load_checkpoint(str(path))
            exposures.extend(rows)
            checkpoint_count += 1
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            bad.append(f"{path.name}: {exc}")

    if bad:
        raise SystemExit("Invalid checkpoint(s): " + "; ".join(bad[:5]))
    if not exposures:
        raise SystemExit(f"No exposures found in {root}")

    return exposures, checkpoint_count


def race_level_effects(matches) -> pd.DataFrame:
    if not matches:
        return pd.DataFrame(
            columns=["race_id", "season_year", "regulation_era", "race_mean_delta_seconds"]
        )

    by_stint: dict[tuple[int, str, int], list[float]] = defaultdict(list)
    for match in matches:
        by_stint[(match.race_id, match.driver_key, match.stint_number)].append(
            match.traffic_delta_seconds
        )

    stint_rows = [
        {
            "race_id": race_id,
            "driver_key": driver_key,
            "stint_number": stint,
            "stint_mean_delta_seconds": float(np.mean(values)),
        }
        for (race_id, driver_key, stint), values in by_stint.items()
    ]
    stint = pd.DataFrame(stint_rows)
    race = (
        stint.groupby("race_id", as_index=False)["stint_mean_delta_seconds"]
        .mean()
        .rename(columns={"stint_mean_delta_seconds": "race_mean_delta_seconds"})
    )

    meta = pd.DataFrame(
        [
            {
                "race_id": m.race_id,
                "season_year": m.season_year,
                "regulation_era": m.regulation_era,
            }
            for m in matches
        ]
    ).drop_duplicates("race_id")

    return race.merge(meta, on="race_id", how="left").sort_values(
        ["season_year", "race_id"]
    )


def bootstrap_ci(values: np.ndarray, iterations: int, seed: int) -> tuple[float, float]:
    values = np.asarray(values, dtype=float)
    if len(values) < 2:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    # Batch to keep memory bounded for large iteration counts.
    boot_means: list[np.ndarray] = []
    batch_size = 1000
    remaining = iterations
    while remaining:
        batch = min(batch_size, remaining)
        idx = rng.integers(0, len(values), size=(batch, len(values)))
        boot_means.append(values[idx].mean(axis=1))
        remaining -= batch
    samples = np.concatenate(boot_means)
    return float(np.quantile(samples, 0.025)), float(np.quantile(samples, 0.975))


def effect_stats(
    race_df: pd.DataFrame,
    *,
    scope: str,
    threshold: float,
    iterations: int,
    seed: int,
    matched_pairs: int,
) -> dict[str, object]:
    values = race_df["race_mean_delta_seconds"].to_numpy(float)
    n = len(values)
    mean_value = float(values.mean()) if n else float("nan")
    median_value = float(np.median(values)) if n else float("nan")
    sd = float(values.std(ddof=1)) if n > 1 else float("nan")
    se = sd / np.sqrt(n) if n > 1 else float("nan")
    lo, hi = bootstrap_ci(values, iterations, seed)

    return {
        "scope": scope,
        "regulation_era": (
            str(race_df["regulation_era"].iloc[0]) if len(race_df) else "ALL"
        ),
        "threshold_m": threshold,
        "races": n,
        "matched_pairs": matched_pairs,
        "mean_delta_seconds": mean_value,
        "median_race_delta_seconds": median_value,
        "race_clustered_se_seconds": se,
        "bootstrap_95ci_low_seconds": lo,
        "bootstrap_95ci_high_seconds": hi,
        "positive_race_rate": float((values > 0).mean()) if n else float("nan"),
        "negative_race_rate": float((values < 0).mean()) if n else float("nan"),
        "min_race_delta_seconds": float(values.min()) if n else float("nan"),
        "max_race_delta_seconds": float(values.max()) if n else float("nan"),
    }


def unmatched_diagnostics(
    exposures: list[TrafficExposure],
    *,
    threshold: float,
    min_close_fraction: float,
    min_ahead_coverage: float,
    unmatched_close: int,
) -> dict[str, object]:
    close = [
        e for e in exposures
        if _classify(e, threshold, min_close_fraction, min_ahead_coverage) == "close"
    ]
    matched_by_key: set[tuple[int, str, int]] = set()
    # Re-run matching to identify exactly which close exposure rows were matched.
    matches, _, _, _ = _match_exposures(
        exposures,
        threshold=threshold,
        min_close_fraction=min_close_fraction,
        min_ahead_coverage=min_ahead_coverage,
        max_lap_distance=10,
        max_tyre_age_diff=1,
        max_clean_air_reuse=1,
    )
    for m in matches:
        matched_by_key.add((m.race_id, m.driver_key, m.close_lap))

    matched = [
        e for e in close
        if (e.lap.race_id, e.lap.driver_key, e.lap.lap_number) in matched_by_key
    ]
    unmatched = [
        e for e in close
        if (e.lap.race_id, e.lap.driver_key, e.lap.lap_number) not in matched_by_key
    ]

    def summarize(label: str, rows: list[TrafficExposure]) -> dict[str, object]:
        if not rows:
            return {
                "threshold_m": threshold,
                "group": label,
                "close_laps": 0,
                "share_of_close_laps": 0.0,
                "mean_tyre_age": np.nan,
                "mean_close_fraction": np.nan,
                "mean_sustained_close_seconds": np.nan,
            }

        return {
            "threshold_m": threshold,
            "group": label,
            "close_laps": len(rows),
            "share_of_close_laps": len(rows) / len(close) if close else np.nan,
            "mean_tyre_age": float(np.mean([e.lap.tyre_age for e in rows if e.lap.tyre_age is not None])),
            "mean_close_fraction": float(np.mean([e.close_fraction(threshold) for e in rows])),
            "mean_sustained_close_seconds": float(
                np.mean([e.sustained_close_seconds(threshold) for e in rows])
            ),
        }

    return {
        "matched": summarize("matched_close", matched),
        "unmatched": summarize("unmatched_close", unmatched),
        "close_count": len(close),
        "unmatched_close_reported": unmatched_close,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate traffic effect v2 from completed traffic checkpoints"
    )
    parser.add_argument("--checkpoint-dir", default=DEFAULT_CHECKPOINT_DIR)
    parser.add_argument(
        "--thresholds",
        default="100,150,200",
        help="Comma-separated close-distance thresholds in metres",
    )
    parser.add_argument("--output-prefix", default="traffic_validation_v2")
    parser.add_argument("--bootstrap-iterations", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=17)
    args = parser.parse_args()

    thresholds = tuple(sorted({float(x.strip()) for x in args.thresholds.split(",") if x.strip()}))
    if not thresholds or any(v <= 0 for v in thresholds):
        raise SystemExit("Thresholds must be positive")
    if args.bootstrap_iterations < 1000:
        raise SystemExit("Use at least 1000 bootstrap iterations")

    exposures, checkpoint_count = load_exposures(args.checkpoint_dir)
    all_effects: list[dict[str, object]] = []
    all_races: list[pd.DataFrame] = []
    all_unmatched: list[dict[str, object]] = []

    for threshold in thresholds:
        matches, unmatched, close_count, clear_count = _match_exposures(
            exposures,
            threshold=threshold,
            min_close_fraction=0.25,
            min_ahead_coverage=0.50,
            max_lap_distance=10,
            max_tyre_age_diff=1,
            max_clean_air_reuse=1,
        )
        race_df = race_level_effects(matches)
        all_races.append(race_df.assign(threshold_m=threshold))

        if race_df.empty:
            continue

        all_effects.append(
            effect_stats(
                race_df,
                scope="overall",
                threshold=threshold,
                iterations=args.bootstrap_iterations,
                seed=args.seed,
                matched_pairs=len(matches),
            )
        )

        for era, era_df in race_df.groupby("regulation_era", sort=True):
            era_matches = [m for m in matches if m.regulation_era == era]
            all_effects.append(
                effect_stats(
                    era_df,
                    scope="era",
                    threshold=threshold,
                    iterations=args.bootstrap_iterations,
                    seed=args.seed,
                    matched_pairs=len(era_matches),
                )
            )

        diag = unmatched_diagnostics(
            exposures,
            threshold=threshold,
            min_close_fraction=0.25,
            min_ahead_coverage=0.50,
            unmatched_close=unmatched,
        )
        all_unmatched.extend([diag["matched"], diag["unmatched"]])

    effects = pd.DataFrame(all_effects).sort_values(
        ["scope", "regulation_era", "threshold_m"]
    )
    races = pd.concat(all_races, ignore_index=True) if all_races else pd.DataFrame()
    unmatched_df = pd.DataFrame(all_unmatched)

    effects.to_csv(f"{args.output_prefix}_effects.csv", index=False)
    races.to_csv(f"{args.output_prefix}_race_deltas.csv", index=False)
    unmatched_df.to_csv(f"{args.output_prefix}_unmatched.csv", index=False)

    stability = (
        effects[effects["scope"].eq("era")]
        .groupby(["regulation_era"], as_index=False)
        .agg(
            thresholds=("threshold_m", "count"),
            all_thresholds_positive=("mean_delta_seconds", lambda s: bool((s > 0).all())),
            minimum_effect_seconds=("mean_delta_seconds", "min"),
            maximum_effect_seconds=("mean_delta_seconds", "max"),
            minimum_ci_low_seconds=("bootstrap_95ci_low_seconds", "min"),
            maximum_ci_high_seconds=("bootstrap_95ci_high_seconds", "max"),
        )
    )
    stability.to_csv(f"{args.output_prefix}_stability.csv", index=False)

    print("=== TRAFFIC VALIDATION V2 ===")
    print(f"checkpoint_files={checkpoint_count}")
    print(f"exposures_loaded={len(exposures)}")
    print("\nEffects with race-clustered uncertainty:")
    print(effects.to_string(index=False))
    print("\nEra / threshold directional stability:")
    print(stability.to_string(index=False))
    print("\nMatched vs unmatched close laps:")
    print(unmatched_df.to_string(index=False))
    print(f"\nWrote {args.output_prefix}_effects.csv")
    print(f"Wrote {args.output_prefix}_race_deltas.csv")
    print(f"Wrote {args.output_prefix}_unmatched.csv")
    print(f"Wrote {args.output_prefix}_stability.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
