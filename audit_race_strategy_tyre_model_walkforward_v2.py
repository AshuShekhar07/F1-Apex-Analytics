"""Corrected leakage-safe walk-forward validation for the pooled tyre model.

Audit-only. Training uses strictly earlier races. Held-out scoring uses the same
leave-one-out field-relative residual transform used during training, so the
validation measures tyre-age shape rather than raw race-pace evolution.

Models:
  raw  = median within-stint raw lap-time slope
  v2   = pooled field-relative tyre-age slope
  flat = zero slope
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

from audit_race_strategy_tyre_model_v2 import COMPOUNDS, LapRow

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


def _ols(xs: list[float], ys: list[float]) -> tuple[float, float] | None:
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 1e-12:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return slope, my - slope * mx


def _bootstrap_race_ci(obs: list[tuple[int, float, float]], draws: int = DEFAULT_BOOTSTRAP_DRAWS, seed: int = 7):
    by_race: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for race_id, x, y in obs:
        by_race[race_id].append((x, y))
    races = list(by_race)
    if len(races) < 3:
        return None, None
    rng = random.Random(seed)
    slopes: list[float] = []
    for _ in range(max(100, draws)):
        xs: list[float] = []
        ys: list[float] = []
        for race_id in (rng.choice(races) for _ in races):
            for x, y in by_race[race_id]:
                xs.append(x); ys.append(y)
        fit = _ols(xs, ys)
        if fit and isfinite(fit[0]):
            slopes.append(fit[0])
    if not slopes:
        return None, None
    slopes.sort()
    lo_i = int(0.025 * (len(slopes) - 1))
    hi_i = int(0.975 * (len(slopes) - 1))
    return slopes[lo_i], slopes[hi_i]


def _race_diversity(rows: list[LapRow]) -> dict[int, int]:
    stops: dict[int, set[int]] = defaultdict(set)
    for row in rows:
        if row.start_lap > 1:
            stops[row.race_id].add(row.start_lap)
    return {race_id: len(v) for race_id, v in stops.items()}


def _field_relative_rows(rows: list[LapRow], eligible_races: set[int], min_stint_age_span: int):
    by_race_lap: dict[tuple[int, int], list[tuple[int, float]]] = defaultdict(list)
    for row in rows:
        if row.race_id in eligible_races:
            by_race_lap[(row.race_id, row.lap_number)].append((row.driver_id, row.lap_time))

    grouped: dict[str, list[LapRow]] = defaultdict(list)
    for row in rows:
        if row.race_id in eligible_races:
            grouped[row.stint_key].append(row)

    result = []
    for stint_rows in grouped.values():
        stint_rows.sort(key=lambda r: r.lap_number)
        if not stint_rows or stint_rows[-1].lap_number - stint_rows[0].lap_number < min_stint_age_span:
            continue
        for row in stint_rows:
            if row.lap_number in (row.start_lap, row.end_lap):
                continue
            others = [time for driver_id, time in by_race_lap[(row.race_id, row.lap_number)] if driver_id != row.driver_id]
            if len(others) < 3:
                continue
            age = row.lap_number - row.start_lap
            if age <= 0:
                continue
            result.append((row.race_id, float(age), float(row.lap_time - median(others)), row.compound, row.driver_id, row.stint_key, row.era))
    return result


def _raw_stint_slopes(rows: list[LapRow], eligible_races: set[int], compound: str, min_stint_age_span: int):
    grouped: dict[str, list[LapRow]] = defaultdict(list)
    for row in rows:
        if row.race_id in eligible_races and row.compound == compound:
            grouped[row.stint_key].append(row)
    slopes: list[float] = []
    for stint_rows in grouped.values():
        stint_rows.sort(key=lambda r: r.lap_number)
        if len(stint_rows) < DEFAULT_MIN_STINT_LAPS or stint_rows[-1].lap_number - stint_rows[0].lap_number < min_stint_age_span:
            continue
        valid = [r for r in stint_rows if r.lap_number not in (r.start_lap, r.end_lap) and r.lap_number > r.start_lap]
        if len(valid) < 3:
            continue
        baseline = sum(r.lap_time for r in stint_rows[:2]) / 2.0
        fit = _ols([float(r.lap_number - r.start_lap) for r in valid], [float(r.lap_time - baseline) for r in valid])
        if fit:
            slopes.append(fit[0])
    return slopes


def fit_models(training_rows: list[LapRow], eligible_races: set[int], compound: str, min_stint_age_span: int):
    relative = [r for r in _field_relative_rows(training_rows, eligible_races, min_stint_age_span) if r[3] == compound]
    fit = _ols([r[1] for r in relative], [r[2] for r in relative])
    v2_ci = _bootstrap_race_ci([(r[0], r[1], r[2]) for r in relative])
    raw_slopes = _raw_stint_slopes(training_rows, eligible_races, compound, min_stint_age_span)
    raw_slope = median(raw_slopes) if raw_slopes else None
    return (
        ModelFit(fit[0] if fit else None, v2_ci[0], v2_ci[1], len({r[0] for r in relative}), len({r[5] for r in relative}), len(relative)),
        ModelFit(raw_slope, None, None, len(raw_slopes), len(raw_slopes), sum(1 for r in training_rows if r.race_id in eligible_races and r.compound == compound)),
    )


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    vx = sum((x - mx) ** 2 for x in xs); vy = sum((y - my) ** 2 for y in ys)
    if vx <= 1e-12 or vy <= 1e-12:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sqrt(vx * vy)


def _target_relative_rows(target_rows: list[LapRow], min_stint_age_span: int):
    races = {r.race_id for r in target_rows}
    return _field_relative_rows(target_rows, races, min_stint_age_span)


def score_target(target_rows: list[LapRow], compound: str, slope: float, min_stint_age_span: int) -> Score:
    """Score slope against the same field-relative residual used in training."""
    rel = [r for r in _target_relative_rows(target_rows, min_stint_age_span) if r[3] == compound]
    grouped: dict[str, list[tuple]] = defaultdict(list)
    for row in rel:
        grouped[row[5]].append(row)
    actual: list[float] = []
    predicted: list[float] = []
    scored_stints = 0
    for rows in grouped.values():
        rows.sort(key=lambda r: r[1])
        if len(rows) < 3:
            continue
        first_age = rows[0][1]
        first_residual = rows[0][2]
        for row in rows:
            actual.append(row[2] - first_residual)
            predicted.append(slope * (row[1] - first_age))
        scored_stints += 1
    if not actual:
        return Score(None, None, 0, 0)
    rmse = sqrt(sum((a - p) ** 2 for a, p in zip(actual, predicted)) / len(actual))
    return Score(_pearson(predicted, actual), rmse, scored_stints, len(actual))


def load_rows(db, start_year: int, end_year: int) -> list[LapRow]:
    result = db.execute(text("""
        SELECT r.id race_id, r.season_year, r.regulation_era era,
               rs.race_entry_id, rs.stint_number, rs.compound, rs.start_lap, rs.end_lap,
               l.lap_number, l.lap_time
        FROM race_stints rs
        JOIN races r ON r.id = rs.race_id
        JOIN sessions s ON s.race_id = r.id AND s.session_type = 'R'
        JOIN session_weather sw ON sw.session_id = s.id
        JOIN laps l ON l.session_id = s.id AND l.race_entry_id = rs.race_entry_id
        WHERE r.race_date IS NOT NULL
          AND r.season_year BETWEEN :start_year AND :end_year
          AND r.regulation_era IS NOT NULL
          AND sw.rainfall = FALSE
          AND rs.compound IN ('SOFT','MEDIUM','HARD')
          AND rs.start_lap IS NOT NULL AND rs.end_lap IS NOT NULL
          AND l.lap_number BETWEEN rs.start_lap AND rs.end_lap
          AND l.lap_time IS NOT NULL AND l.is_valid = TRUE
        ORDER BY r.race_date, r.id, rs.race_entry_id, l.lap_number
    """), {"start_year": start_year, "end_year": end_year})
    return [LapRow(int(x["race_id"]), int(x["season_year"]), str(x["era"]), int(x["race_entry_id"]), f"{int(x['race_id'])}:{int(x['race_entry_id'])}:{int(x['stint_number'])}", str(x["compound"]).upper(), int(x["start_lap"]), int(x["end_lap"]), int(x["lap_number"]), float(x["lap_time"])) for x in result.mappings().all()]


def load_meta(db, start_year: int, end_year: int):
    result = db.execute(text("""
        SELECT id, season_year, race_date, regulation_era FROM races
        WHERE race_date IS NOT NULL AND season_year BETWEEN :start_year AND :end_year
          AND regulation_era IS NOT NULL ORDER BY race_date, id
    """), {"start_year": start_year, "end_year": end_year})
    return [RaceMeta(int(x["id"]), int(x["season_year"]), str(x["race_date"]), str(x["regulation_era"])) for x in result.mappings().all()]


def run(db, start_year: int, end_year: int, min_training_races: int):
    rows = load_rows(db, start_year, end_year)
    metas = load_meta(db, start_year, end_year)
    diversity = _race_diversity(rows)
    by_race = defaultdict(list)
    for row in rows: by_race[row.race_id].append(row)

    summary: list[dict] = []
    stability: list[dict] = []
    stable_index: dict[str, int] = defaultdict(int)
    for target in metas:
        training = [m for m in metas if m.era == target.era and m.race_date < target.race_date]
        if len(training) < min_training_races:
            continue
        train_ids = {m.race_id for m in training if diversity.get(m.race_id, 0) >= DEFAULT_MIN_UNIQUE_PIT_LAPS}
        if not train_ids:
            continue
        train_rows = [r for r in rows if r.race_id in train_ids and r.era == target.era]
        stable_index[target.era] += 1
        for compound in COMPOUNDS:
            v2, raw = fit_models(train_rows, train_ids, compound, DEFAULT_MIN_STINT_AGE_SPAN)
            if v2.slope is None:
                continue
            stability.append({
                "target_race_id": target.race_id, "target_year": target.season_year, "era": target.era,
                "compound": compound, "target_race_index": stable_index[target.era], "slope": v2.slope,
                "ci_low": v2.ci_low, "ci_high": v2.ci_high, "n_training_races": v2.n_races,
                "n_stints": v2.n_stints, "n_laps": v2.n_laps,
            })
            target_rows = by_race.get(target.race_id, [])
            scores = {
                "raw": score_target(target_rows, compound, raw.slope if raw.slope is not None else 0.0, DEFAULT_MIN_STINT_AGE_SPAN),
                "v2": score_target(target_rows, compound, v2.slope, DEFAULT_MIN_STINT_AGE_SPAN),
                "flat": score_target(target_rows, compound, 0.0, DEFAULT_MIN_STINT_AGE_SPAN),
            }
            for model, score in scores.items():
                summary.append({
                    "target_race_id": target.race_id, "target_year": target.season_year,
                    "era": target.era, "compound": compound, "model": model,
                    "slope": raw.slope if model == "raw" else (v2.slope if model == "v2" else 0.0),
                    "ci_low": v2.ci_low if model == "v2" else None, "ci_high": v2.ci_high if model == "v2" else None,
                    "n_training_races": v2.n_races, "n_training_stints": v2.n_stints, "n_training_laps": v2.n_laps,
                    "correlation": score.correlation, "rmse": score.rmse,
                    "scored_stints": score.n_stints, "scored_points": score.n_points,
                })
    return summary, stability


def write_csv(path: str, rows: list[dict]):
    if not rows: return
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys())); writer.writeheader(); writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-year", type=int, default=2018)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument("--min-training-races", type=int, default=DEFAULT_MIN_TRAINING_RACES)
    parser.add_argument("--csv", default="tyre_model_walkforward_v2.csv")
    parser.add_argument("--stability-csv", default="tyre_model_stability_v2.csv")
    args = parser.parse_args()
    url = os.getenv("DATABASE_URL")
    if not url: raise SystemExit("DATABASE_URL is not set")
    db = create_engine(url).connect()
    try: summary, stability = run(db, args.start_year, args.end_year, args.min_training_races)
    finally: db.close()
    write_csv(args.csv, summary); write_csv(args.stability_csv, stability)
    print("=== TYRE MODEL WALK-FORWARD V2 ===")
    print(f"targets_scored={len({r['target_race_id'] for r in summary})}")
    for model in ("raw", "v2", "flat"):
        scored = [r for r in summary if r["model"] == model and r["rmse"] is not None]
        corrs = [r["correlation"] for r in scored if r["correlation"] is not None]
        print(f"{model}: mean_rmse={(sum(r['rmse'] for r in scored)/len(scored) if scored else None)} mean_correlation={(sum(corrs)/len(corrs) if corrs else None)} scored_rows={len(scored)}")
    print("\n=== V2 STABILITY SAMPLE ===")
    for r in stability[:18]:
        print(f"{r['era']} / {r['compound']}: training_races={r['n_training_races']} slope={r['slope']} CI=({r['ci_low']},{r['ci_high']})")
    print(f"\nWrote {args.csv} and {args.stability_csv}")
    print("Production simulator/calibration were not modified.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
