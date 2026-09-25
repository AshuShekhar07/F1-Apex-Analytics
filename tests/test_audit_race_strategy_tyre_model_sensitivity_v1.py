import pytest

from audit_race_strategy_tyre_model_sensitivity_v1 import (
    DEFAULT_MIN_STINT_AGE_SPAN_GRID,
    DEFAULT_MIN_TRAINING_RACES_GRID,
    DEFAULT_MIN_UNIQUE_PIT_LAPS_GRID,
    _summary_for_config,
)


def test_default_sensitivity_grid_has_expected_size():
    assert len(DEFAULT_MIN_UNIQUE_PIT_LAPS_GRID) == 3
    assert len(DEFAULT_MIN_STINT_AGE_SPAN_GRID) == 4
    assert len(DEFAULT_MIN_TRAINING_RACES_GRID) == 3
    assert (
        len(DEFAULT_MIN_UNIQUE_PIT_LAPS_GRID)
        * len(DEFAULT_MIN_STINT_AGE_SPAN_GRID)
        * len(DEFAULT_MIN_TRAINING_RACES_GRID)
        == 36
    )


def test_summary_reports_v2_improvement_against_flat():
    # _score_config returns race scores keyed by model (changed in ffefff4).
    summary = {
        "raw": [{"race_score_rmse": 3.0, "race_score_correlation": -0.1, "target_race_id": 1}],
        "v2": [{"race_score_rmse": 2.4, "race_score_correlation": 0.2, "target_race_id": 1}],
        "flat": [{"race_score_rmse": 2.5, "race_score_correlation": None, "target_race_id": 1}],
    }
    stability = [
        {"low_confidence_training": False},
        {"low_confidence_training": True},
    ]
    result = _summary_for_config(summary, stability, 4, 4, 15)
    assert result["v2_vs_flat_improvement_pct"] == pytest.approx(4.0)
    assert result["v2_vs_raw_improvement_pct"] == pytest.approx(20.0)
    assert result["v2_target_races"] == 1
    assert result["low_confidence_stability_rows"] == 1
