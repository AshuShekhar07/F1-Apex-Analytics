"""Robustness checks for controlled continuous traffic exposure.

Research-only analysis from persisted traffic checkpoints. Tests whether the
positive continuous traffic association survives stricter stint-quality
filters, a first-difference specification, and a within-stint top-vs-bottom
exposure contrast.

No FastF1 fetch, database mutation, or simulator mutation.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from audit_race_strategy_traffic_v1 import _load_checkpoint


def load_rows(checkpoint_dir: str, threshold: float) -> pd.DataFrame:
    rows = []
    for path in sorted(Path(checkpoint_dir).glob("*.json")):
        exposures, _ = _load_checkpoint(str(path))
        for e in exposures:
            age = e.lap.tyre_age
            if age is None:
                continue
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
                    "x": float(x),
                    "y": float(y),
                }
            )
    return pd.DataFrame(rows)


def race_stint_slopes(
    df: pd.DataFrame,
    *,
    min_n: int,
    min_span: float,
    method: str,
) -> pd.DataFrame:
    keys = ["race_id", "season_year", "regulation_era", "driver_key", "stint_number", "compound"]
    rows = []

    for key, g in df.groupby(keys, sort=False, dropna=False):
        g = g.sort_values("lap_number").reset_index(drop=True)
        if len(g) < min_n or g["x"].max() - g["x"].min() < min_span:
            continue

        progress = (g["lap_number"] - g["lap_number"].min()) / max(
            1.0, g["lap_number"].max() - g["lap_number"].min()
        )
        x = g["x"].to_numpy(float)
        y = g["y"].to_numpy(float)

        if method == "controlled_ols":
            X = np.column_stack([np.ones(len(g)), x, progress, progress * progress])
            beta, *_ = np.linalg.lstsq(X, y, rcond=None)
            coef = float(beta[1])
        elif method == "first_difference":
            dx = np.diff(x)
            dy = np.diff(y)
            keep = np.isfinite(dx) & np.isfinite(dy) & (np.abs(dx) > 1e-6)
            if keep.sum() < 5:
                continue
            denom = float(np.dot(dx[keep], dx[keep]))
            if denom <= 1e-10:
                continue
            coef = float(np.dot(dx[keep], dy[keep]) / denom)
        elif method == "quartile_contrast":
            q25, q75 = np.quantile(x, [0.25, 0.75])
            low = y[x <= q25]
            high = y[x >= q75]
            if len(low) < 2 or len(high) < 2:
                continue
            exposure_gap = float(np.mean(x[x >= q75]) - np.mean(x[x <= q25]))
            if exposure_gap <= 1e-6:
                continue
            coef = float((np.mean(high) - np.mean(low)) / exposure_gap)
        else:
            raise ValueError(method)

        if not np.isfinite(coef):
            continue

        rows.append(
            {
                "race_id": int(key[0]),
                "season_year": int(key[1]),
                "regulation_era": str(key[2]),
                "driver_key": str(key[3]),
                "stint_number": int(key[4]),
                "compound": str(key[5]),
                "n_laps": len(g),
                "exposure_span": float(g["x"].max() - g["x"].min()),
                "slope_seconds_per_full_exposure": coef,
                "effect_ms_per_10pct": coef * 100.0,
            }
        )

    return pd.DataFrame(rows)


def race_means(stints: pd.DataFrame) -> pd.DataFrame:
    if stints.empty:
        return pd.DataFrame()
    return (
        stints.groupby(["race_id", "season_year", "regulation_era"], as_index=False)
        ["slope_seconds_per_full_exposure"]
        .mean()
        .rename(columns={"slope_seconds_per_full_exposure": "race_slope"})
    )


def bootstrap(values: np.ndarray, iterations: int, seed: int) -> tuple[float, float]:
    if len(values) < 2:
        return np.nan, np.nan
    rng = np.random.default_rng(seed)
    draws = []
    remaining = iterations
    while remaining:
        n = min(1000, remaining)
        idx = rng.integers(0, len(values), size=(n, len(values)))
        draws.append(values[idx].mean(axis=1))
        remaining -= n
    boot = np.concatenate(draws)
    return float(np.quantile(boot, .025)), float(np.quantile(boot, .975))


def summarize(races: pd.DataFrame, spec: str, threshold: float, iterations: int, seed: int) -> dict[str, object]:
    v = races["race_slope"].to_numpy(float)
    mean_v = float(v.mean()) if len(v) else np.nan
    median_v = float(np.median(v)) if len(v) else np.nan
    lo, hi = bootstrap(v, iterations, seed)
    trimmed = v
    if len(v) >= 20:
        qlo, qhi = np.quantile(v, [.05, .95])
        trimmed = v[(v >= qlo) & (v <= qhi)]
    return {
        "specification": spec,
        "threshold_m": threshold,
        "races": len(v),
        "mean_slope_seconds": mean_v,
        "effect_ms_per_10pct": mean_v * 100 if np.isfinite(mean_v) else np.nan,
        "median_slope_seconds": median_v,
        "trimmed_mean_ms_per_10pct": float(trimmed.mean() * 100) if len(trimmed) else np.nan,
        "bootstrap_95ci_low_seconds": lo,
        "bootstrap_95ci_high_seconds": hi,
        "positive_race_rate": float((v > 0).mean()) if len(v) else np.nan,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint-dir", default="traffic_checkpoints_v1")
    parser.add_argument("--thresholds", default="100,150,200")
    parser.add_argument("--min-n", type=int, default=8)
    parser.add_argument("--min-span", type=float, default=.15)
    parser.add_argument("--bootstrap-iterations", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--output-prefix", default="traffic_robust_v1")
    args = parser.parse_args()

    thresholds = tuple(sorted({float(x.strip()) for x in args.thresholds.split(",") if x.strip()}))
    summaries = []
    race_outputs = []

    for threshold in thresholds:
        df = load_rows(args.checkpoint_dir, threshold)
        for method in ("controlled_ols", "first_difference", "quartile_contrast"):
            stints = race_stint_slopes(
                df, min_n=args.min_n, min_span=args.min_span, method=method
            )
            races = race_means(stints)
            if races.empty:
                continue

            for era, era_df in races.groupby("regulation_era", sort=True):
                summaries.append(
                    summarize(
                        era_df,
                        f"{method}:era:{era}",
                        threshold,
                        args.bootstrap_iterations,
                        args.seed,
                    )
                )
            summaries.append(
                summarize(
                    races,
                    f"{method}:overall",
                    threshold,
                    args.bootstrap_iterations,
                    args.seed,
                )
            )
            race_outputs.append(
                races.assign(
                    threshold_m=threshold,
                    specification=method,
                )
            )

    out = pd.DataFrame(summaries)
    race_out = pd.concat(race_outputs, ignore_index=True)
    out.to_csv(f"{args.output_prefix}_summary.csv", index=False)
    race_out.to_csv(f"{args.output_prefix}_races.csv", index=False)

    print("=== TRAFFIC ROBUSTNESS V1 ===")
    print(out.to_string(index=False))
    print("\nMost extreme controlled-OLS 150 m race slopes:")
    extreme = race_out[
        (race_out["threshold_m"].eq(150.0))
        & (race_out["specification"].eq("controlled_ols"))
    ].copy()
    print(
        extreme.assign(abs_slope=lambda d: d["race_slope"].abs())
        .nlargest(10, "abs_slope")[
            ["season_year", "race_id", "regulation_era", "race_slope"]
        ].to_string(index=False)
    )
    print(f"\nWrote {args.output_prefix}_summary.csv")
    print(f"Wrote {args.output_prefix}_races.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
