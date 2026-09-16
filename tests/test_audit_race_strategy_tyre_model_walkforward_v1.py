from audit_race_strategy_tyre_model_walkforward_v1 import ModelFit, _ols, _pearson, score_target
from audit_race_strategy_tyre_model_v2 import LapRow


def _row(driver, stint, compound, lap, time, start=1, end=8):
    return LapRow(1, 2024, "era2", driver, f"1:{driver}:{stint}", compound, start, end, lap, time)


def test_ols_estimates_positive_slope():
    fit = _ols([1.0, 2.0, 3.0], [0.0, 0.1, 0.2])
    assert fit == (0.1, -0.1)


def test_pearson_is_perfect_for_linear_prediction():
    assert _pearson([0.0, 1.0, 2.0], [0.5, 1.5, 2.5]) == 1.0


def test_score_target_uses_flat_zero_baseline_and_reports_rmse():
    rows = [_row(1, 1, "MEDIUM", lap, 90.0 + 0.1 * lap) for lap in range(1, 9)]
    score = score_target(rows, "MEDIUM", 0.1)
    assert score.n_stints == 1
    assert score.n_points >= 3
    assert score.rmse < 1e-9
    assert score.correlation is None


def test_score_target_positive_slope_is_better_than_flat_for_constructed_stint():
    rows = [_row(1, 1, "MEDIUM", lap, 90.0 + 0.2 * lap) for lap in range(1, 9)]
    model = score_target(rows, "MEDIUM", 0.2)
    flat = score_target(rows, "MEDIUM", 0.0)
    assert model.rmse < flat.rmse
