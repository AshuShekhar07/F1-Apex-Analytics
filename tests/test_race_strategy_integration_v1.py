import random

import pytest

from race_strategy_calibration_v1 import (
    CalibrationConfig,
    PaceCalibrationObservation,
    PitCalibrationObservation,
    calibrate_inputs,
)
from race_strategy_data_adapter_v1 import load_calibration_dataset
from race_strategy_simulator_v1 import (
    Distribution,
    RaceContext,
    Strategy,
    StrategyStint,
    TyreAllocation,
    evaluate_strategy,
)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)


class _FakeDB:
    """Minimal SQLAlchemy-like fixture for adapter-to-calibration tests."""

    def __init__(self):
        self.event_rows = [
            {
                "race_id": 1,
                "season_year": 2024,
                "round_number": 1,
                "total_race_laps": 50,
                "safety_car_periods": 1,
                "vsc_periods": 2,
                "red_flags": 0,
                "rainfall": False,
                "rain_onset_lap": None,
            }
        ]
        self.tyre_rows = [
            {
                "race_id": 1,
                "race_entry_id": 10,
                "stint_number": 1,
                "compound": "MEDIUM",
                "start_lap": 1,
                "end_lap": 6,
                "lap_number": lap,
                "lap_time": 90.0 + 0.10 * lap,
            }
            for lap in range(1, 7)
        ]

    def execute(self, statement, params):
        sql = str(statement)
        if "FROM races r" in sql:
            return _FakeResult(self.event_rows)
        if "FROM race_stints rs" in sql:
            return _FakeResult(self.tyre_rows)
        raise AssertionError(f"Unexpected SQL fixture: {sql[:120]}")


def _calibrated_inputs():
    dataset = load_calibration_dataset(_FakeDB(), start_year=2024, end_year=2024)
    assert dataset.tyre_observations
    assert dataset.event_observations
    assert dataset.pit_observations == ()
    assert dataset.pace_observations == ()

    pit = [
        PitCalibrationObservation(2.3, 21.5),
        PitCalibrationObservation(2.5, 22.0),
        PitCalibrationObservation(2.4, 22.5),
    ]
    pace = [
        PaceCalibrationObservation(90.0),
        PaceCalibrationObservation(90.1),
        PaceCalibrationObservation(89.9),
    ]
    return calibrate_inputs(
        tyre_observations=dataset.tyre_observations,
        event_observations=dataset.event_observations,
        pit_observations=pit,
        pace_observations=pace,
        config=CalibrationConfig(event_min_laps=1),
    )


def test_adapter_output_can_feed_calibration():
    calibrated = _calibrated_inputs()

    assert "MEDIUM" in calibrated.tyre_degradation_per_lap
    assert calibrated.tyre_degradation_per_lap["MEDIUM"].mean >= 0
    assert calibrated.pit_stop_seconds.mean > 0
    assert calibrated.pit_lane_loss_seconds.mean > 0
    assert calibrated.our_base_pace_seconds is not None
    assert calibrated.sc_probability_per_lap_dry > 0
    assert calibrated.vsc_probability_per_lap_dry > 0


def test_calibrated_inputs_propagate_into_simulator():
    calibrated = _calibrated_inputs()
    pace = calibrated.our_base_pace_seconds
    assert pace is not None

    context = RaceContext(
        total_laps=20,
        starting_grid=1,
        our_base_pace_seconds=pace,
        tyre_degradation_per_lap=calibrated.tyre_degradation_per_lap,
        pit_stop_seconds=calibrated.pit_stop_seconds,
        pit_lane_loss_seconds=calibrated.pit_lane_loss_seconds,
        sc_probability_per_lap=calibrated.sc_probability_per_lap_dry,
        vsc_probability_per_lap=calibrated.vsc_probability_per_lap_dry,
        red_flag_probability_per_lap=calibrated.red_flag_probability_per_lap_dry,
    )
    strategy = Strategy(
        "M-M",
        (StrategyStint("MEDIUM", 1, 10), StrategyStint("MEDIUM", 11, 20)),
    )
    allocation = TyreAllocation({"MEDIUM": 2}, "MEDIUM")

    result = evaluate_strategy(
        strategy,
        context,
        [],
        allocation,
        calibrated.to_simulation_parameters(),
        simulations=50,
        seed=11,
    )

    assert result.simulations == 50
    assert result.win_probability == 1.0
    assert result.expected_finish == 1.0
    assert result.strategy.sequence == ("MEDIUM", "MEDIUM")


def test_missing_adapter_inputs_remain_explicit():
    dataset = load_calibration_dataset(_FakeDB(), start_year=2024, end_year=2024)

    assert dataset.pit_observations == ()
    assert dataset.pace_observations == ()
    assert any("Pit service-time" in warning for warning in dataset.warnings)
    assert any("absolute pace" in warning for warning in dataset.warnings)

    with pytest.raises(ValueError, match="pit-stop observation"):
        calibrate_inputs(
            tyre_observations=dataset.tyre_observations,
            event_observations=dataset.event_observations,
            pit_observations=dataset.pit_observations,
            pace_observations=dataset.pace_observations,
        )


def test_full_calibration_and_simulation_are_reproducible():
    calibrated_a = _calibrated_inputs()
    calibrated_b = _calibrated_inputs()
    assert calibrated_a == calibrated_b

    context = RaceContext(
        total_laps=20,
        starting_grid=1,
        our_base_pace_seconds=calibrated_a.our_base_pace_seconds,
        tyre_degradation_per_lap=calibrated_a.tyre_degradation_per_lap,
        pit_stop_seconds=calibrated_a.pit_stop_seconds,
        pit_lane_loss_seconds=calibrated_a.pit_lane_loss_seconds,
        sc_probability_per_lap=calibrated_a.sc_probability_per_lap_dry,
        vsc_probability_per_lap=calibrated_a.vsc_probability_per_lap_dry,
        red_flag_probability_per_lap=calibrated_a.red_flag_probability_per_lap_dry,
    )
    strategy = Strategy(
        "M",
        (StrategyStint("MEDIUM", 1, 20),),
    )
    allocation = TyreAllocation({"MEDIUM": 1}, "MEDIUM")

    first = evaluate_strategy(
        strategy,
        context,
        [],
        allocation,
        calibrated_a.to_simulation_parameters(),
        simulations=25,
        seed=99,
    )
    second = evaluate_strategy(
        strategy,
        context,
        [],
        allocation,
        calibrated_b.to_simulation_parameters(),
        simulations=25,
        seed=99,
    )
    assert first == second
