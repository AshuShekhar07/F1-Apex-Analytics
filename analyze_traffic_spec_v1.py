"""Traffic specification reconciliation v1.

Tests whether the continuous traffic association survives non-parametric/
rank-based exposure comparisons after removing within-stint lap progression.

Methods:
1. Partial residual slope: residualize traffic exposure and pace residual on
   within-stint progress (linear + quadratic), then regress residual pace on
   residual exposure.
2. Progress-adjusted tertile contrast: within each stint, split exposure into
   low/mid/high thirds and compare progress-adjusted pace residual between high
   and low exposure.
3. Winsorized race-level partial-residual slope as an outlier diagnostic.

All estimates are race-balanced and bootstrap at the race level.
Research-only: no causal traffic penalty and no simulator/database mutation.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from audit_race_strategy_traffic_v1 import _load_checkpoint


GROUPS = ["race_id", "season_year", "regulation_era", "driver_key", "stint_number", "compound"]


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
            rows.append({
                "race_id": int(e.lap.race_id),
                "season_year": int(e.lap.season_year),
                "regulation_era": str(e.lap.regulation_era),
                "driver_key": str(e.lap.driver_key),
                "stint_number": int(e.lap.stint_number) if e.lap.stint_number is not None else -1,
                "compound": str(e.lap.compound),
                "lap_number": int(e.lap.lap_number),
                "x": float(x),
                "y": float(y),
            })
    return pd.DataFrame(rows)


def residualize_progress(g: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    g = g.sort_values("lap_number")
    p = (g["lap_number"].to_numpy(float) - g["lap_number"].min()) / max(
        1.0, g["lap_number"].max() - g["lap_number"].min()
    )
    X = np.column_stack([np.ones(len(g)), p, p * p])
    x = g["x"].to_numpy(float)
    y = g["y"].to_numpy(float)

    bx, *_ = np.linalg.lstsq(X, x, rcond=None)
    by, *_ = np.linalg.lstsq(X, y, rcond=None)
    return x - X @ bx, y - X @ by


def stint_estimates(df: pd.DataFrame, min_n: int, min_span: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    slopes = []
    contrasts = []

    for key, g0 in df.groupby(GROUPS, sort=False, dropna=False):
        g = g0.sort_values("lap_number").reset_index(drop=True)
        if len(g) < min_n or g["x"].max() - g["x"].min() < min_span:
            continue

        rx, ry = residualize_progress(g)
        denom = float(np.dot(rx, rx))
        if denom <= 1e-10:
            continue

        slope = float(np.dot(rx, ry) / denom)
        n = len(g)

        # Residualized x keeps the comparison focused on exposure variation not
        # explained by deterministic lap progression.
        tertile = pd.qcut(pd.Series(rx), 3, labels=False, duplicates="drop")
        if tertile.nunique() < 2:
            continue
        low = ry[tertile.to_numpy() == tertile.min()]
        high = ry[tertile.to_numpy() == tertile.max()]
        if len(low) < 2 or len(high) < 2:
            continue

        slopes.append({
            "race_id": key[0], "season_year": key[1], "regulation_era": key[2],
            "driver_key": key[3], "stint_number": key[4], "compound": key[5],
            "n_laps": n, "slope": slope,
        })
        contrasts.append({
            "race_id": key[0], "season_year": key[1], "regulation_era": key[2],
            "driver_key": key[3], "stint_number": key[4], "compound": key[5],
            "n_laps": n, "high_minus_low": float(np.mean(high) - np.mean(low)),
        })

    return pd.DataFrame(slopes), pd.DataFrame(contrasts)


def race_means(stints: pd.DataFrame, column: str, name: str) -> pd.DataFrame:
    if stints.empty:
        return pd.DataFrame()
    return (
        stints.groupby(["race_id", "season_year", "regulation_era"], as_index=False)[column]
        .mean()
        .rename(columns={column: name})
    )


def bootstrap(v: np.ndarray, iterations: int, seed: int) -> tuple[float, float]:
    if len(v) < 2:
        return np.nan, np.nan
    rng = np.random.default_rng(seed)
    samples = []
    left = iterations
    while left:
        n = min(1000, left)
        idx = rng.integers(0, len(v), size=(n, len(v)))
        samples.append(v[idx].mean(axis=1))
        left -= n
    b = np.concatenate(samples)
    return float(np.quantile(b, .025)), float(np.quantile(b, .975))


def summarize(race_df: pd.DataFrame, value_col: str, spec: str, threshold: float, iterations: int, seed: int) -> dict[str, object]:
    v = race_df[value_col].to_numpy(float)
    n = len(v)
    m = float(v.mean()) if n else np.nan
    lo, hi = bootstrap(v, iterations, seed)
    return {
        "specification": spec,
        "threshold_m": threshold,
        "races": n,
        "mean_effect_ms_per_10pct": m * 100,
        "median_race_effect_ms_per_10pct": float(np.median(v) * 100) if n else np.nan,
        "bootstrap_95ci_low_ms_per_10pct": lo * 100,
        "bootstrap_95ci_high_ms_per_10pct": hi * 100,
        "positive_race_rate": float((v > 0).mean()) if n else np.nan,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint-dir", default="traffic_checkpoints_v1")
    parser.add_argument("--thresholds", default="100,150,200")
    parser.add_argument("--min-n", type=int, default=8)
    parser.add_argument("--min-span", type=float, default=.15)
    parser.add_argument("--bootstrap-iterations", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--output-prefix", default="traffic_spec_v1")
    args = parser.parse_args()

    thresholds = tuple(sorted({float(x.strip()) for x in args.thresholds.split(",") if x.strip()}))
    summaries = []
    race_outputs = []

    for threshold in thresholds:
        df = load_rows(args.checkpoint_dir, threshold)
        slopes, contrasts = stint_estimates(df, args.min_n, args.min_span)

        for spec, stints, col, scale in [
            ("partial_residual_slope", slopes, "slope", 100),
            ("progress_adjusted_tertile_contrast", contrasts, "high_minus_low", 1),
        ]:
            races = race_means(stints, col, "race_effect")
            if races.empty:
                continue

            for era, era_df in races.groupby("regulation_era", sort=True):
                summaries.append(
                    summarize(
                        era_df, "race_effect", f"{spec}:era:{era}",
                        threshold, args.bootstrap_iterations, args.seed,
                    )
                )
            summaries.append(
                summarize(
                    races, "race_effect", f"{spec}:overall",
                    threshold, args.bootstrap_iterations, args.seed,
                )
            )
            race_outputs.append(races.assign(threshold_m=threshold, specification=spec))

    out = pd.DataFrame(summaries)
    race_out = pd.concat(race_outputs, ignore_index=True)
    # Tertile contrast is expressed as ms/lap between high and low exposure;
    # partial slope is expressed as ms/lap for a full 1.0 exposure increase.
    out.to_csv(f"{args.output_prefix}_summary.csv", index=False)
    race_out.to_csv(f"{args.output-prefix}_races.csv".replace("-", "_"), index=False)

    print("=== TRAFFIC SPECIFICATION RECONCILIATION V1 ===")
    print(out.to_string(index=False))
    print("\n150 m specification comparison:")
    print(
        out[(out["threshold_m"].eq(150.0)) & (out["specification"].str.contains("overall"))]
        .to_string(index=False)
    )
    print(f"\nWrote {args.output_prefix}_summary.csv")
    print(f"Wrote {args.output_prefix}_races.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
