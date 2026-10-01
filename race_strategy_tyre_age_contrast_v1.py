"""Production-oriented tyre-age effect estimation from within-team age contrasts.

Research/calibration module only. It estimates the lap-time penalty associated with
tyre-age differences using teammate pairs observed on the same race lap and
compound. Pair-level centering removes stable driver/car pace differences, while
same-lap pairing removes shared fuel/track evolution.

No simulator integration is performed here.
"""
from __future__ import annotations

import argparse
import csv
import os
from collections import defaultdict
from dataclasses import dataclass
from math import isfinite, sqrt
from pathlib import Path
from statistics import median

from dotenv import load_dotenv
from sqlalchemy import create_engine, text


@dataclass(frozen=True)
class TyreAgeContrast:
    race_id: int
    track_id: int
    season_year: int
    race_date: str
    era: str
    pair_key: str
    compound: str
    age_difference: float
    lap_time_difference: float


@dataclass(frozen=True)
class PairFit:
    slope: float
    intercept: float
    n_points: int
    age_span: float


@dataclass(frozen=True)
class HierarchicalSlope:
    slope: float
    local_slope: float | None
    global_slope: float
    local_groups: int
    fallback: str


def _ols(xs: list[float], ys: list[float]) -> PairFit | None:
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 1e-12:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return PairFit(slope=slope, intercept=my - slope * mx, n_points=len(xs), age_span=max(xs) - min(xs))


def _center_pair(rows: list[TyreAgeContrast]) -> PairFit | None:
    """Fit age effect after removing a stable driver-pair pace offset."""
    return _ols(
        [r.age_difference for r in rows],
        [r.lap_time_difference for r in rows],
    )


def load_contrasts(
    db,
    *,
    start_year: int,
    end_year: int,
    min_pair_points: int = 5,
    min_age_span: int = 2,
    min_tyre_age: int = 2,
) -> tuple[TyreAgeContrast, ...]:
    """Load same-team/same-lap/same-compound teammate age contrasts.

    A pair observation compares two teammates only when both are on the same
    compound at the same race lap and both laps are inside their current stint.
    """
    result = db.execute(
        text(
            """
            SELECT
                r.id AS race_id,
                r.track_id,
                r.season_year,
                r.race_date,
                r.regulation_era AS era,
                re.driver_id,
                re.team_id,
                rs.stint_number,
                rs.start_lap,
                rs.end_lap,
                rs.compound,
                l.lap_number,
                l.lap_time
            FROM laps l
            JOIN sessions s ON s.id = l.session_id
            JOIN races r ON r.id = s.race_id
            JOIN race_entries re ON re.id = l.race_entry_id
            JOIN race_stints rs
              ON rs.race_entry_id = l.race_entry_id
             AND rs.race_id = r.id
             AND l.lap_number BETWEEN rs.start_lap AND rs.end_lap
            JOIN session_weather sw ON sw.session_id = s.id
            WHERE r.race_date IS NOT NULL
              AND r.season_year BETWEEN :start_year AND :end_year
              AND r.regulation_era IS NOT NULL
              AND s.session_type = 'R'
              AND sw.rainfall = FALSE
              AND l.lap_time IS NOT NULL
              AND l.is_valid = TRUE
              AND l.lap_time BETWEEN 40.0 AND 180.0
              AND rs.compound IN ('SOFT', 'MEDIUM', 'HARD')
              AND re.driver_id IS NOT NULL
              AND re.team_id IS NOT NULL
            ORDER BY r.race_date, r.id, re.team_id, re.driver_id, l.lap_number
            """
        ),
        {"start_year": start_year, "end_year": end_year},
    ).mappings().all()

    by_race_lap_team_compound: dict[tuple[int, int, int, str], list[dict]] = defaultdict(list)
    for row in result:
        start_lap = int(row["start_lap"])
        end_lap = int(row["end_lap"])
        lap_number = int(row["lap_number"])
        tyre_age = lap_number - start_lap
        if tyre_age < min_tyre_age or lap_number >= end_lap:
            continue
        key = (
            int(row["race_id"]),
            lap_number,
            int(row["team_id"]),
            str(row["compound"]).upper(),
        )
        by_race_lap_team_compound[key].append(dict(row))

    pair_rows: dict[tuple[str, str], list[TyreAgeContrast]] = defaultdict(list)
    for key, rows in by_race_lap_team_compound.items():
        if len(rows) < 2:
            continue

        # F1 races normally have two entries per team. If data contains more,
        # use each unique pair deterministically.
        rows = sorted(rows, key=lambda row: int(row["driver_id"]))
        for left_index in range(len(rows)):
            for right_index in range(left_index + 1, len(rows)):
                left = rows[left_index]
                right = rows[right_index]
                pair_key = (
                    f"{left['race_id']}:{left['team_id']}:"
                    f"{left['driver_id']}-{right['driver_id']}"
                )
                left_age = int(left["lap_number"]) - int(left["start_lap"])
                right_age = int(right["lap_number"]) - int(right["start_lap"])
                pair_rows[(pair_key, str(left["compound"]).upper())].append(
                    TyreAgeContrast(
                        race_id=int(left["race_id"]),
                        track_id=int(left["track_id"]),
                        season_year=int(left["season_year"]),
                        race_date=str(left["race_date"]),
                        era=str(left["era"]),
                        pair_key=pair_key,
                        compound=str(left["compound"]).upper(),
                        age_difference=float(left_age - right_age),
                        lap_time_difference=float(left["lap_time"]) - float(right["lap_time"]),
                    )
                )

    usable: list[TyreAgeContrast] = []
    for rows in pair_rows.values():
        if len(rows) < min_pair_points:
            continue
        ages = [r.age_difference for r in rows]
        if max(ages) - min(ages) < min_age_span:
            continue
        usable.extend(rows)
    return tuple(usable)


def fit_pair_models(
    observations: tuple[TyreAgeContrast, ...],
    *,
    min_points: int = 5,
    min_age_span: int = 2,
) -> dict[tuple[str, str, int, int], tuple[PairFit, ...]]:
    grouped: dict[tuple[str, str, int, int, str], list[TyreAgeContrast]] = defaultdict(list)
    for row in observations:
        grouped[(row.era, row.compound, row.track_id, row.race_id, row.pair_key)].append(row)

    out: dict[tuple[str, str, int, int], list[PairFit]] = defaultdict(list)
    for (era, compound, track_id, race_id, pair_key), rows in grouped.items():
        if len(rows) < min_points:
            continue
        fit = _center_pair(rows)
        if fit is None or fit.age_span < min_age_span or not isfinite(fit.slope):
            continue
        out[(era, compound, track_id, race_id)].append(fit)
    return {key: tuple(value) for key, value in out.items()}


def _robust_mean(values: list[float]) -> float:
    if not values:
        return 0.0
    centre = median(values)
    deviations = [abs(v - centre) for v in values]
    mad = median(deviations)
    if mad <= 0:
        return centre
    cutoff = 3.5 * 1.4826 * mad
    clipped = [v for v in values if abs(v - centre) <= cutoff]
    return sum(clipped) / len(clipped)


def fit_hierarchical_slopes(
    observations: tuple[TyreAgeContrast, ...],
    *,
    prior_strength: float = 4.0,
    min_points: int = 5,
    min_age_span: int = 2,
) -> dict[tuple[str, str, int], HierarchicalSlope]:
    """Fit track/compound slopes shrunk toward era/compound pooled slopes."""
    pair_models = fit_pair_models(
        observations,
        min_points=min_points,
        min_age_span=min_age_span,
    )

    global_pairs: dict[tuple[str, str], list[float]] = defaultdict(list)
    local_pairs: dict[tuple[str, str, int], list[float]] = defaultdict(list)
    for (era, compound, track_id, race_id), fits in pair_models.items():
        for fit in fits:
            global_pairs[(era, compound)].append(fit.slope)
            local_pairs[(era, compound, track_id)].append(fit.slope)

    global_slopes = {
        key: _robust_mean(values)
        for key, values in global_pairs.items()
    }

    result: dict[tuple[str, str, int], HierarchicalSlope] = {}
    for key, values in local_pairs.items():
        era, compound, track_id = key
        global_slope = global_slopes[(era, compound)]
        local_slope = _robust_mean(values)
        n = len(values)
        weight = n / (n + max(0.0, prior_strength))
        slope = max(
            0.0,
            weight * local_slope + (1.0 - weight) * global_slope,
        )
        result[key] = HierarchicalSlope(
            slope=slope,
            local_slope=local_slope,
            global_slope=global_slope,
            local_groups=n,
            fallback="era_compound_shrunk",
        )
    return result


def score_target(
    train: tuple[TyreAgeContrast, ...],
    target: tuple[TyreAgeContrast, ...],
    *,
    prior_strength: float = 4.0,
) -> dict[str, float | int]:
    """Score the age effect itself, not the nuisance teammate pace offset.

    Each held-out teammate pair gets an observed slope from its target-race
    age/time contrast. The frozen training model predicts a track/compound
    slope. The flat baseline predicts zero slope. This makes the score answer
    the actual question: does the model predict how lap-time difference changes
    with tyre-age difference?
    """
    if not target:
        return {
            "pairs": 0,
            "points": 0,
            "model_mae": float("nan"),
            "flat_mae": float("nan"),
            "improvement_pct": float("nan"),
        }

    model = fit_hierarchical_slopes(train, prior_strength=prior_strength)
    global_slopes: dict[tuple[str, str], float] = {}
    for key, values in _global_slope_values(train).items():
        global_slopes[key] = _robust_mean(values)

    pair_groups: dict[tuple[str, str], list[TyreAgeContrast]] = defaultdict(list)
    for row in target:
        pair_groups[(row.pair_key, row.compound)].append(row)

    model_errors: list[float] = []
    flat_errors: list[float] = []
    scored_pairs = 0

    for pair_rows in pair_groups.values():
        first = pair_rows[0]
        observed_fit = _center_pair(pair_rows)
        if observed_fit is None:
            continue

        fitted = model.get((first.era, first.compound, first.track_id))
        if fitted is not None:
            predicted_slope = fitted.slope
        else:
            predicted_slope = global_slopes.get((first.era, first.compound), 0.0)

        model_errors.append(abs(observed_fit.slope - predicted_slope))
        flat_errors.append(abs(observed_fit.slope))
        scored_pairs += 1

    model_mae = sum(model_errors) / len(model_errors) if model_errors else float("nan")
    flat_mae = sum(flat_errors) / len(flat_errors) if flat_errors else float("nan")
    improvement = (
        100.0 * (flat_mae - model_mae) / flat_mae
        if isfinite(flat_mae) and flat_mae > 0
        else float("nan")
    )
    return {
        "pairs": scored_pairs,
        "points": len(target),
        "model_mae": model_mae,
        "flat_mae": flat_mae,
        "improvement_pct": improvement,
    }


def _global_slope_values(
    observations: tuple[TyreAgeContrast, ...],
) -> dict[tuple[str, str], list[float]]:
    pair_models = fit_pair_models(observations)
    values: dict[tuple[str, str], list[float]] = defaultdict(list)
    for (era, compound, _track_id, _race_id), fits in pair_models.items():
        for fit in fits:
            values[(era, compound)].append(fit.slope)
    return values


def walk_forward(
    observations: tuple[TyreAgeContrast, ...],
    *,
    min_training_races: int = 15,
) -> list[dict[str, float | int]]:
    metas = sorted(
        {(r.race_id, r.season_year, r.race_date, r.era) for r in observations},
        key=lambda value: (value[2], value[0]),
    )
    rows_by_race: dict[int, tuple[TyreAgeContrast, ...]] = defaultdict(tuple)
    temp: dict[int, list[TyreAgeContrast]] = defaultdict(list)
    for row in observations:
        temp[row.race_id].append(row)
    rows_by_race = {key: tuple(value) for key, value in temp.items()}

    output: list[dict[str, float | int]] = []
    for target_race_id, target_year, target_date, target_era in metas:
        prior_races = [
            race_id
            for race_id, year, race_date, era in metas
            if era == target_era and (race_date, race_id) < (target_date, target_race_id)
        ]
        if len(prior_races) < min_training_races:
            continue
        train_rows = tuple(
            row for race_id in prior_races for row in rows_by_race[race_id]
        )
        target_rows = rows_by_race[target_race_id]
        score = score_target(train_rows, target_rows)
        score.update(
            target_race_id=target_race_id,
            target_year=target_year,
            era=target_era,
            prior_races=len(prior_races),
        )
        output.append(score)
    return output


def summarize(rows: list[dict]) -> dict[str, float | int]:
    usable = [r for r in rows if isfinite(float(r["model_mae"])) and isfinite(float(r["flat_mae"]))]
    improvements = [float(r["improvement_pct"]) for r in usable]
    return {
        "target_races": len(usable),
        "mean_model_mae": sum(float(r["model_mae"]) for r in usable) / len(usable) if usable else float("nan"),
        "mean_flat_mae": sum(float(r["flat_mae"]) for r in usable) / len(usable) if usable else float("nan"),
        "mean_improvement_pct": sum(improvements) / len(improvements) if improvements else float("nan"),
        "positive_race_rate": sum(value > 0 for value in improvements) / len(improvements) if improvements else float("nan"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Leakage-safe teammate-relative tyre-age validation")
    parser.add_argument("--start-year", type=int, default=2018)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument("--min-training-races", type=int, default=15)
    parser.add_argument("--min-pair-points", type=int, default=5)
    parser.add_argument("--min-age-span", type=int, default=2)
    parser.add_argument("--min-tyre-age", type=int, default=2)
    parser.add_argument("--csv", default="tyre_age_contrast_walkforward_v1.csv")
    args = parser.parse_args()

    load_dotenv(dotenv_path=Path.cwd() / ".env")
    url = os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is not set")

    db = create_engine(url).connect()
    try:
        observations = load_contrasts(
            db,
            start_year=args.start_year,
            end_year=args.end_year,
            min_pair_points=args.min_pair_points,
            min_age_span=args.min_age_span,
            min_tyre_age=args.min_tyre_age,
        )
    finally:
        db.close()

    results = walk_forward(observations, min_training_races=args.min_training_races)
    with open(args.csv, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(results[0].keys()) if results else ["target_race_id"],
        )
        writer.writeheader()
        writer.writerows(results)

    summary = summarize(results)
    print("=== LEAKAGE-SAFE TEAMMATE-RELATIVE TYRE-AGE WALK-FORWARD ===")
    print(f"observations={len(observations)}")
    print(f"target_races={summary['target_races']}")
    print(f"mean_model_mae={summary['mean_model_mae']}")
    print(f"mean_flat_mae={summary['mean_flat_mae']}")
    print(f"mean_improvement_pct={summary['mean_improvement_pct']}")
    print(f"positive_race_rate={summary['positive_race_rate']}")
    print(f"Wrote {args.csv}")
    print("Production simulator/calibration were not modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
