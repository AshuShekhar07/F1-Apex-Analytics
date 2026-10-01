"""Walk-forward validation of the traffic exposure coefficient.

For each target race, estimate the traffic coefficient using only earlier
races in the same regulation era. Apply the frozen coefficient to target-race
within-stint exposure after removing target-stint lap progression from both
exposure and pace residual. Compare traffic-aware prediction with a no-traffic
baseline.

This validates whether a coefficient learned before the target season carries
to unseen races. It is still a research effect validation, not a pre-race
strategy forecast: target-race traffic exposure is used as an observed state
variable. No simulator/database/FastF1 mutation occurs.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from collections import defaultdict

import numpy as np
import pandas as pd

from audit_race_strategy_traffic_v1 import _load_checkpoint


GROUPS = [
    "race_id", "season_year", "regulation_era",
    "driver_key", "stint_number", "compound",
]


def load_rows(checkpoint_dir: str, threshold: float) -> pd.DataFrame:
    rows = []
    for path in sorted(Path(checkpoint_dir).glob("*.json")):
        exposures, _ = _load_checkpoint(str(path))
        for e in exposures:
            if e.lap.tyre_age is None:
                continue
            x = e.close_fraction(threshold)
            y = e.lap.field_relative_residual
            if not np.isfinite(x) or not np.isfinite(y):
                continue
            rows.append(
                {
                    "race_id": int(e.lap.race_id),
                    "season_year": int(e.lap.season_year),
                    "regulation_era": str(e.lap.regulation_era),
                    "driver_key": str(e.lap.driver_key),
                    "stint_number": (
                        int(e.lap.stint_number)
                        if e.lap.stint_number is not None else -1
                    ),
                    "compound": str(e.lap.compound),
                    "lap_number": int(e.lap.lap_number),
                    "x": float(x),
                    "y": float(y),
                }
            )
    df = pd.DataFrame(rows)
    if df.empty:
        raise SystemExit("No checkpoint rows available")
    return df


def residualized(df: pd.DataFrame) -> pd.DataFrame:
    out = []
    for _, g in df.groupby(GROUPS, sort=False, dropna=False):
        if len(g) < 8 or g["x"].max() - g["x"].min() < 0.15:
            continue
        g = g.sort_values("lap_number").copy()
        p = (g["lap_number"].to_numpy(float) - g["lap_number"].min()) / max(
            1.0, g["lap_number"].max() - g["lap_number"].min()
        )
        Xp = np.column_stack([np.ones(len(g)), p, p * p])
        x = g["x"].to_numpy(float)
        y = g["y"].to_numpy(float)
        bx, *_ = np.linalg.lstsq(Xp, x, rcond=None)
        by, *_ = np.linalg.lstsq(Xp, y, rcond=None)
        out.append(
            g.assign(
                x_resid=x - Xp @ bx,
                y_resid=y - Xp @ by,
            )
        )
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def fit_beta(train: pd.DataFrame) -> float | None:
    if train.empty:
        return None
    x = train["x_resid"].to_numpy(float)
    y = train["y_resid"].to_numpy(float)
    denom = float(np.dot(x, x))
    if denom <= 1e-10:
        return None
    beta = float(np.dot(x, y) / denom)
    return beta if np.isfinite(beta) else None


def target_metrics(target: pd.DataFrame, beta: float) -> tuple[float, float, float, float, int]:
    x = target["x_resid"].to_numpy(float)
    y = target["y_resid"].to_numpy(float)
    pred = beta * x
    null_pred = np.zeros_like(y)

    traffic_mse = float(np.mean((y - pred) ** 2))
    null_mse = float(np.mean((y - null_pred) ** 2))
    traffic_mae = float(np.mean(np.abs(y - pred)))
    null_mae = float(np.mean(np.abs(y - null_pred)))
    return traffic_mse, null_mse, traffic_mae, null_mae, len(y)


def bootstrap_race_mean(values: np.ndarray, iterations: int, seed: int) -> tuple[float, float]:
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint-dir", default="traffic_checkpoints_v1")
    parser.add_argument("--thresholds", default="100,150,200")
    parser.add_argument("--start-target-year", type=int, default=2019)
    parser.add_argument("--end-target-year", type=int, default=2025)
    parser.add_argument("--bootstrap-iterations", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--output-prefix", default="traffic_walkforward_v1")
    args = parser.parse_args()

    thresholds = tuple(sorted({float(x.strip()) for x in args.thresholds.split(",") if x.strip()}))
    summaries = []
    race_rows = []

    for threshold in thresholds:
        raw = load_rows(args.checkpoint_dir, threshold)
        residual_df = residualized(raw)
        if residual_df.empty:
            continue

        for target_year in range(args.start_target_year, args.end_target_year + 1):
            target_all = residual_df[residual_df["season_year"].eq(target_year)]
            train_all = residual_df[residual_df["season_year"].lt(target_year)]

            for era, target in target_all.groupby("regulation_era", sort=True):
                train = train_all[train_all["regulation_era"].eq(era)]
                beta = fit_beta(train)

                if beta is None:
                    continue

                race_stats = []
                for race_id, race in target.groupby("race_id", sort=False):
                    mse_t, mse_0, mae_t, mae_0, n = target_metrics(race, beta)
                    race_stats.append(
                        {
                            "race_id": int(race_id),
                            "season_year": int(target_year),
                            "regulation_era": str(era),
                            "threshold_m": threshold,
                            "beta_seconds_per_full_exposure": beta,
                            "beta_ms_per_10pct": beta * 100.0,
                            "n_laps": n,
                            "traffic_mse": mse_t,
                            "null_mse": mse_0,
                            "traffic_mae": mae_t,
                            "null_mae": mae_0,
                            "mse_improvement_fraction": (
                                1.0 - mse_t / mse_0 if mse_0 > 0 else np.nan
                            ),
                            "mae_improvement_fraction": (
                                1.0 - mae_t / mae_0 if mae_0 > 0 else np.nan
                            ),
                        }
                    )

                races = pd.DataFrame(race_stats)
                if races.empty:
                    continue

                mse_improve = races["mse_improvement_fraction"].to_numpy(float)
                mae_improve = races["mae_improvement_fraction"].to_numpy(float)
                mse_lo, mse_hi = bootstrap_race_mean(
                    mse_improve, args.bootstrap_iterations, args.seed + target_year
                )
                mae_lo, mae_hi = bootstrap_race_mean(
                    mae_improve, args.bootstrap_iterations, args.seed + target_year + 100
                )

                summaries.append(
                    {
                        "target_year": target_year,
                        "regulation_era": str(era),
                        "threshold_m": threshold,
                        "training_races": int(train["race_id"].nunique()),
                        "target_races": int(races["race_id"].nunique()),
                        "frozen_beta_ms_per_10pct": beta * 100.0,
                        "mean_mse_improvement_fraction": float(mse_improve.mean()),
                        "bootstrap_mse_improvement_low": mse_lo,
                        "bootstrap_mse_improvement_high": mse_hi,
                        "mean_mae_improvement_fraction": float(mae_improve.mean()),
                        "bootstrap_mae_improvement_low": mae_lo,
                        "bootstrap_mae_improvement_high": mae_hi,
                        "race_mse_win_rate": float((mse_improve > 0).mean()),
                        "race_mae_win_rate": float((mae_improve > 0).mean()),
                    }
                )
                race_rows.append(races)

    summary = pd.DataFrame(summaries)
    races = pd.concat(race_rows, ignore_index=True) if race_rows else pd.DataFrame()
    summary.to_csv(f"{args.output_prefix}_summary.csv", index=False)
    races.to_csv(f"{args.output_prefix}_races.csv", index=False)

    print("=== TRAFFIC WALK-FORWARD V1 ===")
    print(summary.to_string(index=False))
    print("\nPooled target-year results (race-weighted equally by target race):")
    if not summary.empty:
        print(
            summary.groupby(["regulation_era", "threshold_m"], as_index=False).agg(
                target_years=("target_year", "count"),
                mean_mse_improvement=("mean_mse_improvement_fraction", "mean"),
                mean_mae_improvement=("mean_mae_improvement_fraction", "mean"),
                mean_mse_win_rate=("race_mse_win_rate", "mean"),
                mean_mae_win_rate=("race_mae_win_rate", "mean"),
            ).to_string(index=False)
        )
    print(f"\nWrote {args.output_prefix}_summary.csv")
    print(f"Wrote {args.output_prefix}_races.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
