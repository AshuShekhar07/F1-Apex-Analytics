import random

import pytest

from race_strategy_simulator_v1 import (
    ALL_COMPOUNDS,
    Distribution,
    RaceContext,
    SimulationParameters,
    Strategy,
    StrategyStint,
    TyreAllocation,
    WeatherTrajectory,
    build_candidate_strategies,
    evaluate_strategy,
    sample_events,
    sample_weather,
    simulate_once,
    tyre_penalty,
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


def test_distribution_is_deterministic_when_std_is_zero():
    rng = random.Random(123)
    distribution = Distribution(5.0)
    assert [distribution.sample(rng) for _ in range(5)] == [5.0] * 5


def test_distribution_respects_bounds():
    rng = random.Random(7)
    distribution = Distribution(100.0, 50.0, lower=90.0, upper=110.0)
    samples = [distribution.sample(rng) for _ in range(500)]
    assert all(90.0 <= value <= 110.0 for value in samples)


def test_tyres_allow_only_known_compounds_and_non_negative_counts():
    TyreAllocation({compound: 0 for compound in ALL_COMPOUNDS}).validate()

    with pytest.raises(ValueError, match="Unknown compound"):
        TyreAllocation({"SUPERSOFT": 1}).validate()

    with pytest.raises(ValueError, match="cannot be negative"):
        TyreAllocation({"MEDIUM": -1}).validate()


def test_rejects_illegal_start_tyre():
    strategy = Strategy(
        "M-H",
        (StrategyStint("MEDIUM", 1, 25), StrategyStint("HARD", 26, 50)),
    )
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


def test_rejects_non_contiguous_strategy():
    strategy = Strategy(
        "M-H",
        (StrategyStint("MEDIUM", 1, 25), StrategyStint("HARD", 27, 50)),
    )
    allocation = TyreAllocation({"MEDIUM": 1, "HARD": 1})
    with pytest.raises(ValueError, match="contiguous"):
        validate_strategy(strategy, allocation, 50)


def test_rejects_strategy_that_does_not_cover_race():
    strategy = Strategy(
        "M-H",
        (StrategyStint("MEDIUM", 1, 24), StrategyStint("HARD", 25, 49)),
    )
    allocation = TyreAllocation({"MEDIUM": 1, "HARD": 1})
    with pytest.raises(ValueError, match="entire race"):
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


def test_weather_without_valid_rain_inputs_is_dry():
    params = SimulationParameters(
        weather_onset_lap=Distribution(20.0),
        weather_duration_laps=Distribution(0.0),
        rain_intensity_mm_h=Distribution(8.0),
    )
    weather = sample_weather(params, 50, random.Random(1))
    assert weather.rain_onset_lap is None
    assert weather.rain_duration_laps == 0
    assert weather.intensity_mm_h == 0.0
    assert not weather.is_wet(20)


def test_weather_classification_matches_v1_severity_rules():
    severe = WeatherTrajectory(10, 5, 5.0, 30.0)
    crossover = WeatherTrajectory(10, 5, 2.0, 30.0)
    assert severe.is_severe(10)
    assert not severe.is_crossover(10)
    assert crossover.is_crossover(10)
    assert not crossover.is_severe(10)
    assert not severe.is_wet(15)


def test_wet_weather_penalizes_dry_tyres_more_than_intermediate():
    weather = WeatherTrajectory(10, 10, 3.0, 30.0)
    ctx = context()
    dry_penalty = tyre_penalty("MEDIUM", 0, 10, weather, ctx, 1.0)
    inter_penalty = tyre_penalty("INTERMEDIATE", 0, 10, weather, ctx, 1.0)
    assert dry_penalty > inter_penalty


def test_dry_weather_penalizes_wet_compounds():
    weather = WeatherTrajectory(None, 0, 0.0, 30.0)
    ctx = context()
    assert tyre_penalty("INTERMEDIATE", 0, 20, weather, ctx, 1.0) > 0
    assert tyre_penalty("WET", 0, 20, weather, ctx, 1.0) > 0


def test_simulation_returns_valid_finish_position_and_weather():
    strategy = Strategy(
        "M-H",
        (StrategyStint("MEDIUM", 1, 25), StrategyStint("HARD", 26, 50)),
    )
    allocation = TyreAllocation({"MEDIUM": 1, "HARD": 1}, "MEDIUM")
    result = simulate_once(
        strategy,
        context(),
        [],
        allocation,
        SimulationParameters(),
        random.Random(42),
    )
    assert 1 <= result.finish_position
    assert result.winner
    assert result.strategy_name == "M-H"
    assert result.total_time_seconds >= 40.0 * context().total_laps


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
    assert 0 <= a.p1_low <= a.p1_high <= 1


def test_simulation_event_classes_are_mutually_exclusive_per_lap():
    ctx = RaceContext(
        total_laps=50,
        starting_grid=2,
        our_base_pace_seconds=Distribution(90.0),
        tyre_degradation_per_lap={"MEDIUM": Distribution(0.06)},
        pit_stop_seconds=Distribution(2.4),
        pit_lane_loss_seconds=Distribution(22.0),
        sc_probability_per_lap=0.4,
        vsc_probability_per_lap=0.4,
        red_flag_probability_per_lap=0.4,
    )
    weather = WeatherTrajectory(None, 0, 0.0, 30.0)
    events = sample_events(ctx, weather, random.Random(8))
    assert not (set(events.safety_car_laps) & set(events.vsc_laps))
    assert not (set(events.safety_car_laps) & set(events.red_flag_laps))
    assert not (set(events.vsc_laps) & set(events.red_flag_laps))


def test_optimizer_rejects_unknown_objective():
    from race_strategy_simulator_v1 import optimize_strategies
    with pytest.raises(ValueError, match="objective"):
        optimize_strategies([], context(), [], TyreAllocation({"MEDIUM": 1, "HARD": 1}), SimulationParameters(), objective="unknown")


def test_optimizer_can_use_expected_finish_objective(monkeypatch):
    from race_strategy_simulator_v1 import StrategyEvaluation, optimize_strategies
    a = Strategy("A", (StrategyStint("MEDIUM", 1, 25), StrategyStint("HARD", 26, 50)))
    b = Strategy("B", (StrategyStint("HARD", 1, 25), StrategyStint("MEDIUM", 26, 50)))
    evaluations = {
        "A": StrategyEvaluation(a, 100, 0.60, 0.80, 8.0, 0.50, 0.70),
        "B": StrategyEvaluation(b, 100, 0.55, 0.85, 5.0, 0.45, 0.65),
    }

    def fake_evaluate(strategy, *args, **kwargs):
        return evaluations[strategy.name]

    monkeypatch.setattr("race_strategy_simulator_v1.evaluate_strategy", fake_evaluate)
    alloc = TyreAllocation({"MEDIUM": 1, "HARD": 1})
    candidates = [a, b]
    p1 = optimize_strategies(candidates, context(), [], alloc, SimulationParameters(), objective="p1")
    finish = optimize_strategies(candidates, context(), [], alloc, SimulationParameters(), objective="expected_finish")
    assert p1[0].strategy.name == "A"
    assert finish[0].strategy.name == "B"
