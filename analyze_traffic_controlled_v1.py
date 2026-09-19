"""Confounder-controlled continuous traffic analysis v1.

Exploratory research analysis using persisted traffic checkpoints. Unlike the
simple within-stint slope, this version controls for lap progression within a
stint (linear and quadratic) and reports how sensitive the traffic coefficient
is to that control.

The analysis remains observational; it does not establish a causal traffic
penalty and does not modify the simulator/database.
"""

from __future__ import annotations

import argparse
import numpy as np
import pandas as pd
from pathlib import Path

from audit_race_strategy_traffic_v1 import _load_checkpoint


def load_rows(checkpoint_dir: str, threshold: float) -> pd.DataFrame:
    rows = []
    root = Path(checkpoint_dir)
    for p in sorted(root.glob("*.json")):
        exposures, _ = _load_checkpoint(str(p))
        for e in exposures:
            x = e.close_fraction(threshold)
            y = e.lap.field_relative_residual
            age = e.lap.tyre_age
            lap = e.lap.lap_number
            if age is None or not np.isfinite(x) or not np.isfinite(y):
                continue
            rows.append({
                "race_id": e.lap.race_id,
                "season_year": e.lap.season_year,
                "regulation_era": e.lap.regulation_era,
                "driver_key": e.lap.driver_key,
                "stint_number": e.lap.stint_number,
                "compound": e.lap.compound,
                "lap_number": lap,
                "tyre_age": float(age),
                "close_fraction": float(x),
                "residual_seconds": float(y),
            })
    df = pd.DataFrame(rows)
    if df.empty:
        raise SystemExit("No usable checkpoint rows found")
    return df


def fit_stint(g: pd.DataFrame, *, controlled: bool) -> float | None:
    if len(g) < 6:
        return None

    x = g["close_fraction"].to_numpy(float)
    y = g["residual_seconds"].to_numpy(float)
    if np.ptp(x) < 0.10:
        return None

    lap = g["lap_number"].to_numpy(float)
    progress = (lap - lap.min()) / max(1.0, lap.max() - lap.min())
    cols = [np.ones(len(g)), x]
    if controlled:
        cols.extend([progress, progress * progress])

    X = np.column_stack(cols)
    try:
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    except np.linalg.LinAlgError:
        return None

    coef = float(beta[1])
    return coef if np.isfinite(coef) else None


def build_stint_slopes(df: pd.DataFrame, *, controlled: bool) -> pd.DataFrame:
    rows = []
    keys = ["race_id", "season_year", "regulation_era", "driver_key", "stint_number", "compound"]

    for key, g in df.groupby(keys, sort=False, dropna=False):
        coef = fit_stint(g, controlled=controlled)
        if coef is None:
            continue
        rows.append({
            "race_id": int(key[0]),
            "season_year": int(key[1]),
            "regulation_era": str(key[2]),
            "driver_key": str(key[3]),
            "stint_number": int(key[4]),
            "compound": str(key[5]),
            "n_laps": len(g),
            "close_fraction_span": float(g["close_fraction"].max() - g["close_fraction"].min()),
            "slope_seconds_per_full_exposure": coef,
            "slope_ms_per_10pct": coef * 100.0,
            "controlled": controlled,
        })
    return pd.DataFrame(rows)


def race_aggregate(stints: pd.DataFrame) -> pd.DataFrame:
    if stints.empty:
        return pd.DataFrame()
    return (
        stints.groupby(["race_id", "season_year", "regulation_era"], as_index=False)
        ["slope_seconds_per_full_exposure"]
        .mean()
        .rename(columns={"slope_seconds_per_full_exposure": "race_slope_seconds"})
    )


def bootstrap_ci(values: np.ndarray, iterations: int, seed: int) -> tuple[float, float]:
    if len(values) < 2:
        return np.nan, np.nan
    rng = np.random.default_rng(seed)
    out = []
    left = iterations
    while left:
        n = min(1000, left)
        idx = rng.integers(0, len(values), size=(n, len(values)))
        out.append(values[idx].mean(axis=1))
        left -= n
    b = np.concatenate(out)
    return float(np.quantile(b, 0.025)), float(np.quantile(b, 0.975))


def summary(races: pd.DataFrame, label: str, threshold: float, iterations: int, seed: int) -> dict[str, object]:
    v = races["race_slope_seconds"].to_numpy(float)
    n = len(v)
    m = float(v.mean()) if n else np.nan
    sd = float(v.std(ddof=1)) if n > 1 else np.nan
    se = sd / np.sqrt(n) if n > 1 else np.nan
    lo, hi = bootstrap_ci(v, iterations, seed)
    return {
        "specification": label,
        "threshold_m": threshold,
        "races": n,
        "mean_slope_seconds": m,
        "effect_ms_per_10pct_exposure": m * 100 if np.isfinite(m) else np.nan,
        "race_clustered_se": se,
        "bootstrap_95ci_low": lo,
        "bootstrap_95ci_high": hi,
        "positive_race_rate": float((v > 0).mean()) if n else np.nan,
        "median_race_slope_seconds": float(np.median(v)) if n else np.nan,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint-dir", default="traffic_checkpoints_v1")
    parser.add_argument("--thresholds", default="100,150,200")
    parser.add_argument("--bootstrap-iterations", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--output-prefix", default="traffic_controlled_v1")
    args = parser.parse_args()

    thresholds = tuple(sorted({float(x.strip()) for x in args.thresholds.split(",") if x.strip()}))
    all_summaries = []
    all_races = []
    for threshold in thresholds:
        df = load_rows(args.checkpoint_dir, threshold)
        raw = build_stint_slopes(df, controlled=False)
        controlled = build_stint_slopes(df, controlled=True)
        for spec, stints in [("raw_within_stint", raw), ("lap_progress_controlled", controlled)]:
            races = race_aggregate(stints)
            if races.empty:
                continue
            for era, era_df in races.groupby("regulation_era", sort=True):
                all_summaries.append(
                    summary(era_df, f"{spec}:era:{era}", threshold, args.bootstrap_iterations, args.seed)
                )
            all_summaries.append(
                summary(races, f"{spec}:overall", threshold, args.bootstrap_iterations, args.seed)
            )
            races = races.copy()
            races["specification"] = spec
            races["threshold_m"] = threshold
            all_races.append(races)

    out = pd.DataFrame(all_summaries)
    race_out = pd.concat(all_races, ignore_index=True)
    out.to_csv(f"{args.output_prefix}_summary.csv", index=False)
    race_out.to_csv(f"{args.output_prefix}_races.csv", index=False)

    print("=== CONTROLLED TRAFFIC ANALYSIS V1 ===")
    print(out.to_string(index=False))
    print("\nMost extreme 150 m controlled race slopes:")
    print(
        race_out[
            (race_out["threshold_m"].eq(150.0))
            & (race_out["specification"].eq("lap_progress_controlled"))
        ]
        .assign(abs_slope=lambda d: d["race_slope_seconds"].abs())
        .nlargest(10, "abs_slope")[
            ["season_year", "race_id", "regulation_era", "race_slope_seconds"]
        ]
        .to_string(index=False)
    )
    print(f"\nWrote {args.output_prefix}_summary.csv")
    print(f"Wrote {args.output_prefix}_races.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
