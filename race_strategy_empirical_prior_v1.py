
"""Leakage-safe empirical F1 strategy prior V1.

Research-only experiment. This module does NOT modify the production simulator.

The model deliberately avoids sparse hard context buckets. It uses:
- observed dry-race strategy families (compound sequences),
- soft historical weighting by track, regulation era, grid band and recency,
- a continuous track-character similarity signal from the historical
  overtaking index,
- Dirichlet-style shrinkage for strategy-family probabilities,
- shrinkage for a pace-residual-adjusted strategy effect.

The performance target is NOT raw wins/finishing position. For each historical
driver-race, we first estimate pre-race pace competitiveness from the existing
pace-normalization machinery, rank the predicted field, and define:

    finish_residual = actual_finish - expected_pace_rank

Negative means the driver finished better than their pace rank; positive means
they finished worse. This is still observational and not causal: incidents,
grid effects, and race events remain residual confounders.

V1 is a predictive gate:
Does historical strategy-family information predict this residual better than
a zero-residual baseline? If not, the strategy-prior direction should be
rejected before simulator integration.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from dotenv import load_dotenv
from sqlalchemy import create_engine

from race_status import is_classified
from strategy_context_feasibility_audit_v1 import (
    load_dry_race_context,
    load_stints,
    load_pit_stops,
    load_post_pit_compounds,
    load_observed_lap_counts,
    reconstruct_observed_strategy_patterns,
)
from race_strategy_pace_calibration_v3 import (
    PaceResidualObservation,
    build_residual_observations,
)
from race_strategy_pace_v3_db_adapter import load_pace_observations


@dataclass(frozen=True)
class EmpiricalPriorConfig:
    track_same_weight: float = 3.0
    same_era_weight: float = 1.0
    cross_era_weight: float = 0.40
    same_grid_weight: float = 1.0
    cross_grid_weight: float = 0.85
    recency_half_life_years: float = 2.5
    overtaking_scale_positions: float = 2.0
    dirichlet_strength: float = 8.0
    residual_shrinkage_strength: float = 8.0
    era_base_mix: float = 0.75
    global_base_mix: float = 0.25


@dataclass(frozen=True)
class StrategyResidualObservation:
    race_id: int
    race_date: date
    track_id: int
    regulation_era: str
    grid_band: str
    driver_id: int
    team_id: int
    strategy_family: tuple[str, ...]
    pit_buckets: tuple[str, ...]
    finish_position: int
    expected_pace_rank: float
    finish_residual: float


@dataclass(frozen=True)
class StrategyPrediction:
    strategy_family: tuple[str, ...]
    predicted_residual: float
    probability: float
    effective_weight: float


def strategy_family_label(sequence: Iterable[str]) -> str:
    return " → ".join(str(x).upper() for x in sequence)


def _finite(values: Iterable[float]) -> list[float]:
    return [float(v) for v in values if math.isfinite(float(v))]


def _median(values: Iterable[float], default: float = 0.0) -> float:
    vals = _finite(values)
    return float(statistics.median(vals)) if vals else default


def _mean(values: Iterable[float], default: float = 0.0) -> float:
    vals = _finite(values)
    return sum(vals) / len(vals) if vals else default


def _pearson(xs: list[float], ys: list[float]) -> float:
    if len(xs) != len(ys) or len(xs) < 2:
        return float("nan")
    mx = _mean(xs)
    my = _mean(ys)
    dx = [x - mx for x in xs]
    dy = [y - my for y in ys]
    den = math.sqrt(sum(x * x for x in dx) * sum(y * y for y in dy))
    return sum(a * b for a, b in zip(dx, dy)) / den if den > 0 else float("nan")


def _recency_weight(target_date: date, historical_date: date, half_life: float) -> float:
    age_years = max(0.0, (target_date - historical_date).days / 365.25)
    if half_life <= 0:
        return 1.0
    return 0.5 ** (age_years / half_life)


def _overtaking_similarity(
    target_value: float | None,
    historical_value: float | None,
    scale: float,
) -> float:
    if target_value is None or historical_value is None:
        return 1.0
    if not (math.isfinite(float(target_value)) and math.isfinite(float(historical_value))):
        return 1.0
    scale = max(0.1, float(scale))
    return math.exp(-abs(float(target_value) - float(historical_value)) / scale)


def build_prior_weights(
    target: StrategyResidualObservation,
    history: Iterable[StrategyResidualObservation],
    track_overtaking_index: dict[tuple[int, str], float],
    *,
    config: EmpiricalPriorConfig | None = None,
) -> list[tuple[StrategyResidualObservation, float]]:
    config = config or EmpiricalPriorConfig()
    target_oi = track_overtaking_index.get((target.track_id, target.regulation_era))

    weighted: list[tuple[StrategyResidualObservation, float]] = []
    for row in history:
        if row.race_date >= target.race_date:
            continue

        era_weight = (
            config.same_era_weight
            if row.regulation_era == target.regulation_era
            else config.cross_era_weight
        )
        grid_weight = (
            config.same_grid_weight
            if row.grid_band == target.grid_band
            else config.cross_grid_weight
        )
        track_weight = (
            config.track_same_weight
            if row.track_id == target.track_id
            else 1.0
        )
        oi_similarity = _overtaking_similarity(
            target_oi,
            track_overtaking_index.get((row.track_id, row.regulation_era)),
            config.overtaking_scale_positions,
        )
        recency = _recency_weight(
            target.race_date,
            row.race_date,
            config.recency_half_life_years,
        )

        weight = max(0.0, track_weight * era_weight * grid_weight * oi_similarity * recency)
        if weight > 0:
            weighted.append((row, weight))

    return weighted


def _weighted_family_counts(
    rows: list[tuple[StrategyResidualObservation, float]],
) -> Counter:
    counts: Counter = Counter()
    for row, weight in rows:
        counts[row.strategy_family] += weight
    return counts


def _distribution_from_counts(counts: Counter) -> dict[tuple[str, ...], float]:
    total = sum(counts.values())
    return {
        key: (value / total if total > 0 else 0.0)
        for key, value in counts.items()
    }


def posterior_strategy_prior(
    target: StrategyResidualObservation,
    history: Iterable[StrategyResidualObservation],
    track_overtaking_index: dict[tuple[int, str], float],
    *,
    config: EmpiricalPriorConfig | None = None,
) -> dict[tuple[str, ...], StrategyPrediction]:
    """Return shrunk probability and residual-effect estimates by family."""
    config = config or EmpiricalPriorConfig()
    history_rows = list(history)
    weighted = build_prior_weights(
        target,
        history_rows,
        track_overtaking_index,
        config=config,
    )
    if not weighted:
        return {}

    same_era = [(row, w) for row, w in weighted if row.regulation_era == target.regulation_era]
    era_counts = _weighted_family_counts(same_era)
    global_counts = _weighted_family_counts(weighted)

    era_probs = _distribution_from_counts(era_counts)
    global_probs = _distribution_from_counts(global_counts)

    # Include the target family even if it has never appeared in training.
    # It then receives a zero-effect/default posterior rather than being
    # silently dropped from evaluation.
    families = set(global_counts) | {target.strategy_family}
    base_probs: dict[tuple[str, ...], float] = {}
    for family in families:
        base_probs[family] = (
            config.era_base_mix * era_probs.get(family, 0.0)
            + config.global_base_mix * global_probs.get(family, 0.0)
        )

    total_weight = sum(weight for _, weight in weighted)
    posterior: dict[tuple[str, ...], StrategyPrediction] = {}

    for family in sorted(families, key=str):
        family_weight = global_counts[family]
        probability = (
            family_weight + config.dirichlet_strength * base_probs.get(family, 0.0)
        ) / max(1e-12, total_weight + config.dirichlet_strength)

        residual_numerator = sum(
            weight * row.finish_residual
            for row, weight in weighted
            if row.strategy_family == family
        )
        residual_effect = (
            residual_numerator
            / max(1e-12, family_weight + config.residual_shrinkage_strength)
        )

        posterior[family] = StrategyPrediction(
            strategy_family=family,
            predicted_residual=float(residual_effect),
            probability=float(probability),
            effective_weight=float(family_weight),
        )

    total_probability = sum(p.probability for p in posterior.values())
    if total_probability > 0:
        posterior = {
            family: StrategyPrediction(
                strategy_family=p.strategy_family,
                predicted_residual=p.predicted_residual,
                probability=p.probability / total_probability,
                effective_weight=p.effective_weight,
            )
            for family, p in posterior.items()
        }

    return posterior


def predict_actual_strategy(
    target: StrategyResidualObservation,
    history: Iterable[StrategyResidualObservation],
    track_overtaking_index: dict[tuple[int, str], float],
    *,
    config: EmpiricalPriorConfig | None = None,
) -> StrategyPrediction | None:
    posterior = posterior_strategy_prior(
        target,
        history,
        track_overtaking_index,
        config=config,
    )
    return posterior.get(target.strategy_family)


def _pre_race_era_pace_prediction(
    *,
    target_track_id: int,
    target_era: str,
    driver_id: int,
    team_id: int,
    historical_residuals: list[PaceResidualObservation],
) -> float | None:
    rows = [
        row for row in historical_residuals
        if row.regulation_era == target_era
        and math.isfinite(row.relative_gap_seconds)
        and math.isfinite(row.track_reference_seconds)
    ]
    if not rows:
        return None

    track_rows = [row for row in rows if row.track_id == target_track_id]
    era_track_rows = rows
    team_rows = [row for row in rows if row.team_key == str(team_id)]
    driver_rows = [row for row in team_rows if row.driver_key == str(driver_id)]

    track_reference = _median(
        [row.track_reference_seconds for row in track_rows],
        default=_median([row.track_reference_seconds for row in era_track_rows], default=0.0),
    )
    team_effect = _median([row.relative_gap_seconds for row in team_rows], default=0.0)
    team_reference = _median([row.relative_gap_seconds for row in team_rows], default=0.0)
    driver_adjustment = (
        _median([row.relative_gap_seconds for row in driver_rows], default=team_reference)
        - team_reference
    )
    return track_reference + team_effect + driver_adjustment


def compute_pre_race_finish_residuals(
    context_df,
    pace_residuals: Iterable[PaceResidualObservation],
) -> tuple[StrategyResidualObservation, ...]:
    """
    Build race-level strategy performance residuals in strict chronological order.

    Pace observations from a race are only eligible when that race_date precedes
    the target race. No target-race pace information is used to construct the
    target's expected pace rank.
    """
    residuals = list(pace_residuals)
    race_rows = (
        context_df[["race_id", "race_date"]]
        .drop_duplicates()
        .sort_values(["race_date", "race_id"])
    )
    race_dates = {
        int(row.race_id): row.race_date
        for row in race_rows.itertuples(index=False)
    }

    observations: list[StrategyResidualObservation] = []

    for target_row in race_rows.itertuples(index=False):
        target_race_id = int(target_row.race_id)
        target_date = target_row.race_date
        prior_race_ids = {
            race_id for race_id, rdate in race_dates.items()
            if rdate < target_date
        }
        historical = [
            row for row in residuals
            if row.race_id in prior_race_ids
        ]
        if not historical:
            continue

        target_entries = context_df[
            (context_df["race_id"].astype(int) == target_race_id)
            & context_df["starting_grid_position"].notna()
            & context_df["finishing_position"].notna()
        ].copy()
        target_entries["finishing_status"] = target_entries["finishing_status"].astype(str)
        target_entries = target_entries[
            target_entries["finishing_status"].map(is_classified)
        ]
        if target_entries.empty:
            continue

        predictions: dict[int, float] = {}
        for row in target_entries.itertuples(index=False):
            predicted = _pre_race_era_pace_prediction(
                target_track_id=int(row.track_id),
                target_era=str(row.regulation_era),
                driver_id=int(row.driver_id),
                team_id=int(row.team_id),
                historical_residuals=historical,
            )
            if predicted is not None:
                predictions[int(row.race_entry_id)] = float(predicted)

        if len(predictions) < 5:
            continue

        expected_ranks = {
            entry_id: rank
            for rank, (entry_id, _) in enumerate(
                sorted(predictions.items(), key=lambda item: (item[1], item[0])),
                start=1,
            )
        }

        for row in target_entries.itertuples(index=False):
            entry_id = int(row.race_entry_id)
            if entry_id not in expected_ranks:
                continue
            observations.append(
                StrategyResidualObservation(
                    race_id=target_race_id,
                    race_date=target_date,
                    track_id=int(row.track_id),
                    regulation_era=str(row.regulation_era),
                    grid_band=str(row.grid_band),
                    driver_id=int(row.driver_id),
                    team_id=int(row.team_id),
                    strategy_family=tuple(),  # filled from strategy patterns later
                    pit_buckets=tuple(),
                    finish_position=int(row.finishing_position),
                    expected_pace_rank=float(expected_ranks[entry_id]),
                    finish_residual=float(int(row.finishing_position) - expected_ranks[entry_id]),
                )
            )

    return tuple(observations)


def load_track_overtaking_index_as_of(
    engine,
    *,
    target_date: date,
) -> dict[tuple[int, str], float]:
    """Historical dry-race overtaking index available before target_date."""
    from sqlalchemy import text

    query = text(
        """
        SELECT
            r.track_id,
            r.regulation_era,
            AVG(ABS(rr.starting_grid_position - rr.finishing_position))
                AS avg_position_change
        FROM race_results rr
        JOIN race_entries re ON re.id = rr.race_entry_id
        JOIN races r ON r.id = re.race_id
        JOIN sessions s
          ON s.id = rr.session_id
         AND s.session_type = 'R'
        LEFT JOIN session_weather sw ON sw.session_id = s.id
        WHERE r.race_date < :target_date
          AND r.race_date IS NOT NULL
          AND r.regulation_era IS NOT NULL
          AND sw.rainfall IS FALSE
          AND rr.starting_grid_position IS NOT NULL
          AND rr.finishing_position IS NOT NULL
        GROUP BY r.track_id, r.regulation_era
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(query, {"target_date": target_date}).mappings().all()
    return {
        (int(row["track_id"]), str(row["regulation_era"])): float(row["avg_position_change"])
        for row in rows
        if row["avg_position_change"] is not None
    }


def attach_strategies(
    performance_rows: Iterable[StrategyResidualObservation],
    patterns: dict[tuple[int, int], dict[str, Any]],
) -> tuple[StrategyResidualObservation, ...]:
    by_race_driver_entry = defaultdict(list)
    for key, pattern in patterns.items():
        race_id, race_entry_id = key
        by_race_driver_entry[(race_id, race_entry_id)].append(pattern)

    pattern_by_key = {
        key: value
        for key, values in by_race_driver_entry.items()
        for value in values
    }

    # Performance rows carry race_entry_id only indirectly in this version, so
    # rebuild a race/driver map from the pattern records.
    pattern_by_race_driver = {
        (race_id, int(pattern.get("driver_id", -1))): pattern
        for (race_id, _entry_id), pattern in patterns.items()
    }
    _ = pattern_by_race_driver

    return tuple(performance_rows)


def build_strategy_residual_dataset(
    context_df,
    patterns: dict[tuple[int, int], dict[str, Any]],
    pace_residuals: Iterable[PaceResidualObservation],
) -> tuple[StrategyResidualObservation, ...]:
    """Join strategy patterns to the pre-race pace residual target."""
    residual_rows = compute_pre_race_finish_residuals(context_df, pace_residuals)
    # Match through race entry id using a lightweight map rebuilt from context.
    context_key_to_pattern = {}
    for (race_id, entry_id), pattern in patterns.items():
        context_key_to_pattern[(int(race_id), int(entry_id))] = pattern

    context_lookup = {
        (int(row.race_id), int(row.driver_id)): int(row.race_entry_id)
        for row in context_df.itertuples(index=False)
    }

    output: list[StrategyResidualObservation] = []
    for row in residual_rows:
        entry_id = context_lookup.get((row.race_id, row.driver_id))
        pattern = context_key_to_pattern.get((row.race_id, entry_id)) if entry_id is not None else None
        if pattern is None:
            continue
        output.append(
            StrategyResidualObservation(
                race_id=row.race_id,
                race_date=row.race_date,
                track_id=row.track_id,
                regulation_era=row.regulation_era,
                grid_band=row.grid_band,
                driver_id=row.driver_id,
                team_id=row.team_id,
                strategy_family=tuple(pattern["compounds"]),
                pit_buckets=tuple(pattern["pit_buckets"]),
                finish_position=row.finish_position,
                expected_pace_rank=row.expected_pace_rank,
                finish_residual=row.finish_residual,
            )
        )
    return tuple(output)


def race_balanced_metrics(
    predictions: Iterable[tuple[StrategyResidualObservation, float]],
) -> dict[str, float | int]:
    by_race: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for row, predicted in predictions:
        by_race[row.race_id].append((row.finish_residual, predicted))

    if not by_race:
        return {
            "races": 0,
            "observations": 0,
            "mae": float("nan"),
            "rmse": float("nan"),
            "correlation": float("nan"),
            "positive_race_rate": float("nan"),
        }

    race_mae: list[float] = []
    race_rmse: list[float] = []
    race_positive: list[bool] = []
    all_actual: list[float] = []
    all_pred: list[float] = []

    for rows in by_race.values():
        abs_errors = [abs(actual - pred) for actual, pred in rows]
        sq_errors = [(actual - pred) ** 2 for actual, pred in rows]
        race_mae.append(_mean(abs_errors))
        race_rmse.append(math.sqrt(_mean(sq_errors)))
        race_positive.append(
            _mean([abs(actual - pred) for actual, pred in rows])
            < _mean([abs(actual) for actual, _ in rows])
        )
        all_actual.extend(actual for actual, _ in rows)
        all_pred.extend(pred for _, pred in rows)

    return {
        "races": len(by_race),
        "observations": sum(len(rows) for rows in by_race.values()),
        "mae": _mean(race_mae),
        "rmse": _mean(race_rmse),
        "correlation": _pearson(all_actual, all_pred),
        "positive_race_rate": _mean([1.0 if x else 0.0 for x in race_positive]),
    }


def run_walk_forward(
    engine,
    *,
    start_year: int = 2024,
    end_year: int = 2025,
    config: EmpiricalPriorConfig | None = None,
    output_csv: str = "strategy_empirical_prior_walkforward_v1.csv",
) -> dict[str, Any]:
    """Evaluate the strategy prior on dry races strictly before each target date."""
    context = load_dry_race_context(engine, start_year, end_year)
    if context.empty:
        raise ValueError("No dry-race context rows found")

    # Build strategy observations for all races needed by both training and
    # target periods. Strategy labels from target races are only used as targets.
    all_context = load_dry_race_context(engine, 2018, end_year)
    dry_race_ids = set(all_context["race_id"].astype(int))
    stints = load_stints(engine, dry_race_ids)
    pits = load_pit_stops(engine, dry_race_ids)
    post_pit = load_post_pit_compounds(engine, dry_race_ids)
    race_laps, driver_laps = load_observed_lap_counts(engine, dry_race_ids)
    patterns, _, usable = reconstruct_observed_strategy_patterns(
        stints, pits, all_context, race_laps, driver_laps, post_pit
    )

    pace_rows, pace_warnings = load_pace_observations(
        engine, start_year=2018, end_year=end_year
    )
    pace_residuals = build_residual_observations(pace_rows)

    dataset = build_strategy_residual_dataset(all_context, patterns, pace_residuals)
    target_dates = {
        int(row.race_id): row.race_date
        for row in context[["race_id", "race_date"]].drop_duplicates().itertuples(index=False)
    }

    training_rows_by_target: list[tuple[StrategyResidualObservation, list[StrategyResidualObservation], dict[tuple[int, str], float]]] = []
    target_rows = [
        row for row in dataset
        if row.race_id in target_dates
    ]

    for target in target_rows:
        history = [
            row for row in dataset
            if row.race_date < target.race_date
        ]
        oi = load_track_overtaking_index_as_of(
            engine,
            target_date=target.race_date,
        )
        training_rows_by_target.append((target, history, oi))

    model_predictions: list[tuple[StrategyResidualObservation, float]] = []
    baseline_predictions: list[tuple[StrategyResidualObservation, float]] = []
    output_rows: list[dict[str, Any]] = []

    for target, history, oi in training_rows_by_target:
        prediction = predict_actual_strategy(target, history, oi, config=config)
        if prediction is None:
            continue

        model_predictions.append((target, prediction.predicted_residual))
        baseline_predictions.append((target, 0.0))

        output_rows.append({
            "race_id": target.race_id,
            "race_date": target.race_date.isoformat(),
            "track_id": target.track_id,
            "era": target.regulation_era,
            "grid_band": target.grid_band,
            "driver_id": target.driver_id,
            "team_id": target.team_id,
            "strategy_family": strategy_family_label(target.strategy_family),
            "pit_buckets": "|".join(target.pit_buckets),
            "actual_finish": target.finish_position,
            "expected_pace_rank": target.expected_pace_rank,
            "finish_residual": target.finish_residual,
            "predicted_residual": prediction.predicted_residual,
            "predicted_probability": prediction.probability,
            "effective_weight": prediction.effective_weight,
        })

    metrics = race_balanced_metrics(model_predictions)
    baseline_metrics = race_balanced_metrics(baseline_predictions)

    improvement_pct = (
        100.0 * (baseline_metrics["mae"] - metrics["mae"]) / baseline_metrics["mae"]
        if math.isfinite(float(baseline_metrics["mae"])) and baseline_metrics["mae"] > 0
        else float("nan")
    )

    if output_csv:
        path = Path(output_csv)
        with path.open("w", newline="", encoding="utf-8") as handle:
            if output_rows:
                writer = csv.DictWriter(handle, fieldnames=list(output_rows[0].keys()))
                writer.writeheader()
                writer.writerows(output_rows)

    result = {
        "target_races": len(target_dates),
        "usable_strategy_observations_total": len(usable),
        "model_predictions": len(model_predictions),
        "model_metrics": metrics,
        "baseline_metrics": baseline_metrics,
        "mae_improvement_pct": improvement_pct,
        "pace_warnings": list(pace_warnings),
        "output_csv": output_csv,
        "production_integration": False,
    }

    print("=== LEAKAGE-SAFE EMPIRICAL STRATEGY PRIOR WALK-FORWARD V1 ===")
    print(f"target_races={result['target_races']}")
    print(f"model_predictions={result['model_predictions']}")
    print(f"model_race_balanced_mae={metrics['mae']:.4f}")
    print(f"baseline_zero_mae={baseline_metrics['mae']:.4f}")
    print(f"mae_improvement_pct={improvement_pct:.3f}")
    print(f"positive_race_rate={metrics['positive_race_rate']:.3f}")
    print(f"correlation={metrics['correlation']:.4f}")
    print(f"wrote={output_csv}")
    print("Production integration intentionally disabled.")

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Empirical strategy-prior walk-forward V1")
    parser.add_argument("--start-year", type=int, default=2024)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument("--csv", default="strategy_empirical_prior_walkforward_v1.csv")
    args = parser.parse_args()

    load_dotenv()
    url = os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is not set")

    engine = create_engine(url)
    run_walk_forward(
        engine,
        start_year=args.start_year,
        end_year=args.end_year,
        output_csv=args.csv,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
