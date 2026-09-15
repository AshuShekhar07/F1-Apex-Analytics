import math

import pytest

from race_strategy_calibration_v1 import (
    CalibrationConfig,
    EventCalibrationObservation,
    PaceCalibrationObservation,
    PitCalibrationObservation,
    TyreCalibrationObservation,
    calibrate_event_hazards,
    calibrate_inputs,
    calibrate_pace,
    calibrate_pit_stops,
    calibrate_tyre_degradation,
    robust_distribution,
    smoothed_event_probability,
)


def test_robust_distribution_reduces_single_outlier_impact():
    dist = robust_distribution([90.0, 90.2, 89.9, 90.1, 140.0], min_std=0.01)
    assert dist.mean < 95.0
    assert dist.upper is None


def test_smoothed_event_probability_shrinks_small_samples():
    raw = 1 / 10
    smoothed = smoothed_event_probability(1, 10, alpha=1, beta=99)
    assert 0 < smoothed < raw


def test_event_hazards_are_exposure_adjusted():
    rows = [
        EventCalibrationObservation(total_laps=50, safety_car_count=1, vsc_count=2, red_flag_count=0, wet_laps=0),
        EventCalibrationObservation(total_laps=50, safety_car_count=2, vsc_count=1, red_flag_count=1, wet_laps=20),
    ]
    hazards, warnings = calibrate_event_hazards(rows, config=CalibrationConfig(event_min_laps=1))
    assert hazards["sc_probability_per_lap_dry"] > 0
    assert hazards["vsc_probability_per_lap_wet"] > 0
    assert hazards["red_flag_probability_per_lap_wet"] > hazards["red_flag_probability_per_lap_dry"]
    assert warnings == ()


def test_tyre_degradation_estimate_is_non_negative():
    rows = [
        TyreCalibrationObservation("MEDIUM", age, 0.05 * age + jitter)
        for age, jitter in [(1, 0.00), (2, 0.01), (3, -0.01), (4, 0.00), (5, 0.01)]
    ] * 5
    calibrated, warnings = calibrate_tyre_degradation(rows)
    assert calibrated["MEDIUM"].mean >= 0
    assert calibrated["MEDIUM"].upper == 0.5
    assert warnings == () or any("Low tyre" in w for w in warnings)


def test_pit_and_pace_calibration():
    pit = [PitCalibrationObservation(2.3 + i * 0.01, 22.0 + i * 0.1) for i in range(25)]
    pace_rows = [PaceCalibrationObservation(90.0 + (i % 5) * 0.1) for i in range(40)]
    service, lane, pit_warnings = calibrate_pit_stops(pit)
    pace, pace_warnings = calibrate_pace(pace_rows)
    assert 1.5 <= service.mean <= 5.5
    assert 10.0 <= lane.mean <= 40.0
    assert pace.lower == 40.0
    assert pit_warnings == ()
    assert pace_warnings == ()


def test_calibrate_inputs_produces_complete_calibrated_object():
    tyre_rows = [TyreCalibrationObservation("HARD", age, 0.04 * age) for age in range(1, 31)]
    event_rows = [EventCalibrationObservation(total_laps=100, safety_car_count=2, vsc_count=2, wet_laps=25)]
    pit_rows = [PitCalibrationObservation(2.4, 22.0) for _ in range(25)]
    pace_rows = [PaceCalibrationObservation(90.0 + (i % 3) * 0.05) for i in range(35)]

    calibrated = calibrate_inputs(
        tyre_observations=tyre_rows,
        event_observations=event_rows,
        pit_observations=pit_rows,
        pace_observations=pace_rows,
        config=CalibrationConfig(event_min_laps=1),
    )

    assert "HARD" in calibrated.tyre_degradation_per_lap
    assert calibrated.our_base_pace_seconds is not None
    assert calibrated.report.observations == {
        "tyre": 30,
        "events": 1,
        "pit_stops": 25,
        "pace": 35,
    }
    assert all(math.isfinite(value) for value in (
        calibrated.sc_probability_per_lap_dry,
        calibrated.vsc_probability_per_lap_wet,
        calibrated.red_flag_probability_per_lap_wet,
    ))


def test_empty_inputs_fail_loudly():
    with pytest.raises(ValueError):
        robust_distribution([])
    with pytest.raises(ValueError):
        calibrate_event_hazards([])
    with pytest.raises(ValueError):
        calibrate_pit_stops([])
