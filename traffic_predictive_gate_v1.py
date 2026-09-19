"""Predictive gate for the walk-forward traffic coefficient.

Reads traffic_walkforward_v1_races.csv produced by
validate_traffic_walkforward_v1.py and evaluates out-of-sample incremental
value with race-clustered and year-balanced bootstrap intervals.

No FastF1, database, or simulator writes.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


def bootstrap(values: np.ndarray, iterations: int, seed: int) -> tuple[float, float]:
    values = np.asarray(values, dtype=float)
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
    b = np.concatenate(draws)
    return float(np.quantile(b, .025)), float(np.quantile(b, .975))


def year_balanced_bootstrap(
    df: pd.DataFrame, column: str, iterations: int, seed: int
) -> tuple[float, float]:
    years = sorted(df["season_year"].unique())
    if len(years) < 2:
        return np.nan, np.nan
    rng = np.random.default_rng(seed)
    samples = []
    groups = {year: df[df["season_year"].eq(year)][column].to_numpy(float) for year in years}
    for _ in range(iterations):
        year_draw = rng.choice(years, size=len(years), replace=True)
        year_means = [rng.choice(groups[y], size=len(groups[y]), replace=True).mean() for y in year_draw]
        samples.append(np.mean(year_means))
    return float(np.quantile(samples, .025)), float(np.quantile(samples, .975))


def sign_test_p(values: np.ndarray) -> float:
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    values = values[values != 0]
    n = len(values)
    if n == 0:
        return np.nan
    k = int((values > 0).sum())
    # Exact two-sided binomial test under p=0.5.
    from math import comb

    probs = [comb(n, i) / (2 ** n) for i in range(n + 1)]
    observed = min(k, n - k)
    p = 2.0 * sum(probs[i] for i in range(observed + 1))
    return min(1.0, p)


def summarize(df: pd.DataFrame, label: str, threshold: float, seed: int, iterations: int) -> dict[str, object]:
    mse = df["mse_improvement_fraction"].to_numpy(float)
    mae = df["mae_improvement_fraction"].to_numpy(float)

    mse_lo, mse_hi = bootstrap(mse, iterations, seed)
    mae_lo, mae_hi = bootstrap(mae, iterations, seed + 1)
    ymse_lo, ymse_hi = year_balanced_bootstrap(df, "mse_improvement_fraction", iterations, seed + 2)
    ymae_lo, ymae_hi = year_balanced_bootstrap(df, "mae_improvement_fraction", iterations, seed + 3)

    return {
        "scope": label,
        "threshold_m": threshold,
        "races": len(df),
        "years": df["season_year"].nunique(),
        "mean_mse_improvement_pct": float(mse.mean() * 100),
        "median_mse_improvement_pct": float(np.median(mse) * 100),
        "race_bootstrap_mse_low_pct": mse_lo * 100,
        "race_bootstrap_mse_high_pct": mse_hi * 100,
        "year_balanced_mse_low_pct": ymse_lo * 100,
        "year_balanced_mse_high_pct": ymse_hi * 100,
        "mse_positive_race_rate": float((mse > 0).mean()),
        "mse_sign_test_p": sign_test_p(mse),
        "mean_mae_improvement_pct": float(mae.mean() * 100),
        "median_mae_improvement_pct": float(np.median(mae) * 100),
        "race_bootstrap_mae_low_pct": mae_lo * 100,
        "race_bootstrap_mae_high_pct": mae_hi * 100,
        "year_balanced_mae_low_pct": ymae_lo * 100,
        "year_balanced_mae_high_pct": ymae_hi * 100,
        "mae_positive_race_rate": float((mae > 0).mean()),
        "mae_sign_test_p": sign_test_p(mae),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Gate the walk-forward traffic coefficient")
    parser.add_argument("--csv", default="traffic_walkforward_v1_races.csv")
    parser.add_argument("--thresholds", default="100,150,200")
    parser.add_argument("--bootstrap-iterations", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--output", default="traffic_predictive_gate_v1.csv")
    args = parser.parse_args()

    path = Path(args.csv)
    if not path.exists():
        raise SystemExit(f"Missing walk-forward race CSV: {path}")

    df = pd.read_csv(path)
    required = {
        "season_year", "threshold_m", "mse_improvement_fraction",
        "mae_improvement_fraction", "regulation_era",
    }
    missing = sorted(required - set(df.columns))
    if missing:
        raise SystemExit(f"Missing columns: {missing}")

    rows = []
    thresholds = tuple(sorted(float(x.strip()) for x in args.thresholds.split(",") if x.strip()))

    for threshold in thresholds:
        sub = df[df["threshold_m"].eq(threshold)].copy()
        if sub.empty:
            continue

        rows.append(summarize(sub, "overall", threshold, args.seed, args.bootstrap_iterations))
        for era, era_df in sub.groupby("regulation_era", sort=True):
            rows.append(
                summarize(
                    era_df,
                    f"era:{era}",
                    threshold,
                    args.seed,
                    args.bootstrap_iterations,
                )
            )

    out = pd.DataFrame(rows)
    out.to_csv(args.output, index=False)

    print("=== TRAFFIC PREDICTIVE GATE V1 ===")
    print(out.to_string(index=False))
    print("\nYear-by-year 150 m race-mean improvements:")
    sub = df[df["threshold_m"].eq(150.0)]
    print(
        sub.groupby(["regulation_era", "season_year"], as_index=False).agg(
            races=("race_id", "nunique"),
            mse_improvement_pct=("mse_improvement_fraction", lambda s: s.mean() * 100),
            mae_improvement_pct=("mae_improvement_fraction", lambda s: s.mean() * 100),
            mse_positive_rate=("mse_improvement_fraction", lambda s: (s > 0).mean()),
            mae_positive_rate=("mae_improvement_fraction", lambda s: (s > 0).mean()),
        ).to_string(index=False)
    )
    print(f"\nWrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
