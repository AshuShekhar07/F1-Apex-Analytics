import pytest

from audit_race_strategy_tyre_model_walkforward_v2 import _ols, _pearson


def test_ols_tolerates_float_rounding():
    fit = _ols([1.0, 2.0, 3.0], [0.0, 0.1, 0.2])
    assert fit is not None
    assert fit[0] == pytest.approx(0.1)
    assert fit[1] == pytest.approx(-0.1)


def test_pearson_detects_perfect_linear_shape():
    assert _pearson([0.0, 0.1, 0.2], [0.0, 1.0, 2.0]) is not None
    assert _pearson([0.0, 0.1, 0.2], [0.0, 1.0, 2.0]) > 0.999
