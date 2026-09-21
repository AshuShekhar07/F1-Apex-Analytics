import pytest

from race_strategy_tyre_age_contrast_v1 import (
    TyreAgeContrast,
    fit_pair_models,
    fit_hierarchical_slopes,
    score_target,
)


def _synthetic_pair(*, track_id=10, slope=0.06, offset=0.8, pair="r:1:11-12"):
    return tuple(
        TyreAgeContrast(
            race_id=1,
            track_id=track_id,
            season_year=2024,
            era="era2",
            pair_key=pair,
            compound="MEDIUM",
            age_difference=float(age),
            lap_time_difference=offset + slope * age,
        )
        for age in (-4, -2, 0, 2, 4)
    )


def test_pair_fit_recovers_age_effect_with_pair_offset():
    rows = _synthetic_pair()
    fits = fit_pair_models(rows, min_points=5, min_age_span=2)
    fit = fits[("era2", "MEDIUM", 10, 1)][0]
    assert fit.slope == pytest.approx(0.06, abs=1e-9)


def test_hierarchical_track_slope_shrinks_toward_global():
    train = (
        _synthetic_pair(track_id=10, slope=0.10, pair="r1:1:11-12")
        + _synthetic_pair(track_id=20, slope=0.04, pair="r2:1:11-12")
        + _synthetic_pair(track_id=20, slope=0.04, pair="r3:1:13-14")
    )
    model = fit_hierarchical_slopes(train, prior_strength=4.0)
    local = model[("era2", "MEDIUM", 20)]
    assert local.global_slope == pytest.approx(0.06, abs=1e-9)
    assert 0.04 < local.slope < 0.06


def test_score_target_uses_zero_as_strong_baseline():
    train = (
        _synthetic_pair(track_id=10, slope=0.06, pair="r1:1:11-12")
        + _synthetic_pair(track_id=20, slope=0.06, pair="r2:1:11-12")
    )
    target = (
        TyreAgeContrast(3, 20, 2025, "era2", "r3:1:11-12", "MEDIUM", -4, 0.8 - 0.24),
        TyreAgeContrast(3, 20, 2025, "era2", "r3:1:11-12", "MEDIUM", -2, 0.8 - 0.12),
        TyreAgeContrast(3, 20, 2025, "era2", "r3:1:11-12", "MEDIUM", 0, 0.8),
        TyreAgeContrast(3, 20, 2025, "era2", "r3:1:11-12", "MEDIUM", 2, 0.8 + 0.12),
        TyreAgeContrast(3, 20, 2025, "era2", "r3:1:11-12", "MEDIUM", 4, 0.8 + 0.24),
    )
    result = score_target(train, target)
    assert result["model_mae"] < result["flat_mae"]
    assert result["improvement_pct"] > 0
