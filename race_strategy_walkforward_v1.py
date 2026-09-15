"""Leakage-safe walk-forward harness for race-strategy evaluation v1.

This module does not fetch data and does not infer unavailable pre-race inputs.
A backtest case must explicitly provide the pre-race simulator inputs and the
realized post-race outcome separately. The runner rejects cases whose declared
input snapshot is newer than the prediction cutoff.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from statistics import mean
from typing import Callable, Iterable, Sequence

from race_strategy_simulator_v1 import (
    CompetitorProfile,
    RaceContext,
    SimulationParameters,
    Strategy,
    TyreAllocation,
    evaluate_strategy,
    optimize_strategies,
)


@dataclass(frozen=True)
class InputSnapshot:
    """Pre-race model inputs and the timestamp/year at which they were known."""

    available_year: int
    context: RaceContext
    competitors: tuple[CompetitorProfile, ...]
    allocation: TyreAllocation
    parameters: SimulationParameters
    source: str = "historical_snapshot"


@dataclass(frozen=True)
class RealizedOutcome:
    """Post-race labels used only for scoring the already-frozen prediction."""

    year: int
    actual_finish_position: int
    actual_strategy: Strategy | None = None


@dataclass(frozen=True)
class RaceBacktestCase:
    race_id: int
    year: int
    prediction_cutoff_year: int
    snapshot: InputSnapshot
    outcome: RealizedOutcome
    baseline_strategy: Strategy


@dataclass(frozen=True)
class RaceBacktestResult:
    race_id: int
    year: int
    selected_strategy: str
    model_expected_finish: float
    model_p1_probability: float
    actual_finish_position: int
    baseline_name: str
    baseline_expected_finish: float
    baseline_p1_probability: float
    baseline_distance_from_actual: float
    selected_distance_from_actual: float
    leakage_safe: bool
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class WalkForwardReport:
    cases_attempted: int
    cases_scored: int
    leakage_rejected: int
    model_mean_abs_finish_error: float
    baseline_mean_abs_finish_error: float
    model_better_than_baseline_rate: float
    results: tuple[RaceBacktestResult, ...]


def _validate_case(case: RaceBacktestCase) -> None:
    if case.snapshot.available_year > case.prediction_cutoff_year:
        raise ValueError(
            f"Leakage: snapshot year {case.snapshot.available_year} is after cutoff {case.prediction_cutoff_year}"
        )
    if case.outcome.year != case.year:
        raise ValueError("Outcome year must match backtest race year")
    if case.prediction_cutoff_year > case.year:
        raise ValueError("Prediction cutoff cannot be after target race year")
    if case.outcome.actual_finish_position <= 0:
        raise ValueError("Actual finish position must be positive")


def run_case(
    case: RaceBacktestCase,
    candidates: Sequence[Strategy],
    *,
    simulations_per_strategy: int = 1500,
    seed: int = 17,
) -> RaceBacktestResult:
    """Optimize from the frozen pre-race snapshot and score against the outcome."""
    _validate_case(case)
    if not candidates:
        raise ValueError("At least one candidate strategy is required")

    evaluations = optimize_strategies(
        candidates,
        case.snapshot.context,
        case.snapshot.competitors,
        case.snapshot.allocation,
        case.snapshot.parameters,
        simulations_per_strategy=simulations_per_strategy,
        seed=seed,
    )
    selected = evaluations[0]

    baseline_evaluation = evaluate_strategy(
        case.baseline_strategy,
        case.snapshot.context,
        case.snapshot.competitors,
        case.snapshot.allocation,
        case.snapshot.parameters,
        simulations=simulations_per_strategy,
        seed=seed + len(candidates) + 1,
    )

    actual = case.outcome.actual_finish_position
    return RaceBacktestResult(
        race_id=case.race_id,
        year=case.year,
        selected_strategy=selected.strategy.name,
        model_expected_finish=selected.expected_finish,
        model_p1_probability=selected.win_probability,
        actual_finish_position=actual,
        baseline_name=case.baseline_strategy.name,
        baseline_expected_finish=baseline_evaluation.expected_finish,
        baseline_p1_probability=baseline_evaluation.win_probability,
        baseline_distance_from_actual=abs(baseline_evaluation.expected_finish - actual),
        selected_distance_from_actual=abs(selected.expected_finish - actual),
        leakage_safe=True,
    )


def walk_forward_evaluate(
    cases: Iterable[RaceBacktestCase],
    candidate_builder: Callable[[RaceBacktestCase], Sequence[Strategy]],
    *,
    simulations_per_strategy: int = 1500,
    seed: int = 17,
    reject_leakage: bool = True,
) -> WalkForwardReport:
    """Run chronological strategy evaluations with explicit leakage accounting."""
    ordered = sorted(cases, key=lambda case: (case.year, case.race_id))
    results: list[RaceBacktestResult] = []
    rejected = 0

    for index, case in enumerate(ordered):
        if case.snapshot.available_year > case.prediction_cutoff_year:
            rejected += 1
            if reject_leakage:
                raise ValueError(
                    f"Leakage detected in race {case.race_id}: snapshot={case.snapshot.available_year}, cutoff={case.prediction_cutoff_year}"
                )
            continue

        candidates = tuple(candidate_builder(case))
        result = run_case(
            case,
            candidates,
            simulations_per_strategy=simulations_per_strategy,
            seed=seed + index,
        )
        results.append(result)

    if results:
        model_errors = [r.selected_distance_from_actual for r in results if isfinite(r.selected_distance_from_actual)]
        baseline_errors = [r.baseline_distance_from_actual for r in results if isfinite(r.baseline_distance_from_actual)]
        model_mae = mean(model_errors) if model_errors else float("nan")
        baseline_mae = mean(baseline_errors) if baseline_errors else float("nan")
        better = (
            sum(m < b for m, b in zip(model_errors, baseline_errors)) / len(model_errors)
            if model_errors and len(model_errors) == len(baseline_errors)
            else 0.0
        )
    else:
        model_mae = baseline_mae = float("nan")
        better = 0.0

    return WalkForwardReport(
        cases_attempted=len(ordered),
        cases_scored=len(results),
        leakage_rejected=rejected,
        model_mean_abs_finish_error=model_mae,
        baseline_mean_abs_finish_error=baseline_mae,
        model_better_than_baseline_rate=better,
        results=tuple(results),
    )
