import pytest

from race_strategy_simulator_v1 import (
    CompetitorProfile,
    Distribution,
    RaceContext,
    SimulationParameters,
    Strategy,
    StrategyStint,
    TyreAllocation,
)
from race_strategy_walkforward_v1 import (
    InputSnapshot,
    RaceBacktestCase,
    RealizedOutcome,
    run_case,
    walk_forward_evaluate,
)


def _snapshot(year: int) -> InputSnapshot:
    return InputSnapshot(
        available_year=year,
        context=RaceContext(
            total_laps=20,
            starting_grid=1,
            our_base_pace_seconds=Distribution(90.0, 0.0, 40.0, 150.0),
            tyre_degradation_per_lap={"MEDIUM": Distribution(0.01, 0.0, 0.0, 0.3)},
            pit_stop_seconds=Distribution(2.4, 0.0, 1.8, 4.5),
            pit_lane_loss_seconds=Distribution(23.5, 0.0, 10.0, 60.0),
        ),
        competitors=(),
        allocation=TyreAllocation({"MEDIUM": 2}, "MEDIUM"),
        parameters=SimulationParameters(),
    )


def _strategy(name: str) -> Strategy:
    return Strategy(name, (StrategyStint("MEDIUM", 1, 20),))


def test_run_case_is_leakage_safe_and_scores_baseline():
    baseline = _strategy("baseline")
    case = RaceBacktestCase(
        race_id=1,
        year=2024,
        prediction_cutoff_year=2024,
        snapshot=_snapshot(2023),
        outcome=RealizedOutcome(2024, 1),
        baseline_strategy=baseline,
    )
    result = run_case(case, [baseline], simulations_per_strategy=30, seed=3)
    assert result.leakage_safe
    assert result.selected_strategy == "baseline"
    assert result.actual_finish_position == 1
    assert result.baseline_expected_finish == 1.0
    assert result.selected_distance_from_actual == 0.0


def test_future_snapshot_is_rejected():
    baseline = _strategy("baseline")
    case = RaceBacktestCase(
        race_id=2,
        year=2024,
        prediction_cutoff_year=2024,
        snapshot=_snapshot(2025),
        outcome=RealizedOutcome(2024, 1),
        baseline_strategy=baseline,
    )
    with pytest.raises(ValueError, match="Leakage"):
        run_case(case, [baseline], simulations_per_strategy=10)


def test_walk_forward_counts_rejected_cases_and_can_continue():
    baseline = _strategy("baseline")
    cases = [
        RaceBacktestCase(1, 2023, 2023, _snapshot(2023), RealizedOutcome(2023, 1), baseline),
        RaceBacktestCase(2, 2024, 2024, _snapshot(2023), RealizedOutcome(2024, 1), baseline),
        RaceBacktestCase(3, 2025, 2024, _snapshot(2025), RealizedOutcome(2025, 1), baseline),
    ]
    report = walk_forward_evaluate(
        cases,
        lambda case: [case.baseline_strategy],
        simulations_per_strategy=20,
        seed=5,
        reject_leakage=False,
    )
    assert report.cases_attempted == 3
    assert report.cases_scored == 2
    assert report.leakage_rejected == 1
