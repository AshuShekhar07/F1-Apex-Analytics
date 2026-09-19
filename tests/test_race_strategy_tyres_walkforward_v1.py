import pytest

from race_strategy_tyres_walkforward_v1 import validate_target_year
from race_strategy_calibration_v1 import TyreCalibrationObservation


def test_helper_module_imports():
    assert validate_target_year


def test_empty_year_returns_zero_scored_stints():
    rows = (
        TyreCalibrationObservation("MEDIUM", 1, 0.1, stint_key="s"),
    )
    result = validate_target_year(rows, target_year=2024, era="era2")
    assert result["training_observations"] == 0
    assert result["target_observations"] == 0
    assert result["scored_stints"] == 0
    assert "slope_baseline_mae_seconds_per_lap" in result
    assert "slope_improvement_pct" in result
    assert "baseline_mae_seconds" in result
    assert "prediction_mae_improvement_pct" in result


def test_target_year_only_uses_earlier_training_years():
    rows = []
    for year in (2020, 2021):
        for age in range(1, 6):
            rows.append(
                TyreCalibrationObservation(
                    "MEDIUM",
                    age,
                    0.10 * age if year == 2020 else 10.0 + 0.10 * age,
                    stint_key=f"{year}-stint",
                    season_year=year,
                    regulation_era="era1",
                )
            )

    result = validate_target_year(tuple(rows), target_year=2021, era="era1")
    assert result["training_observations"] == 5
    assert result["target_observations"] == 5
    assert result["scored_stints"] == 1
    assert result["slope_mae_seconds_per_lap"] == pytest.approx(0.0, abs=1e-9)
