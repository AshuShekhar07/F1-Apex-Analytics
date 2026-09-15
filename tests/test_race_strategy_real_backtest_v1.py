import pytest

from race_strategy_real_backtest_v1 import RaceTarget, candidate_sequences, build_candidates
from race_strategy_simulator_v1 import RaceContext, Distribution, TyreAllocation, SimulationParameters, Strategy, StrategyStint
from race_strategy_walkforward_v1 import InputSnapshot, RaceBacktestCase, RealizedOutcome


def _case() -> RaceBacktestCase:
    context = RaceContext(
        total_laps=50,
        starting_grid=1,
        our_base_pace_seconds=Distribution(90.0),
        tyre_degradation_per_lap={
            "SOFT": Distribution(0.08),
            "MEDIUM": Distribution(0.06),
            "HARD": Distribution(0.05),
        },
        pit_stop_seconds=Distribution(0.0),
        pit_lane_loss_seconds=Distribution(23.5),
    )
    baseline = Strategy(
        "MEDIUM → HARD [50%]",
        (StrategyStint("MEDIUM", 1, 25), StrategyStint("HARD", 26, 50)),
        source="fixed_baseline",
    )
    return RaceBacktestCase(
        race_id=1,
        year=2024,
        prediction_cutoff_year=2024,
        snapshot=InputSnapshot(
            available_year=2024,
            context=context,
            competitors=(),
            allocation=TyreAllocation({"SOFT": 2, "MEDIUM": 2, "HARD": 2}),
            parameters=SimulationParameters(),
        ),
        outcome=RealizedOutcome(2024, 1),
        baseline_strategy=baseline,
    )


def test_candidate_sequence_family_contains_one_and_two_stop_cases():
    sequences = candidate_sequences()
    assert ("MEDIUM", "HARD") in sequences
    assert ("SOFT", "HARD", "MEDIUM") in sequences
    assert all(2 <= len(seq) <= 3 for seq in sequences)


def test_build_candidates_are_legal_for_nominal_allocation():
    candidates = build_candidates(_case())
    assert candidates
    assert all(len(candidate.stints) in (2, 3) for candidate in candidates)
    assert all(candidate.stints[0].start_lap == 1 for candidate in candidates)
    assert all(candidate.stints[-1].end_lap == 50 for candidate in candidates)


def test_race_target_requires_positive_fields():
    target = RaceTarget(10, 2024, 5, "era2_18inch_groundeffect", 70, 1, 2, 1, 4, True)
    assert target.dry_confirmed
    assert target.starting_grid == 1
    assert target.actual_finish == 4
