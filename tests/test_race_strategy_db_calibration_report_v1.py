from race_strategy_calibration_v1 import (
    EventCalibrationObservation,
    PaceCalibrationObservation,
    PitCalibrationObservation,
    TyreCalibrationObservation,
)
from race_strategy_db_calibration_report_v1 import build_report
from race_strategy_data_adapter_v1 import DatabaseCalibrationDataset


def test_report_renders_available_calibration_and_explicit_gaps():
    dataset = DatabaseCalibrationDataset(
        tyre_observations=tuple(
            TyreCalibrationObservation("MEDIUM", age, 0.06 * age)
            for age in range(1, 5)
        ),
        event_observations=(
            EventCalibrationObservation(
                total_laps=100,
                safety_car_count=2,
                vsc_count=1,
                red_flag_count=0,
                wet_laps=20,
            ),
        ),
        pit_observations=(),
        pace_observations=(),
        warnings=("wet exposure missing for one other race",),
    )

    report = build_report(dataset)

    assert "tyre observations : 4" in report
    assert "event observations: 1" in report
    assert "MEDIUM" in report
    assert "sc_probability_per_lap_dry" in report
    assert "pit service/pit-lane observations: unavailable" in report
    assert "absolute pace observations: unavailable" in report
    assert "wet exposure missing for one other race" in report


def test_report_is_safe_for_empty_available_observations():
    dataset = DatabaseCalibrationDataset(
        tyre_observations=(),
        event_observations=(),
        pit_observations=(),
        pace_observations=(),
        warnings=(),
    )

    report = build_report(dataset)

    assert "unavailable: no usable tyre observations" in report
    assert "unavailable: no event observations" in report
    assert "pit service/pit-lane observations: unavailable" in report
    assert "absolute pace observations: unavailable" in report
