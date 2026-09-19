"""Traffic effect validation v2.

Research-only post-hoc analysis of the completed traffic exposure CSV.
No FastF1 telemetry is fetched and no simulator/database state is changed.

Outputs threshold/era-aware uncertainty, race-level stability, and matched-vs-
unmatched diagnostics for the 100/150/200 m sensitivity analysis.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

DEFAULT_INPUT = "traffic_exposure_v1_2018_2025.csv"
DEFAULT_OUT = "traffic_validation_v2"


def _numeric(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    out = df.copy()
    for c in cols:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    return out


def reconstruct_close_matches(
    df: pd.DataFrame,
    *,
    threshold: float,
    min_close_fraction: float = 0.25,
    min_ahead_coverage: float = 0.50,
    max_lap_distance: int = 10,
    max_tyre_age_diff: int = 1,
    max_clean_air_reuse: int = 1,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Reproduce the audit matching logic for one threshold.

    Returns:
      close_rows: one row per classified close lap, including matched/unmatched.
      matches: one row per successfully matched close/clean pair.
    """
    needed = {
        "race_id", "driver_key", "stint_number", "compound", "tyre_age",
        "lap_number", "traffic_state", "field_relative_residual_seconds",
        "regulation_era", "season_year", "valid_seconds", "known_ahead_fraction",
        "close_fraction", "sustained_close_seconds",
    }
    missing = sorted(needed - set(df.columns))
    if missing:
        raise ValueError(f"Missing exposure columns: {missing}")

    work = _numeric(
        df,
        [
            "race_id", "stint_number", "tyre_age", "lap_number",
            "field_relative_residual_seconds", "season_year", "valid_seconds",
            "known_ahead_fraction", "close_fraction", "sustained_close_seconds",
        ],
    ).copy()
    work["driver_key"] = work["driver_key"].astype(str)
    work["compound"] = work["compound"].astype(str).str.upper()

    classified = work[
        work["valid_seconds"].gt(0)
        & work["known_ahead_fraction"].ge(min_ahead_coverage)
    ].copy()
    # Reclassify from the exposure's actual continuous fraction instead of using
    # the CSV's 150 m state, because v2 evaluates 100/150/200 m independently.
    threshold_col = f"close_fraction_{int(threshold)}m"
    # The source CSV stores only the primary close_fraction. The threshold-specific
    # durations are not exported separately, so use the summary CSV for the exact
    # aggregate estimates and reconstruct pair-level matching only for 150 m.
    # For 100/200 m we therefore report the persisted audit aggregates rather than
    # inventing per-pair classifications.
    if threshold != 150.0:
        return pd.DataFrame(), pd.DataFrame()

    classified["traffic_state_150m"] = np.where(
        classified["close_fraction"].ge(min_close_fraction), "close", "clear"
    )

    groups = classified[
        classified["stint_number"].notna()
        & classified["compound"].notna()
        & classified["tyre_age"].notna()
    ].groupby(["race_id", "driver_key", "stint_number", "compound"], sort=False)

    close_rows: list[dict[str, object]] = []
    matches: list[dict[str, object]] = []
    for _, group in groups:
        ordered = group.sort_values("lap_number")
        close = ordered[ordered["traffic_state_150m"].eq("close")]
        clear = ordered[ordered["traffic_state_150m"].eq("clear")]
        reuse: Counter[int] = Counter()

        for _, cr in close.iterrows():
            candidates = clear[
                ((clear["tyre_age"] - cr["tyre_age"]).abs() <= max_tyre_age_diff)
                & ((clear["lap_number"] - cr["lap_number"]).abs() <= max_lap_distance)
            ].sort_values(
                by=["tyre_age", "lap_number"],
                key=lambda s: s
            )
            # Exact audit priority: smallest tyre-age difference, then lap distance,
            # then lap number. Build the sortable keys explicitly to avoid changing
            # the deterministic choice.
            if not candidates.empty:
                candidates = candidates.assign(
                    _age_diff=(candidates["tyre_age"] - cr["tyre_age"]).abs(),
                    _lap_diff=(candidates["lap_number"] - cr["lap_number"]).abs(),
                ).sort_values(["_age_diff", "_lap_diff", "lap_number"])

            chosen = None
            if not candidates.empty:
                for _, clr in candidates.iterrows():
                    lp = int(clr["lap_number"])
                    if reuse[lp] < max_clean_air_reuse:
                        chosen = clr
                        break

            base = cr.to_dict()
            base["match_status"] = "matched_close" if chosen is not None else "unmatched_close"
            if chosen is not None:
                base["clean_lap_number"] = int(chosen["lap_number"])
                base["traffic_delta_seconds"] = (
                    float(cr["field_relative_residual_seconds"])
                    - float(chosen["field_relative_residual_seconds"])
                )
                reuse[int(chosen["lap_number"])] += 1
                matches.append(
                    {
                        "race_id": int(cr["race_id"]),
                        "season_year": int(cr["season_year"]),
                        "regulation_era": str(cr["regulation_era"]),
                        "driver_key": str(cr["driver_key"]),
                        "stint_number": int(cr["stint_number"]),
                        "close_lap": int(cr["lap_number"]),
                        "clean_lap": int(chosen["lap_number"]),
                        "traffic_delta_seconds": float(base["traffic_delta_seconds"]),
                        "close_fraction": float(cr["close_fraction"]),
                        "sustained_close_seconds": float(cr["sustained_close_seconds"]),
                        "close_tyre_age": int(cr["tyre_age"]),
                        "clean_tyre_age": int(chosen["tyre_age"]),
                    }
                )
            close_rows.append(base)

    return pd.DataFrame(close_rows), pd.DataFrame(matches)


def race_level_effects(matches: pd.DataFrame) -> pd.DataFrame:
    if matches.empty:
        return pd.DataFrame(
            columns=["race_id", "season_year", "regulation_era", "race_mean_delta_seconds"]
        )
    stint = (
        matches.groupby(["race_id", "driver_key", "stint_number"], as_index=False)
        ["traffic_delta_seconds"].mean()
        .rename(columns={"traffic_delta_seconds": "stint_mean_delta"})
    )
    race = (
        stint.groupby("race_id", as_index=False)["stint_mean_delta"].mean()
        .rename(columns={"stint_mean_delta": "race_mean_delta_seconds"})
    )
    meta = matches[["race_id", "season_year", "regulation_era"]].drop_duplicates("race_id")
    return race.merge(meta, on="race_id", how="left").sort_values(["season_year", "race_id"])


def bootstrap_ci(values: np.ndarray, iterations: int, seed: int) -> tuple[float, float]:
    if len(values) < 2:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(values), size=(iterations, len(values)))
    means = values[idx].mean(axis=1)
    return float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def summarize_races(race_df: pd.DataFrame, scope: str, iterations: int, seed: int) -> dict[str, object]:
    v = race_df["race_mean_delta_seconds"].to_numpy(float)
    n = len(v)
    m = float(v.mean()) if n else float("nan")
    med = float(np.median(v)) if n else float("nan")
    sd = float(v.std(ddof=1)) if n > 1 else float("nan")
    se = sd / np.sqrt(n) if n > 1 else float("nan")
    lo, hi = bootstrap_ci(v, iterations, seed)
    return {
        "scope": scope,
        "races": n,
        "mean_seconds": m,
        "median_seconds": med,
        "race_sd_seconds": sd,
        "clustered_se_seconds": se,
        "bootstrap_95ci_low_seconds": lo,
        "bootstrap_95ci_high_seconds": hi,
        "positive_race_rate": float((v > 0).mean()) if n else float("nan"),
        "negative_race_rate": float((v < 0).mean()) if n else float("nan"),
        "min_race_seconds": float(v.min()) if n else float("nan"),
        "max_race_seconds": float(v.max()) if n else float("nan"),
    }


def unmatched_summary(close_rows: pd.DataFrame) -> pd.DataFrame:
    if close_rows.empty:
        return pd.DataFrame()
    close_rows = close_rows.copy()
    group_cols = ["race_id", "driver_key", "stint_number", "compound"]
    close_rows["stint_min_lap"] = close_rows.groupby(group_cols)["lap_number"].transform("min")
    close_rows["stint_max_lap"] = close_rows.groupby(group_cols)["lap_number"].transform("max")
    span = (close_rows["stint_max_lap"] - close_rows["stint_min_lap"]).replace(0, np.nan)
    close_rows["stint_progress"] = (
        (close_rows["lap_number"] - close_rows["stint_min_lap"]) / span
    ).clip(0, 1)
    rows = []
    for status, subset in close_rows.groupby("match_status"):
        rows.append(
            {
                "status": status,
                "close_laps": len(subset),
                "share_of_close_laps": len(subset) / len(close_rows),
                "mean_tyre_age": float(subset["tyre_age"].mean()),
                "mean_stint_progress": float(subset["stint_progress"].mean()),
                "mean_close_fraction": float(subset["close_fraction"].mean()),
                "mean_sustained_close_seconds": float(subset["sustained_close_seconds"].mean()),
            }
        )
    return pd.DataFrame(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate traffic effect v2 from completed audit CSV")
    parser.add_argument("--csv", default=DEFAULT_INPUT)
    parser.add_argument("--summary-csv", default="traffic_effect_summary_v1_2018_2025.csv")
    parser.add_argument("--output-prefix", default=DEFAULT_OUT)
    parser.add_argument("--bootstrap-iterations", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=17)
    args = parser.parse_args()

    source = Path(args.csv)
    summary_path = Path(args.summary_csv)
    if not source.exists():
        raise SystemExit(f"Missing exposure CSV: {source}")
    if not summary_path.exists():
        raise SystemExit(f"Missing summary CSV: {summary_path}")

    summary = pd.read_csv(summary_path)
    primary = summary[summary["scope"].isin(["overall", "era"])].copy()

    # The audit already persisted exact threshold-level race-balanced aggregates.
    # For uncertainty, 150 m pair/race reconstruction is reproduced from raw
    # exposure rows. For 100/200 m we preserve the audit aggregates and explicitly
    # mark CI as unavailable rather than manufacturing pair-level classifications.
    df = pd.read_csv(source)
    close_rows, matches = reconstruct_close_matches(df, threshold=150.0)
    race_df = race_level_effects(matches)

    rows: list[dict[str, object]] = []
    for threshold in sorted(primary["threshold_m"].dropna().unique()):
        subset = primary[primary["threshold_m"].eq(threshold)]
        for _, row in subset.iterrows():
            scope = str(row["scope"])
            era = str(row["regulation_era"])
            item = {
                "scope": scope,
                "regulation_era": era,
                "threshold_m": float(threshold),
                "races": int(row["races"]),
                "matched_pairs": int(row["matched_pairs"]),
                "mean_seconds": float(row["mean_traffic_delta_seconds_race_balanced"]),
                "median_seconds": float(row["median_race_mean_delta_seconds"]),
                "positive_pair_rate": float(row["positive_pair_delta_rate"]),
                "bootstrap_95ci_low_seconds": np.nan,
                "bootstrap_95ci_high_seconds": np.nan,
                "positive_race_rate": np.nan,
                "ci_method": "not_reconstructed",
            }
            if threshold == 150.0 and not race_df.empty:
                rsub = race_df
                if scope == "era":
                    rsub = rsub[rsub["regulation_era"].eq(era)]
                stats = summarize_races(
                    rsub, scope=f"{scope}:{era}", iterations=args.bootstrap_iterations, seed=args.seed
                )
                item.update(
                    {
                        "mean_seconds": stats["mean_seconds"],
                        "median_seconds": stats["median_seconds"],
                        "bootstrap_95ci_low_seconds": stats["bootstrap_95ci_low_seconds"],
                        "bootstrap_95ci_high_seconds": stats["bootstrap_95ci_high_seconds"],
                        "positive_race_rate": stats["positive_race_rate"],
                        "ci_method": "race_bootstrap_from_raw_exposures",
                    }
                )
            rows.append(item)

    stats_df = pd.DataFrame(rows)
    stats_df.to_csv(f"{args.output_prefix}_effects.csv", index=False)
    race_df.to_csv(f"{args.output_prefix}_race_deltas_150m.csv", index=False)
    unmatched_summary(close_rows).to_csv(f"{args.output_prefix}_unmatched.csv", index=False)

    stability = (
        primary.groupby(["scope", "regulation_era"], dropna=False)
        .agg(
            thresholds_positive=("mean_traffic_delta_seconds_race_balanced", lambda s: int((s > 0).all())),
            threshold_count=("threshold_m", "count"),
            min_effect_seconds=("mean_traffic_delta_seconds_race_balanced", "min"),
            max_effect_seconds=("mean_traffic_delta_seconds_race_balanced", "max"),
        )
        .reset_index()
    )
    stability.to_csv(f"{args.output_prefix}_stability.csv", index=False)

    print("=== TRAFFIC VALIDATION V2 ===")
    print(stats_df.to_string(index=False))
    print("\nThreshold direction stability:")
    print(stability.to_string(index=False))
    print("\n150 m matched vs unmatched:")
    print(unmatched_summary(close_rows).to_string(index=False))
    print(f"\nWrote {args.output_prefix}_effects.csv")
    print(f"Wrote {args.output_prefix}_race_deltas_150m.csv")
    print(f"Wrote {args.output_prefix}_unmatched.csv")
    print(f"Wrote {args.output-prefix}_stability.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
