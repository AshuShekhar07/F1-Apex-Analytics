"""Continuous traffic-exposure analysis v1.

Uses persisted traffic checkpoint exposures rather than matched clean-air pairs.
The primary analysis estimates a within-driver/stint association between
continuous close-following exposure and field-relative lap-time residual.

This is observational and does not establish a causal traffic penalty.
No database, simulator, or FastF1 state is modified.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from audit_race_strategy_traffic_v1 import TrafficExposure, _load_checkpoint


def load_exposures(checkpoint_dir: str) -> tuple[list[TrafficExposure], int]:
    root = Path(checkpoint_dir)
    if not root.exists():
        raise SystemExit(f"Checkpoint directory not found: {root}")
    exposures: list[TrafficExposure] = []
    count = 0
    for path in sorted(root.glob("*.json")):
        if path.name.endswith(".tmp"):
            continue
        rows, _ = _load_checkpoint(str(path))
        exposures.extend(rows)
        count += 1
    if not exposures:
        raise SystemExit("No exposures found in checkpoint directory")
    return exposures, count


def frame(exposures: list[TrafficExposure], threshold: float) -> pd.DataFrame:
    rows = []
    for e in exposures:
        x = e.close_fraction(threshold)
        y = e.lap.field_relative_residual
        if not np.isfinite(x) or not np.isfinite(y):
            continue
        rows.append(
            {
                "race_id": e.lap.race_id,
                "season_year": e.lap.season_year,
                "regulation_era": e.lap.regulation_era,
                "driver_key": e.lap.driver_key,
                "stint_number": e.lap.stint_number,
                "compound": e.lap.compound,
                "lap_number": e.lap.lap_number,
                "tyre_age": e.lap.tyre_age,
                "close_fraction": float(x),
                "sustained_close_seconds": float(e.sustained_close_seconds(threshold)),
                "residual_seconds": float(y),
            }
        )
    return pd.DataFrame(rows)


def group_slopes(df: pd.DataFrame) -> pd.DataFrame:
    keys = ["race_id", "season_year", "regulation_era", "driver_key", "stint_number", "compound"]
    out: list[dict[str, object]] = []

    for key, g in df.groupby(keys, sort=False, dropna=False):
        if len(g) < 3:
            continue

        x = g["close_fraction"].to_numpy(float)
        y = g["residual_seconds"].to_numpy(float)

        # Within-stint slope: removes time-invariant driver/stint level effects.
        x_center = x - x.mean()
        y_center = y - y.mean()
        denom = float(np.dot(x_center, x_center))
        if denom <= 1e-10:
            continue

        slope = float(np.dot(x_center, y_center) / denom)
        corr = float(np.corrcoef(x, y)[0, 1]) if np.std(x) > 0 and np.std(y) > 0 else np.nan

        out.append(
            {
                "race_id": int(key[0]),
                "season_year": int(key[1]),
                "regulation_era": str(key[2]),
                "driver_key": str(key[3]),
                "stint_number": int(key[4]) if pd.notna(key[4]) else None,
                "compound": str(key[5]),
                "n_laps": len(g),
                "close_fraction_mean": float(x.mean()),
                "residual_mean_seconds": float(y.mean()),
                "slope_seconds_per_full_exposure": slope,
                "slope_ms_per_10pct": slope * 100.0,
                "within_stint_correlation": corr,
            }
        )

    return pd.DataFrame(out)


def race_means(stints: pd.DataFrame) -> pd.DataFrame:
    if stints.empty:
        return pd.DataFrame()
    return (
        stints.groupby(
            ["race_id", "season_year", "regulation_era"], as_index=False
        )["slope_seconds_per_full_exposure"]
        .mean()
        .rename(columns={"slope_seconds_per_full_exposure": "race_slope_seconds"})
    )


def bootstrap_ci(values: np.ndarray, iterations: int, seed: int) -> tuple[float, float]:
    if len(values) < 2:
        return np.nan, np.nan
    rng = np.random.default_rng(seed)
    samples = []
    remaining = iterations
    while remaining:
        n = min(1000, remaining)
        idx = rng.integers(0, len(values), size=(n, len(values)))
        samples.append(values[idx].mean(axis=1))
        remaining -= n
    boot = np.concatenate(samples)
    return float(np.quantile(boot, 0.025)), float(np.quantile(boot, 0.975))


def summarize(race_df: pd.DataFrame, scope: str, threshold: float, iterations: int, seed: int) -> dict[str, object]:
    v = race_df["race_slope_seconds"].to_numpy(float)
    n = len(v)
    m = float(v.mean()) if n else np.nan
    sd = float(v.std(ddof=1)) if n > 1 else np.nan
    se = sd / np.sqrt(n) if n > 1 else np.nan
    lo, hi = bootstrap_ci(v, iterations, seed)
    return {
        "scope": scope,
        "threshold_m": threshold,
        "races": n,
        "mean_slope_seconds_per_full_exposure": m,
        "mean_effect_ms_per_10pct": m * 100.0 if np.isfinite(m) else np.nan,
        "clustered_se_seconds": se,
        "bootstrap_95ci_low_seconds": lo,
        "bootstrap_95ci_high_seconds": hi,
        "positive_race_rate": float((v > 0).mean()) if n else np.nan,
        "median_race_slope_seconds": float(np.median(v)) if n else np.nan,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze continuous traffic exposure from audit checkpoints")
    parser.add_argument("--checkpoint-dir", default="traffic_checkpoints_v1")
    parser.add_argument("--thresholds", default="100,150,200")
    parser.add_argument("--bootstrap-iterations", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--output-prefix", default="traffic_continuous_v1")
    args = parser.parse_args()

    thresholds = tuple(sorted({float(x.strip()) for x in args.thresholds.split(",") if x.strip()}))
    if not thresholds or any(x <= 0 for x in thresholds):
        raise SystemExit("Thresholds must be positive")

    exposures, checkpoints = load_exposures(args.checkpoint_dir)
    all_summary = []
    all_stints = []
    all_races = []

    for threshold in thresholds:
        df = frame(exposures, threshold)
        stints = group_slopes(df)
        races = race_means(stints)
        if not races.empty:
            for era, era_df in races.groupby("regulation_era", sort=True):
                all_summary.append(
                    summarize(
                        era_df,
                        f"era:{era}",
                        threshold,
                        args.bootstrap_iterations,
                        args.seed,
                    )
                )
            all_summary.append(
                summarize(
                    races,
                    "overall",
                    threshold,
                    args.bootstrap_iterations,
                    args.seed,
                )
            )
        stints = stints.copy()
        stints["threshold_m"] = threshold
        races = races.copy()
        races["threshold_m"] = threshold
        all_stints.append(stints)
        all_races.append(races)

    summary = pd.DataFrame(all_summary)
    stints = pd.concat(all_stints, ignore_index=True) if all_stints else pd.DataFrame()
    races = pd.concat(all_races, ignore_index=True) if all_races else pd.DataFrame()

    summary.to_csv(f"{args.output_prefix}_summary.csv", index=False)
    stints.to_csv(f"{args.output_prefix}_stints.csv", index=False)
    races.to_csv(f"{args.output_prefix}_races.csv", index=False)

    print("=== CONTINUOUS TRAFFIC ANALYSIS V1 ===")
    print(f"checkpoint_files={checkpoints}")
    print(f"exposures_loaded={len(exposures)}")
    print("\nRace-clustered within-stint slope:")
    print(summary.to_string(index=False))
    if not races.empty:
        print("\nMost positive race slopes (150 m):")
        print(
            races[races["threshold_m"].eq(150.0)]
            .nlargest(10, "race_slope_seconds")[
                ["season_year", "race_id", "regulation_era", "race_slope_seconds"]
            ].to_string(index=False)
        )
        print("\nMost negative race slopes (150 m):")
        print(
            races[races["threshold_m"].eq(150.0)]
            .nsmallest(10, "race_slope_seconds")[
                ["season_year", "race_id", "regulation_era", "race_slope_seconds"]
            ].to_string(index=False)
        )
    print(f"\nWrote {args.output_prefix}_summary.csv")
    print(f"Wrote {args.output_prefix}_stints.csv")
    print(f"Wrote {args.output_prefix}_races.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
