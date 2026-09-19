import pytest

from race_strategy_tyres_walkforward_v1 import validate_target_year
from race_strategy_calibration_v1 import TyreCalibrationObservation


def _row(year, era, stint, compound, age, delta):
    return TyreCalibrationObservation(
        compound=compound,
        tyre_age_laps=age,
        lap_time_delta_seconds=delta,
        wet_state="dry",
        stint_key=stint,
    )


def test_helper_module_imports():
    assert validate_target_year


def test_empty_year_returns_zero_scored_stints():
    rows = (
        TyreCalibrationObservation("MEDIUM", 1, 0.1, stint_key="s"),
    )
    result = validate_target_year(rows, target_year=2024, era="era2")
    assert result["scored_stints"] == 0
