import pytest

from finishing_position_contract import (
    ModelSafetyError,
    assert_season_publication_allowed,
    validate_bundle,
)


def test_legacy_leaky_bundle_is_rejected():
    bundle = {
        "model": object(),
        "features": ["quali_position", "tire_degradation_rate"],
        "residual_std": 1.0,
    }

    with pytest.raises(ModelSafetyError, match="unsafe outcome features"):
        validate_bundle(bundle, serving_mode="pre_qualifying")


def test_post_qualifying_candidate_cannot_serve_pre_qualifying_simulation():
    bundle = {
        "model": object(),
        "features": ["quali_position", "form_avg_finish"],
        "residual_std": 1.0,
        "metadata": {
            "contract_version": 1,
            "prediction_timing": "post_qualifying",
            "status": "candidate",
        },
    }

    with pytest.raises(ModelSafetyError, match="timing does not match"):
        validate_bundle(bundle, serving_mode="pre_qualifying")


def test_season_prediction_publication_is_fail_closed():
    with pytest.raises(ModelSafetyError, match="publication is disabled"):
        assert_season_publication_allowed()
