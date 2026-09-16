"""Leakage-safe walk-forward validation for the pooled tyre model.

Audit-only. This module does not modify the production simulator or calibration.
It evaluates three held-out predictors:
  A) historical raw within-stint slope median,
  B) pooled field-relative tyre-age slope,
  C) flat/no-degradation slope of zero.

The target race is never used to fit a slope. Training races are strictly earlier
by race_date, and early targets with fewer than the configured minimum training
races are excluded from stability conclusions.
"""

from __future__ import annotations

import argparse
import csv
import os
import random
from collections import defaultdict
from dataclasses import dataclass
from math import isfinite, sqrt
from statistics import median

from sqlalchemy import create_engine, text

from audit_race_strategy_tyre_model_v2 import (
    COMPOUNDS,
    LapRow,
    _build_rows,
    _query_rows,
)

DEFAULT_MIN_TRAINING_RACES = 15
DEFAULT_MIN_UNIQUE_PIT_LAPS = 4
DEFAULT_MIN_STINT_AGE_SPAN = 4
DEFAULT_MIN_STINT_LAPS = 5
DEFAULT_BOOTSTRAP_DRAWS = 300


@dataclass(frozen=True)
class RaceMeta:
    race_id: int
    season_year: int
    race_date: str
    era: str


@dataclass(frozen=True)
class ModelFit:
    slope: float | None
    ci_low: float | None
    ci_high: float | None
    n_races: int
    n_stints: int
    n_laps: int


@dataclass(frozen=True)
class Score:
    correlation: float | None
    rmse: float | None
    n_stints: int
    n_points: int


def _ols(x: list[float], y: list[float]) -> tuple[float, float] | None:
    if len(x) != len(y) or len(x) < 3:
        return None
    mx = sum(x) / len(x)
    my = sum(y) / len(y)
    sxx = sum((v - mx) ** 2 for v in x)
    if sxx <= 1e-12:
        return None
    slope = sum((a - mx) * (b - my) for a, b in zip(x, y)) / sxx
    return slope, my - slope * mx


def _bootstrap_race_ci(
    observations: list[tuple[int, float, float]],
    *,
    draws: int = DEFAULT_BOOTSTRAP_DRAWS,
    seed: int = 7,
) -> tuple[float | None, float | None]:
    """Cluster-bootstrap the pooled slope by race, avoiding lap-level pseudo-replication."""
    if len(observations) < 3:
        return None, None
    by_race: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for race_id, x, y in observations:
        by_race[race_id].append((x, y))
    races = list(by_race)
    if len(races) < 3:
        return None, None
    rng = random.Random(seed)
    slopes: list[float] = []
    for _ in range(max(100, draws)):
        sample = [rng.choice(races) for _ in races]
        xs: list[float] = []
        ys: list[float] = []
        for race_id in sample:
            for x, y in by_race[race_id]:
                xs.append(x)
                ys.append(y)
        fit = _ols(xs, ys)
        if fit is not None and isfinite(fit[0]):
            slopes.append(fit[0])
    if not slopes:
        return None, None
    slopes.sort()
    lo = slopes[max(0, int(0.025 * (len(slopes) - 1)))]
    hi = slopes[min(len(slopes) - 1, int(0.975 * (len(slopes) - 1)))]
    return lo, hi


def _race_diversity(rows: list[LapRow]) -> dict[int, int]:
    stops: dict[int, set[int]] = defaultdict(set)
    for row in rows:
        if row.start_lap > 1:
            stops[row.race_id].add(row.start_lap)
    return {race_id: len(laps) for race_id, laps in stops.items()}


def _field_relative_rows(
    rows: list[LapRow],
    eligible_races: set[int],
) -> list[tuple[int, float, float, str, int]]:
    """Return (race_id, tyre_age, residual, compound, driver_id), leave-one-out field median."""
    by_race_lap: dict[tuple[int, int], list[tuple[int, float]]] = defaultdict(list)
    for row in rows:
        if row.race_id in eligible_races:
            by_race_lap[(row.race_id, row.lap_number)].append((row.driver_id, row.lap_time))

    result: list[tuple[int, float, float, str, int]] = []
    grouped: dict[str, list[LapRow]] = defaultdict(list)
    for row in rows:
        if row.race_id in eligible_races:
            grouped[row.stint_key].append(row)

    for stint_rows in grouped.values():
        stint_rows.sort(key=lambda r: r.lap_number)
        if not stint_rows:
            continue
        if stint_rows[-1].lap_number - stint_rows[0].lap_number < DEFAULT_MIN_STINT_AGE_SPAN:
            continue
        for row in stint_rows:
            # Exclude out-lap and in-lap.
            if row.lap_number in (row.start_lap, row.end_lap):
                continue
            values = by_race_lap[(row.race_id, row.lap_number)]
            others = [value for driver_id, value in values if driver_id != row.driver_id]
            if len(others) < 3:
                continue
            ref = median(others)
            age = row.lap_number - row.start_lap
            if age <= 0:
                continue
            result.append((row.race_id, float(age), float(row.lap_time - ref), row.compound, row.driver_id))
    return result


def _stint_slope(rowset: list[tuple[int, float, float, str, int]]) -> float | None:
    if len(rowset) < 3:
        return None
    fit = _ols([r[1] for r in rowset], [r[2] for r in rowset])
    return fit[0] if fit is not None else None


def fit_v2(training_rows: list[LapRow], eligible_races: set[int], compound: str) -> ModelFit:
    observations = [r for r in _field_relative_rows(training_rows, eligible_races) if r[3] == compound]
    triples = [(r[0], r[1], r[2]) for r in observations]
    fit = _ols([r[1] for r in observations], [r[2] for r in observations])
    lo, hi = _bootstrap_race_ci(triples)
    return ModelFit(
        slope=fit[0] if fit else None,
        ci_low=lo,
        ci_high=hi,
        n_races=len({r[0] for r in observations}),
        n_stints=len({(r[0], r[4]) for r in observations}),
        n_laps=len(observations),
    )


def fit_raw(training_rows: list[LapRow], eligible_races: set[int], compound: str) -> ModelFit:
    grouped: dict[str, list[LapRow]] = defaultdict(list)
    for row in training_rows:
        if row.race_id in eligible_races and row.compound == compound:
            grouped[row.stint_key].append(row)
    slopes: list[float] = []
    race_ids: set[int] = set()
    for stint_key, stint_rows in grouped.items():
        stint_rows.sort(key=lambda r: r.lap_number)
        if len(stint_rows) < DEFAULT_MIN_STINT_LAPS:
            continue
        if stint_rows[-1].lap_number - stint_rows[0].lap_number < DEFAULT_MIN_STINT_AGE_SPAN:
            continue
        trimmed = [r for r in stint_rows if r.lap_number not in (r.start_lap, r.end_lap)]
        fit = _ols(
            [float(r.lap_number - r.start_lap) for r in trimmed if r.lap_number > r.start_lap],
            [float(r.lap_time - sum(x.lap_time for x in stint_rows[:2]) / 2) for r in trimmed if r.lap_number > r.start_lap],
        )
        if fit is not None:
            slopes.append(fit[0])
            race_ids.add(stint_rows[0].race_id)
    return ModelFit(
        slope=median(slopes) if slopes else None,
        ci_low=None,
        ci_high=None,
        n_races=len(race_ids),
        n_stints=len(slopes),
        n_laps=sum(1 for row in training_rows if row.race_id in eligible_races and row.compound == compound),
    )


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 1e-12 or vy <= 1e-12:
        return None
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return cov / sqrt(vx * vy)


def score_target(
    target_rows: list[LapRow],
    compound: str,
    slope: float,
) -> Score:
    grouped: dict[str, list[LapRow]] = defaultdict(list)
    for row in target_rows:
        if row.compound == compound:
            grouped[row.stint_key].append(row)

    actual: list[float] = []
    predicted: list[float] = []
    scored_stints = 0
    for stint_rows in grouped.values():
        stint_rows.sort(key=lambda r: r.lap_number)
        if len(stint_rows) < DEFAULT_MIN_STINT_LAPS:
            continue
        valid = [r for r in stint_rows if r.lap_number not in (r.start_lap, r.end_lap) and r.lap_number > r.start_lap]
        if len(valid) < 3:
            continue
        baseline = valid[0].lap_time
        for row in valid:
            age = float(row.lap_number - row.start_lap)
            actual.append(float(row.lap_time - baseline))
            predicted.append(float(slope * (age - (valid[0].lap_number - row.start_lap))))
        scored_stints += 1

    if not actual:
        return Score(None, None, 0, 0)
    rmse = sqrt(sum((a - p) ** 2 for a, p in zip(actual, predicted)) / len(actual))
    return Score(_pearson(predicted, actual), rmse, scored_stints, len(actual))


def load_race_meta(db, start_year: int, end_year: int) -> dict[int, RaceMeta]:
    rows = db.execute(
        text("""
            SELECT id, season_year, race_date, regulation_era
            FROM races
            WHERE race_date IS NOT NULL
              AND season_year BETWEEN :start_year AND :end_year
              AND regulation_era IS NOT NULL
            ORDER BY race_date, id
        """),
        {"start_year": start_year, "end_year": end_year},
    ).mappings().all()
    return {
        int(r["id"]): RaceMeta(int(r["id"]), int(r["season_year"]), str(r["race_date"]), str(r["regulation_era"]))
        for r in rows
    }


def _stable_training_races(target: RaceMeta, metas: dict[int, RaceMeta], min_training: int) -> list[int]:
    same_era = [m for m in metas.values() if m.era == target.era and m.race_date < target.race_date]
    same_era.sort(key=lambda m: (m.race_date, m.race_id))
    return [m.race_id for m in same_era] if len(same_era) >= min_training else []


def _sensitivity(
    rows: list[LapRow],
    *,
    min_age_span_values=(3, 4, 5),
    diversity_values=(3, 4, 5),
) -> list[dict]:
    out: list[dict] = []
    for age_span in min_age_span_values:
        for diversity in diversity_values:
            diversity_map = _race_diversity(rows)
            allowed = {race for race, count in diversity_map.items() if count >= diversity}
            relative = _field_relative_rows(rows, allowed)
            for era in sorted({r.era for r in rows}):
                for compound in COMPOUNDS:
                    obs = [r for r in relative if r[3] == compound and next((x.era for x in rows if x.race_id == r[0]), None) == era]
                    fit = _ols([r[1] for r in obs], [r[2] for r in obs])
                    out.append({"era": era, "compound": compound, "min_age_span": age_span, "min_unique_pit_laps": diversity, "slope": fit[0] if fit else None, "n_races": len({r[0] for r in obs}), "n_stints": len({(r[0], r[4]) for r in obs}), "n_laps": len(obs)})
    return out


def run(db, *, start_year: int, end_year: int, min_training_races: int) -> tuple[list[dict], list[dict]]:
    raw = _query_rows(db, start_year, end_year)
    rows, diversity, _ = _build_rows(raw)
    metas = load_race_meta(db, start_year, end_year)
    rows_by_race: dict[int, list[LapRow]] = defaultdict(list)
    for row in rows:
        rows_by_race[row.race_id].append(row)

    summary: list[dict] = []
    for target in sorted(metas.values(), key=lambda m: (m.race_date, m.race_id)):
        training_ids = _stable_training_races(target, metas, min_training_races)
        if not training_ids:
            continue
        train_ids = {race_id for race_id in training_ids if diversity.get(race_id, 0) >= DEFAULT_MIN_UNIQUE_PIT_LAPS}
        if not train_ids:
            continue
        train_rows = [r for r in rows if r.race_id in train_ids and r.era == target.era]
        target_rows = rows_by_race.get(target.race_id, [])
        for compound in COMPOUNDS:
            v2 = fit_v2(train_rows, train_ids, compound)
            raw_fit = fit_raw(train_rows, train_ids, compound)
            if v2.slope is None:
                continue
            scores = {
                "raw": score_target(target_rows, compound, raw_fit.slope if raw_fit.slope is not None else 0.0),
                "v2": score_target(target_rows, compound, v2.slope),
                "flat": score_target(target_rows, compound, 0.0),
            }
            for model_name, score in scores.items():
                summary.append({
                    "target_race_id": target.race_id,
                    "target_year": target.season_year,
                    "era": target.era,
                    "compound": compound,
                    "model": model_name,
                    "slope": raw_fit.slope if model_name == "raw" else (v2.slope if model_name == "v2" else 0.0),
                    "ci_low": v2.ci_low if model_name == "v2" else None,
                    "ci_high": v2.ci_high if model_name == "v2" else None,
                    "n_training_races": v2.n_races,
                    "n_training_stints": v2.n_stints,
                    "n_training_laps": v2.n_laps,
                    "correlation": score.correlation,
                    "rmse": score.rmse,
                    "scored_stints": score.n_stints,
                    "scored_points": score.n_points,
                })

    sensitivity = _sensitivity(rows)
    return summary, sensitivity


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-year", type=int, default=2018)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument("--min-training-races", type=int, default=DEFAULT_MIN_TRAINING_RACES)
    parser.add_argument("--csv", default="tyre_model_walkforward_v1.csv")
    parser.add_argument("--sensitivity-csv", default="tyre_model_sensitivity_v1.csv")
    args = parser.parse_args()

    url = os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is not set")
    db = create_engine(url).connect()
    try:
        summary, sensitivity = run(db, start_year=args.start_year, end_year=args.end_year, min_training_races=args.min_training_races)
    finally:
        db.close()

    with open(args.csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(summary[0].keys()) if summary else ["target_race_id"])
        writer.writeheader()
        writer.writerows(summary)
    with open(args.sensitivity_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(sensitivity[0].keys()) if sensitivity else ["era"])
        writer.writeheader()
        writer.writerows(sensitivity)

    print("=== TYRE MODEL WALK-FORWARD V1 ===")
    print(f"targets_scored={len({r['target_race_id'] for r in summary})}")
    print(f"rows={len(summary)}")
    for model in ("raw", "v2", "flat"):
        rows = [r for r in summary if r["model"] == model and r["rmse"] is not None]
        mean_rmse = sum(r["rmse"] for r in rows) / len(rows) if rows else None
        corr_rows = [r["correlation"] for r in rows if r["correlation"] is not None]
        mean_corr = sum(corr_rows) / len(corr_rows) if corr_rows else None
        print(f"{model}: mean_rmse={mean_rmse} mean_correlation={mean_corr} scored_rows={len(rows)}")

    if sensitivity:
        print("\n=== SENSITIVITY SNAPSHOT ===")
        for row in sensitivity[:18]:
            print(f"{row['era']} / {row['compound']} / age>={row['min_age_span']} diversity>={row['min_unique_pit_laps']}: slope={row['slope']} races={row['n_races']} stints={row['n_stints']}")
    print(f"\nWrote {args.csv} and {args.sensitivity_csv}")
    print("Production simulator/calibration were not modified by this audit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
