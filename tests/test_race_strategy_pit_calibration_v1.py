import pytest

from race_strategy_pit_calibration_v1 import (
    calibrate_total_pit_lane_loss,
    context_with_total_pit_lane_calibration,
)
from race_strategy_pit_db_adapter_v1 import StoredPitLaneObservation
from race_strategy_simulator_v1 import Distribution, RaceContext


def _rows():
    values = [21.0, 22.0, 23.0, 24.0, 23.5, 22.5, 23.2, 24.1, 25.0, 21.8]
    return tuple(
        StoredPitLaneObservation(i, i, 10, value, "fastf1")
        for i, value in enumerate(values, start=1)
    )


def test_calibrates_total_pit_lane_time_without_service_split():
    result = calibrate_total_pit_lane_loss(_rows(), min_observations=5)
    assert result.observations == 10
    assert 21.0 <= result.total_pit_lane_seconds.mean <= 25.0
    assert result.total_pit_lane_seconds.lower == 10.0
    assert result.total_pit_lane_seconds.upper == 60.0
    assert any("not separately identified" in warning for warning in result.warnings)


def test_context_uses_observed_total_without_double_counting():
    context = RaceContext(
        total_laps=50,
        starting_grid=1,
        our_base_pace_seconds=Distribution(90.0),
        tyre_degradation_per_lap={"MEDIUM": Distribution(0.05)},
        pit_stop_seconds=Distribution(2.4),
        pit_lane_loss_seconds=Distribution(20.0),
    )
    calibrated = calibrate_total_pit_lane_loss(_rows())
    updated = context_with_total_pit_lane_calibration(context, calibrated)
    assert updated.pit_stop_seconds.mean == 0.0
    assert updated.pit_lane_loss_seconds == calibrated.total_pit_lane_seconds


def test_empty_or_invalid_total_pit_data_fails():
    with pytest.raises(ValueError, match="total pit-lane"):
        calibrate_total_pit_lane_loss(())
