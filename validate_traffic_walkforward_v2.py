"""Leakage-safe traffic coefficient validation v2.

The v1 continuous walk-forward validator residualized each target stint using
the full target stint, which can use future target-race laps. This v2 avoids
that by using adjacent-lap first differences within driver/stint/compound.

For each target year:
- train beta only on races from earlier years in the same regulation era
- for each target race, predict change in field-relative residual from change
  in close-following exposure
- compare against a zero-change baseline
- bootstrap at the race level

This validates an out-of-sample conditional association, not a pre-race
strategy forecast. Target traffic exposure is observed race-state input.
No FastF1, DB, or simulator mutation.
"""

from __future__ import annotations

import argparse
from pathlib import Path

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
            if e.lap.tyre_age is None or e.lap.stint_number is None or e.lap.compound is None:
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
                    "stint_number": int(e.lap.stint_number),
                    "compound": str(e.lap.compound),
                    "lap_number": int(e.lap.lap_number),
                    "x": float(x),
                    "y": float(y),
                }
            )
    return pd.DataFrame(rows)


def first_differences(df: pd.DataFrame, *, min_pairs: int = 5) -> pd.DataFrame:
    rows = []
    for key, g in df.groupby(GROUPS, sort=False, dropna=False):
        g = g.sort_values("lap_number").drop_duplicates("lap_number")
        if len(g) < min_pairs + 1:
            continue
        lap_values = g["lap_number"].to_numpy(int)
        dx_raw = g["x"].to_numpy(float)[1:] - g["x"].to_numpy(float)[:-1]
        dy_raw = g["y"].to_numpy(float)[1:] - g["y"].to_numpy(float)[:-1]
        lap2 = lap_values[1:]
        consecutive = lap_values[1:] == lap_values[:-1] + 1
        keep = (
            consecutive
            & np.isfinite(dx_raw)
            & np.isfinite(dy_raw)
            & (np.abs(dx_raw) > 1e-5)
        )
        dx = dx_raw
        dy = dy_raw
        if keep.sum() < min_pairs:
            continue

        rows.append(
            pd.DataFrame(
                {
                    "race_id": key[0],
                    "season_year": key[1],
                    "regulation_era": key[2],
                    "driver_key": key[3],
                    "stint_number": key[4],
                    "compound": key[5],
                    "lap_number": lap2[keep],
                    "dx": dx[keep],
                    "dy": dy[keep],
                }
            )
        )

    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def fit_beta(train: pd.DataFrame) -> float | None:
    if train.empty:
        return None
    x = train["dx"].to_numpy(float)
    y = train["dy"].to_numpy(float)
    denom = float(np.dot(x, x))
    if denom <= 1e-10:
        return None
    beta = float(np.dot(x, y) / denom)
    return beta if np.isfinite(beta) else None


def race_metrics(target: pd.DataFrame, beta: float) -> tuple[float, float]:
    x = target["dx"].to_numpy(float)
    y = target["dy"].to_numpy(float)
    pred = beta * x
    mae_model = float(np.mean(np.abs(y - pred)))
    mae_null = float(np.mean(np.abs(y)))
    return mae_model, mae_null


def bootstrap(values: np.ndarray, iterations: int, seed: int) -> tuple[float, float]:
    if len(values) < 2:
        return np.nan, np.nan
    rng = np.random.default_rng(seed)
    out = []
    remaining = iterations
    while remaining:
        n = min(1000, remaining)
        idx = rng.integers(0, len(values), size=(n, len(values)))
        out.append(values[idx].mean(axis=1))
        remaining -= n
    b = np.concatenate(out)
    return float(np.quantile(b, .025)), float(np.quantile(b, .975))


def main() -> int:
    parser = argparse.ArgumentParser(description="Leakage-safe traffic walk-forward v2")
    parser.add_argument("--checkpoint-dir", default="traffic_checkpoints_v1")
    parser.add_argument("--thresholds", default="100,150,200")
    parser.add_argument("--start-target-year", type=int, default=2019)
    parser.add_argument("--end-target-year", type=int, default=2025)
    parser.add_argument("--min-training-pairs", type=int, default=100)
    parser.add_argument("--bootstrap-iterations", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--output-prefix", default="traffic_walkforward_v2")
    args = parser.parse_args()

    thresholds = tuple(sorted(float(x.strip()) for x in args.thresholds.split(",") if x.strip()))
    summaries = []
    race_outputs = []

    for threshold in thresholds:
        raw = load_rows(args.checkpoint_dir, threshold)
        diffs = first_differences(raw)
        if diffs.empty:
            continue

        for year in range(args.start_target_year, args.end_target_year + 1):
            target_all = diffs[diffs["season_year"].eq(year)]
            train_all = diffs[diffs["season_year"].lt(year)]
            if target_all.empty:
                continue

            for era, target_era in target_all.groupby("regulation_era", sort=True):
                train_era = train_all[train_all["regulation_era"].eq(era)]
                if len(train_era) < args.min_training_pairs:
                    continue

                beta = fit_beta(train_era)
                if beta is None:
                    continue

                race_rows = []
                for race_id, race in target_era.groupby("race_id", sort=False):
                    if len(race) < 5:
                        continue
                    mae_model, mae_null = race_metrics(race, beta)
                    improvement = 1.0 - mae_model / mae_null if mae_null > 0 else np.nan
                    race_rows.append(
                        {
                            "race_id": int(race_id),
                            "season_year": year,
                            "regulation_era": str(era),
                            "threshold_m": threshold,
                            "training_pairs": len(train_era),
                            "frozen_beta_seconds_per_exposure_change": beta,
                            "frozen_beta_ms_per_10pct_change": beta * 100.0,
                            "n_difference_pairs": len(race),
                            "traffic_mae": mae_model,
                            "null_mae": mae_null,
                            "mae_improvement_fraction": improvement,
                        }
                    )

                race_df = pd.DataFrame(race_rows)
                if race_df.empty:
                    continue

                v = race_df["mae_improvement_fraction"].to_numpy(float)
                lo, hi = bootstrap(v, args.bootstrap_iterations, args.seed + year)

                summaries.append(
                    {
                        "target_year": year,
                        "regulation_era": str(era),
                        "threshold_m": threshold,
                        "training_pairs": len(train_era),
                        "target_races": len(race_df),
                        "frozen_beta_ms_per_10pct_change": beta * 100.0,
                        "mean_mae_improvement_pct": float(v.mean() * 100),
                        "bootstrap_95ci_low_pct": lo * 100,
                        "bootstrap_95ci_high_pct": hi * 100,
                        "race_mae_win_rate": float((v > 0).mean()),
                    }
                )
                race_outputs.append(race_df)

    summary = pd.DataFrame(summaries)
    races = pd.concat(race_outputs, ignore_index=True) if race_outputs else pd.DataFrame()

    summary.to_csv(f"{args.output_prefix}_summary.csv", index=False)
    races.to_csv(f"{args.output_prefix}_races.csv", index=False)

    print("=== LEAKAGE-SAFE TRAFFIC WALK-FORWARD V2 ===")
    print(summary.to_string(index=False))
    print("\nPooled by era/threshold:")
    if not summary.empty:
        print(
            summary.groupby(["regulation_era", "threshold_m"], as_index=False).agg(
                target_years=("target_year", "count"),
                mean_mae_improvement_pct=("mean_mae_improvement_pct", "mean"),
                mean_race_mae_win_rate=("race_mae_win_rate", "mean"),
            ).to_string(index=False)
        )
    print(f"\nWrote {args.output_prefix}_summary.csv")
    print(f"Wrote {args.output_prefix}_races.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
