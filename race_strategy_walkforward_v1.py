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
    available_year: int
    context: RaceContext
    competitors: tuple[CompetitorProfile, ...]
    allocation: TyreAllocation
    parameters: SimulationParameters
    source: str = "historical_snapshot"


@dataclass(frozen=True)
class RealizedOutcome:
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
    actual_strategy: str | None
    selected_sequence_match: bool | None
    selected_stop_l1_error: float | None
    selected_lap_window_error: float | None
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
    strategy_sequence_match_rate: float
    mean_selected_stop_l1_error: float
    mean_selected_lap_window_error: float
    results: tuple[RaceBacktestResult, ...]


def _strategy_metrics(selected: Strategy, actual: Strategy | None) -> tuple[bool | None, float | None, float | None]:
    if actual is None:
        return None, None, None
    sequence_match = selected.sequence == actual.sequence
    selected_stops = list(selected.stop_laps)
    actual_stops = list(actual.stop_laps)
    if not selected_stops or not actual_stops:
        stop_l1 = float(abs(len(selected_stops) - len(actual_stops)))
    else:
        pairs = min(len(selected_stops), len(actual_stops))
        stop_l1 = float(sum(abs(selected_stops[i] - actual_stops[i]) for i in range(pairs)))
        stop_l1 += float(abs(len(selected_stops) - len(actual_stops)) * 100)
    # Lap-window error is mean absolute boundary error, normalized by the
    # number of matched boundaries; unmatched boundaries receive 100 laps.
    if not selected_stops and not actual_stops:
        window_error = 0.0
    else:
        n = max(len(selected_stops), len(actual_stops))
        errors = [
            abs(selected_stops[i] - actual_stops[i]) if i < len(selected_stops) and i < len(actual_stops) else 100
            for i in range(n)
        ]
        window_error = float(sum(errors) / n)
    return sequence_match, stop_l1, window_error


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
    objective: str = "p1",
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
        objective=objective,
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
    sequence_match, stop_l1, window_error = _strategy_metrics(selected.strategy, case.outcome.actual_strategy)
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
        actual_strategy=(" → ".join(case.outcome.actual_strategy.sequence) if case.outcome.actual_strategy else None),
        selected_sequence_match=sequence_match,
        selected_stop_l1_error=stop_l1,
        selected_lap_window_error=window_error,
        leakage_safe=True,
    )


def walk_forward_evaluate(
    cases: Iterable[RaceBacktestCase],
    candidate_builder: Callable[[RaceBacktestCase], Sequence[Strategy]],
    *,
    simulations_per_strategy: int = 1500,
    seed: int = 17,
    reject_leakage: bool = True,
    objective: str = "p1",
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
            objective=objective,
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
        sequence_matches = [r.selected_sequence_match for r in results if r.selected_sequence_match is not None]
        stop_errors = [r.selected_stop_l1_error for r in results if r.selected_stop_l1_error is not None]
        window_errors = [r.selected_lap_window_error for r in results if r.selected_lap_window_error is not None]
        sequence_match_rate = sum(sequence_matches) / len(sequence_matches) if sequence_matches else 0.0
        mean_stop_error = mean(stop_errors) if stop_errors else float("nan")
        mean_window_error = mean(window_errors) if window_errors else float("nan")
    else:
        model_mae = baseline_mae = float("nan")
        better = 0.0
        sequence_match_rate = 0.0
        mean_stop_error = mean_window_error = float("nan")

    return WalkForwardReport(
        cases_attempted=len(ordered),
        cases_scored=len(results),
        leakage_rejected=rejected,
        model_mean_abs_finish_error=model_mae,
        baseline_mean_abs_finish_error=baseline_mae,
        model_better_than_baseline_rate=better,
        strategy_sequence_match_rate=sequence_match_rate,
        mean_selected_stop_l1_error=mean_stop_error,
        mean_selected_lap_window_error=mean_window_error,
        results=tuple(results),
    )
