"""Post-hoc statistical validation for the completed traffic exposure audit.

Reads the research-only traffic exposure CSV and computes:
- race-clustered SE and percentile bootstrap CI for the race-balanced effect
- regulation-era estimates
- per-race distribution / outlier diagnostics
- matched vs unmatched close-lap diagnostics

This script does not modify the simulator or database.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

DEFAULT_INPUT = "traffic_exposure_v1_2018_2025.csv"
DEFAULT_SUMMARY = "traffic_effect_summary_v1_2018_2025.csv"
DEFAULT_PREFIX = "traffic_audit_stats_v1"


def _numeric(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    out = df.copy()
    for column in columns:
        out[column] = pd.to_numeric(out[column], errors="coerce")
    return out


def reconstruct_matches(
    df: pd.DataFrame,
    *,
    max_lap_distance: int = 10,
    max_tyre_age_diff: int = 1,
    max_clean_air_reuse: int = 1,
) -> pd.DataFrame:
    required = {
        "race_id",
        "driver_key",
        "stint_number",
        "compound",
        "tyre_age",
        "lap_number",
        "traffic_state",
        "field_relative_residual_seconds",
        "season_year",
        "regulation_era",
        "close_fraction",
        "sustained_close_seconds",
    }
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing exposure columns: {sorted(missing)}")

    work = _numeric(
        df,
        [
            "race_id",
            "stint_number",
            "tyre_age",
            "lap_number",
            "field_relative_residual_seconds",
            "season_year",
            "close_fraction",
            "sustained_close_seconds",
        ],
    )
    work = work.dropna(
        subset=[
            "race_id",
            "driver_key",
            "stint_number",
            "compound",
            "tyre_age",
            "lap_number",
            "field_relative_residual_seconds",
        ]
    ).copy()

    work["compound"] = work["compound"].astype(str).str.upper()
    work["driver_key"] = work["driver_key"].astype(str)

    work["match_status"] = "not_close"
    group_cols = ["race_id", "driver_key", "stint_number", "compound"]

    for _, group in work.groupby(group_cols, sort=False):
        ordered = group.sort_values("lap_number")
        close = ordered[ordered["traffic_state"].eq("close")]
        clear = ordered[ordered["traffic_state"].eq("clear")]

        reuse: Counter[int] = Counter()
        for close_index, close_row in close.iterrows():
            candidates = clear[
                ((clear["tyre_age"] - close_row["tyre_age"]).abs() <= max_tyre_age_diff)
                & ((clear["lap_number"] - close_row["lap_number"]).abs() <= max_lap_distance)
            ].copy()
            if candidates.empty:
                work.loc[close_index, "match_status"] = "unmatched_close"
                continue

            candidates["_age_diff"] = (candidates["tyre_age"] - close_row["tyre_age"]).abs()
            candidates["_lap_diff"] = (candidates["lap_number"] - close_row["lap_number"]).abs()
            candidates = candidates.sort_values(
                ["_age_diff", "_lap_diff", "lap_number"],
                kind="mergesort",
            )
            chosen = None
            for clear_index, clear_row in candidates.iterrows():
                lap = int(clear_row["lap_number"])
                if reuse[lap] < max_clean_air_reuse:
                    chosen = clear_row
                    break

            if chosen is None:
                work.loc[close_index, "match_status"] = "unmatched_close"
            else:
                reuse[int(chosen["lap_number"])] += 1
                work.loc[close_index, "match_status"] = "matched_close"
                work.loc[close_index, "clean_lap_number"] = int(chosen["lap_number"])
                work.loc[close_index, "traffic_delta_seconds"] = (
                    float(close_row["field_relative_residual_seconds"])
                    - float(chosen["field_relative_residual_seconds"])
                )

    close_only = work[work["traffic_state"].eq("close")].copy()
    close_only["stint_min_lap"] = close_only.groupby(group_cols)["lap_number"].transform("min")
    close_only["stint_max_lap"] = close_only.groupby(group_cols)["lap_number"].transform("max")
    span = (close_only["stint_max_lap"] - close_only["stint_min_lap"]).replace(0, np.nan)
    close_only["stint_progress"] = (
        (close_only["lap_number"] - close_only["stint_min_lap"]) / span
    ).clip(0, 1)

    return close_only


def build_pair_table(close_rows: pd.DataFrame) -> pd.DataFrame:
    matched = close_rows[close_rows["match_status"].eq("matched_close")].copy()
    if matched.empty:
        return matched

    group_cols = ["race_id", "driver_key", "stint_number"]
    stint = (
        matched.groupby(group_cols, as_index=False)["traffic_delta_seconds"]
        .mean()
        .rename(columns={"traffic_delta_seconds": "stint_mean_delta"})
    )
    race = (
        stint.groupby("race_id", as_index=False)["stint_mean_delta"]
        .mean()
        .rename(columns={"stint_mean_delta": "race_mean_delta_seconds"})
    )

    meta = (
        close_rows[["race_id", "season_year", "regulation_era"]]
        .drop_duplicates("race_id")
    )
    race = race.merge(meta, on="race_id", how="left")
    return race.sort_values(["season_year", "race_id"]).reset_index(drop=True)


def bootstrap_ci(values: np.ndarray, *, iterations: int = 10000, seed: int = 17) -> tuple[float, float]:
    values = np.asarray(values, dtype=float)
    if len(values) < 2:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, len(values), size=(iterations, len(values)))
    boot_means = values[indices].mean(axis=1)
    return float(np.quantile(boot_means, 0.025)), float(np.quantile(boot_means, 0.975))


def effect_stats(
    race_table: pd.DataFrame,
    label: str,
    *,
    bootstrap_iterations: int = 10000,
    seed: int = 17,
) -> dict[str, object]:
    values = race_table["race_mean_delta_seconds"].to_numpy(dtype=float)
    n = len(values)
    mean_value = float(values.mean()) if n else float("nan")
    median_value = float(np.median(values)) if n else float("nan")
    sd = float(values.std(ddof=1)) if n > 1 else float("nan")
    se = sd / np.sqrt(n) if n > 1 else float("nan")
    ci_low, ci_high = bootstrap_ci(values, iterations=bootstrap_iterations, seed=seed)

    return {
        "scope": label,
        "races": n,
        "mean_delta_seconds": mean_value,
        "median_delta_seconds": median_value,
        "race_sd_seconds": sd,
        "race_clustered_se_seconds": se,
        "normal_95ci_low_seconds": mean_value - 1.96 * se if n > 1 else float("nan"),
        "normal_95ci_high_seconds": mean_value + 1.96 * se if n > 1 else float("nan"),
        "bootstrap_95ci_low_seconds": ci_low,
        "bootstrap_95ci_high_seconds": ci_high,
        "positive_race_rate": float((values > 0).mean()) if n else float("nan"),
        "min_race_delta_seconds": float(values.min()) if n else float("nan"),
        "max_race_delta_seconds": float(values.max()) if n else float("nan"),
        "mean_minus_median_seconds": mean_value - median_value if n else float("nan"),
    }


def leave_one_out_diagnostics(race_table: pd.DataFrame) -> pd.DataFrame:
    values = race_table["race_mean_delta_seconds"].to_numpy(dtype=float)
    rows: list[dict[str, object]] = []
    total = float(values.sum())
    n = len(values)
    for i, row in race_table.reset_index(drop=True).iterrows():
        loo = (total - float(row["race_mean_delta_seconds"])) / (n - 1) if n > 1 else np.nan
        rows.append(
            {
                "race_id": int(row["race_id"]),
                "season_year": int(row["season_year"]),
                "regulation_era": str(row["regulation_era"]),
                "race_mean_delta_seconds": float(row["race_mean_delta_seconds"]),
                "leave_one_out_mean_seconds": float(loo),
                "influence_seconds": float(loo - values.mean()) if n > 1 else np.nan,
            }
        )
    return pd.DataFrame(rows).sort_values("influence_seconds")


def unmatched_diagnostics(close_rows: pd.DataFrame) -> pd.DataFrame:
    if close_rows.empty:
        return pd.DataFrame()

    rows: list[dict[str, object]] = []
    for label, mask in [
        ("matched_close", close_rows["match_status"].eq("matched_close")),
        ("unmatched_close", close_rows["match_status"].eq("unmatched_close")),
    ]:
        subset = close_rows[mask]
        if subset.empty:
            continue
        rows.append(
            {
                "group": label,
                "close_laps": len(subset),
                "share_of_close_laps": len(subset) / len(close_rows),
                "mean_tyre_age": float(subset["tyre_age"].mean()),
                "median_tyre_age": float(subset["tyre_age"].median()),
                "mean_stint_progress": float(subset["stint_progress"].mean()),
                "median_stint_progress": float(subset["stint_progress"].median()),
                "mean_close_fraction": float(subset["close_fraction"].mean()),
                "median_close_fraction": float(subset["close_fraction"].median()),
                "mean_sustained_close_seconds": float(subset["sustained_close_seconds"].mean()),
                "median_sustained_close_seconds": float(subset["sustained_close_seconds"].median()),
            }
        )
    return pd.DataFrame(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate traffic audit statistics without rerunning FastF1")
    parser.add_argument("--csv", default=DEFAULT_INPUT)
    parser.add_argument("--summary-csv", default=DEFAULT_SUMMARY)
    parser.add_argument("--output-prefix", default=DEFAULT_PREFIX)
    parser.add_argument("--bootstrap-iterations", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=17)
    args = parser.parse_args()

    exposure_path = Path(args.csv)
    if not exposure_path.exists():
        raise SystemExit(f"Exposure CSV not found: {exposure_path}")

    df = pd.read_csv(exposure_path)
    close_rows = reconstruct_matches(df)
    race_table = build_pair_table(close_rows)

    if race_table.empty:
        raise SystemExit("No matched traffic pairs found")

    all_stats = [
        effect_stats(
            race_table,
            "overall",
            bootstrap_iterations=args.bootstrap_iterations,
            seed=args.seed,
        )
    ]
    for era, era_rows in race_table.groupby("regulation_era", sort=True):
        all_stats.append(
            effect_stats(
                era_rows,
                f"era:{era}",
                bootstrap_iterations=args.bootstrap_iterations,
                seed=args.seed,
            )
        )

    stats = pd.DataFrame(all_stats)
    stats.to_csv(f"{args.output_prefix}.csv", index=False)

    race_table.to_csv(f"{args.output_prefix}_race_deltas.csv", index=False)
    leave_one_out_diagnostics(race_table).to_csv(
        f"{args.output_prefix}_race_influence.csv", index=False
    )
    unmatched_diagnostics(close_rows).to_csv(
        f"{args.output_prefix}_unmatched_diagnostics.csv", index=False
    )

    print("=== TRAFFIC STATISTICAL VALIDATION ===")
    print(stats.to_string(index=False))
    print()
    print("Top positive race effects:")
    print(
        race_table.nlargest(10, "race_mean_delta_seconds")[
            ["season_year", "race_id", "regulation_era", "race_mean_delta_seconds"]
        ].to_string(index=False)
    )
    print()
    print("Most negative race effects:")
    print(
        race_table.nsmallest(10, "race_mean_delta_seconds")[
            ["season_year", "race_id", "regulation_era", "race_mean_delta_seconds"]
        ].to_string(index=False)
    )
    print()
    print("Matched vs unmatched close laps:")
    print(unmatched_diagnostics(close_rows).to_string(index=False))
    print()
    print(f"Wrote {args.output_prefix}.csv")
    print(f"Wrote {args.output_prefix}_race_deltas.csv")
    print(f"Wrote {args.output_prefix}_race_influence.csv")
    print(f"Wrote {args.output_prefix}_unmatched_diagnostics.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
