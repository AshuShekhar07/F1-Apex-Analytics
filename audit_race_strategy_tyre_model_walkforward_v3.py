"""Corrected leakage-safe walk-forward validation for the pooled tyre model.

Audit-only. This version fixes the aggregation bug in v2: a target race is
scored by averaging *all valid stint scores* across compounds, so compounds do
not receive implicit equal weight when they contain different numbers of
stints. Target races are then averaged equally in the final aggregate.

Training remains strictly earlier races and uses the same leave-one-out
field-relative residual transform as the held-out scoring.

Outputs:
  - per-compound diagnostic rows
  - per-stint held-out scores
  - one corrected race-level score per target race/model
"""
from __future__ import annotations

import argparse
import csv
import os
from collections import defaultdict
from dataclasses import dataclass
from math import sqrt

from sqlalchemy import create_engine

from audit_race_strategy_tyre_model_v2 import COMPOUNDS
from audit_race_strategy_tyre_model_walkforward_v2 import (
    DEFAULT_MIN_STINT_AGE_SPAN,
    ModelFit,
    _field_relative_rows,
    _low_confidence,
    _race_diversity,
    _raw_stint_slopes,
    fit_models,
    load_meta,
    load_rows,
)


@dataclass(frozen=True)
class StintScore:
    stint_key: str
    compound: str
    correlation: float | None
    rmse: float | None
    n_points: int


@dataclass(frozen=True)
class RaceScore:
    correlation: float | None
    rmse: float | None
    n_stints: int
    n_points: int
    stint_scores: tuple[StintScore, ...]


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 1e-12 or vy <= 1e-12:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sqrt(vx * vy)


def _score_stints(
    relative_rows: list[tuple],
    slopes_by_compound: dict[str, float],
) -> tuple[StintScore, ...]:
    """Score every usable stint once, using that stint's compound slope."""
    grouped: dict[str, list[tuple]] = defaultdict(list)
    for row in relative_rows:
        if row[3] in slopes_by_compound:
            grouped[row[5]].append(row)

    scores: list[StintScore] = []
    for stint_key, rows in grouped.items():
        rows.sort(key=lambda r: r[1])
        if len(rows) < 3:
            continue

        compound = rows[0][3]
        slope = slopes_by_compound[compound]
        first_age = rows[0][1]
        first_residual = rows[0][2]
        actual = [row[2] - first_residual for row in rows]
        predicted = [slope * (row[1] - first_age) for row in rows]
        rmse = sqrt(
            sum((a - p) ** 2 for a, p in zip(actual, predicted)) / len(actual)
        )
        scores.append(
            StintScore(
                stint_key=stint_key,
                compound=compound,
                correlation=_pearson(predicted, actual),
                rmse=rmse,
                n_points=len(actual),
            )
        )

    return tuple(scores)


def _aggregate_stint_scores(stint_scores: tuple[StintScore, ...]) -> RaceScore:
    """Average metrics across stints, not across compounds."""
    if not stint_scores:
        return RaceScore(None, None, 0, 0, tuple())

    corrs = [s.correlation for s in stint_scores if s.correlation is not None]
    rmses = [s.rmse for s in stint_scores if s.rmse is not None]
    return RaceScore(
        correlation=sum(corrs) / len(corrs) if corrs else None,
        rmse=sum(rmses) / len(rmses) if rmses else None,
        n_stints=len(stint_scores),
        n_points=sum(s.n_points for s in stint_scores),
        stint_scores=stint_scores,
    )


def score_race(
    target_rows,
    slopes_by_compound: dict[str, float],
    min_stint_age_span: int = DEFAULT_MIN_STINT_AGE_SPAN,
) -> RaceScore:
    """Score one target race with equal weight per valid stint."""
    target_relative = _field_relative_rows(
        target_rows,
        {row.race_id for row in target_rows},
        min_stint_age_span,
    )
    return _aggregate_stint_scores(
        _score_stints(target_relative, slopes_by_compound)
    )


def run(db, start_year: int, end_year: int, min_training_races: int):
    rows = load_rows(db, start_year, end_year)
    metas = load_meta(db, start_year, end_year)
    diversity = _race_diversity(rows)
    by_race = defaultdict(list)
    for row in rows:
        by_race[row.race_id].append(row)

    summary: list[dict] = []
    stability: list[dict] = []
    stint_scores_output: list[dict] = []
    race_scores_output: list[dict] = []
    target_index_by_era: dict[str, int] = defaultdict(int)

    for target in metas:
        training = [
            m for m in metas
            if m.era == target.era and m.race_date < target.race_date
        ]
        prior_same_era_races = len(training)
        if prior_same_era_races < min_training_races:
            continue

        train_ids = {
            m.race_id
            for m in training
            if diversity.get(m.race_id, 0) >= 4
        }
        if not train_ids:
            continue

        train_rows = [
            r for r in rows if r.race_id in train_ids and r.era == target.era
        ]
        target_rows = by_race.get(target.race_id, [])
        target_index_by_era[target.era] += 1

        model_slopes: dict[str, dict[str, float]] = {
            "raw": {},
            "v2": {},
            "flat": {},
        }
        compound_diagnostics: list[dict] = []

        for compound in COMPOUNDS:
            v2: ModelFit
            raw: ModelFit
            v2, raw = fit_models(
                train_rows,
                train_ids,
                compound,
                DEFAULT_MIN_STINT_AGE_SPAN,
            )
            if v2.slope is None:
                continue

            usable_races = v2.n_races
            low_confidence = _low_confidence(usable_races)
            model_slopes["v2"][compound] = v2.slope
            model_slopes["raw"][compound] = raw.slope if raw.slope is not None else 0.0
            model_slopes["flat"][compound] = 0.0

            stability.append(
                {
                    "target_race_id": target.race_id,
                    "target_year": target.season_year,
                    "era": target.era,
                    "compound": compound,
                    "target_race_index": target_index_by_era[target.era],
                    "slope": v2.slope,
                    "ci_low": v2.ci_low,
                    "ci_high": v2.ci_high,
                    "prior_same_era_races": prior_same_era_races,
                    "compound_usable_training_races": usable_races,
                    "n_training_stints": v2.n_stints,
                    "n_training_laps": v2.n_laps,
                    "low_confidence": low_confidence,
                }
            )

            compound_diagnostics.append(
                {
                    "compound": compound,
                    "v2_slope": v2.slope,
                    "raw_slope": raw.slope if raw.slope is not None else 0.0,
                }
            )

        if not model_slopes["v2"]:
            continue

        race_scores = {
            model: score_race(target_rows, slopes, DEFAULT_MIN_STINT_AGE_SPAN)
            for model, slopes in model_slopes.items()
            if slopes
        }

        for model, score in race_scores.items():
            race_scores_output.append(
                {
                    "target_race_id": target.race_id,
                    "target_year": target.season_year,
                    "era": target.era,
                    "model": model,
                    "race_score_correlation": score.correlation,
                    "race_score_rmse": score.rmse,
                    "stint_count": score.n_stints,
                    "scored_points": score.n_points,
                }
            )
            for stint in score.stint_scores:
                stint_scores_output.append(
                    {
                        "target_race_id": target.race_id,
                        "target_year": target.season_year,
                        "era": target.era,
                        "compound": stint.compound,
                        "model": model,
                        "stint_key": stint.stint_key,
                        "correlation": stint.correlation,
                        "rmse": stint.rmse,
                        "scored_points": stint.n_points,
                    }
                )

        # Keep the familiar per-compound diagnostic shape, while making the
        # race_score_* fields explicitly reference the corrected race metric.
        for diag in compound_diagnostics:
            compound = diag["compound"]
            for model in ("raw", "v2", "flat"):
                if model not in race_scores:
                    continue
                score = score_race_for_compound(
                    target_rows,
                    {compound: model_slopes[model][compound]},
                    DEFAULT_MIN_STINT_AGE_SPAN,
                )
                race_score = race_scores[model]
                summary.append(
                    {
                        "target_race_id": target.race_id,
                        "target_year": target.season_year,
                        "era": target.era,
                        "compound": compound,
                        "model": model,
                        "slope": diag["raw_slope"] if model == "raw" else (
                            diag["v2_slope"] if model == "v2" else 0.0
                        ),
                        "ci_low": None,
                        "ci_high": None,
                        "prior_same_era_races": prior_same_era_races,
                        "compound_usable_training_races": None,
                        "stint_count": score.n_stints,
                        "scored_points": score.n_points,
                        "stint_mean_correlation": score.correlation,
                        "race_score_correlation": race_score.correlation,
                        "stint_mean_rmse": score.rmse,
                        "race_score_rmse": race_score.rmse,
                    }
                )

    return summary, stability, stint_scores_output, race_scores_output


def score_race_for_compound(target_rows, slopes_by_compound, min_stint_age_span):
    """Diagnostic helper for one compound; used only for summary rows."""
    return score_race(target_rows, slopes_by_compound, min_stint_age_span)


def write_csv(path: str, rows: list[dict]):
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-year", type=int, default=2018)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument("--min-training-races", type=int, default=15)
    parser.add_argument("--csv", default="tyre_model_walkforward_v3.csv")
    parser.add_argument("--stability-csv", default="tyre_model_stability_v3.csv")
    parser.add_argument("--stint-csv", default="tyre_model_walkforward_v3_stints.csv")
    parser.add_argument("--race-csv", default="tyre_model_walkforward_v3_races.csv")
    args = parser.parse_args()

    url = os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is not set")

    db = create_engine(url).connect()
    try:
        summary, stability, stint_scores, race_scores = run(
            db, args.start_year, args.end_year, args.min_training_races
        )
    finally:
        db.close()

    write_csv(args.csv, summary)
    write_csv(args.stability_csv, stability)
    write_csv(args.stint_csv, stint_scores)
    write_csv(args.race_csv, race_scores)

    print("=== TYRE MODEL WALK-FORWARD V3 ===")
    print(
        "Scoring hierarchy: lap points -> equal-weight stints across all compounds "
        "-> equal-weight target races"
    )
    for model in ("raw", "v2", "flat"):
        rows = [r for r in race_scores if r["model"] == model]
        corrs = [r["race_score_correlation"] for r in rows if r["race_score_correlation"] is not None]
        rmses = [r["race_score_rmse"] for r in rows if r["race_score_rmse"] is not None]
        print(
            f"{model}: overall_race_rmse={sum(rmses) / len(rmses) if rmses else None} "
            f"overall_race_correlation={sum(corrs) / len(corrs) if corrs else None} "
            f"target_races={len(rmses)}"
        )

    print(
        f"\nWrote {args.csv}, {args.stability_csv}, {args.stint_csv}, and {args.race_csv}"
    )
    print("Production simulator/calibration were not modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
