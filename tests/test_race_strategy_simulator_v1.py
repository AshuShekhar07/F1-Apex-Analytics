import pytest

from race_strategy_simulator_v1 import (
    Distribution,
    RaceContext,
    SimulationParameters,
    Strategy,
    StrategyStint,
    TyreAllocation,
    build_candidate_strategies,
    evaluate_strategy,
    validate_strategy,
)


def context():
    return RaceContext(
        total_laps=50,
        starting_grid=2,
        our_base_pace_seconds=Distribution(90.0),
        tyre_degradation_per_lap={
            "SOFT": Distribution(0.08),
            "MEDIUM": Distribution(0.06),
            "HARD": Distribution(0.05),
            "INTERMEDIATE": Distribution(0.04),
            "WET": Distribution(0.03),
        },
        pit_stop_seconds=Distribution(2.4, 0.1, 2.0, 3.0),
        pit_lane_loss_seconds=Distribution(22.0, 0.5, 18.0, 28.0),
    )


def test_rejects_illegal_start_tyre():
    strategy = Strategy(
        "M-H",
        (StrategyStint("MEDIUM", 1, 25), StrategyStint("HARD", 26, 50)),
    )
    # Include the mandatory-start set so this test reaches the intended
    # strategy-level validation rather than failing allocation validation first.
    allocation = TyreAllocation({"SOFT": 1, "MEDIUM": 1, "HARD": 1}, "SOFT")
    with pytest.raises(ValueError, match="must start"):
        validate_strategy(strategy, allocation, 50)


def test_rejects_too_many_sets():
    strategy = Strategy(
        "M-M-H",
        (
            StrategyStint("MEDIUM", 1, 15),
            StrategyStint("MEDIUM", 16, 30),
            StrategyStint("HARD", 31, 50),
        ),
    )
    allocation = TyreAllocation({"MEDIUM": 1, "HARD": 1})
    with pytest.raises(ValueError, match="uses 2 MEDIUM"):
        validate_strategy(strategy, allocation, 50)


def test_candidate_builder_respects_mandatory_start():
    allocation = TyreAllocation({"MEDIUM": 1, "HARD": 1}, "MEDIUM")
    candidates = build_candidate_strategies(
        50,
        [("SOFT", "HARD"), ("MEDIUM", "HARD")],
        allocation,
        stop_offsets=(0,),
    )
    assert candidates
    assert all(c.sequence[0] == "MEDIUM" for c in candidates)


def test_evaluation_is_reproducible():
    strategy = Strategy(
        "M-H",
        (StrategyStint("MEDIUM", 1, 25), StrategyStint("HARD", 26, 50)),
    )
    allocation = TyreAllocation({"MEDIUM": 1, "HARD": 1}, "MEDIUM")
    params = SimulationParameters()
    a = evaluate_strategy(strategy, context(), [], allocation, params, simulations=100, seed=42)
    b = evaluate_strategy(strategy, context(), [], allocation, params, simulations=100, seed=42)
    assert a == b
    assert 0 <= a.win_probability <= 1
    assert 0 <= a.podium_probability <= 1
    assert a.p1_low <= a.win_probability or a.p1_low < 0.5
    assert a.p1_high >= a.win_probability or a.p1_high > 0.5
