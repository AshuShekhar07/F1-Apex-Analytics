"""Safety contract for finishing-position model artifacts.

The historical ``finishing_position_model.pkl`` is intentionally retained for
research reproducibility, but it is not a production artifact.  Code that
simulates or publishes predictions must validate this contract before using a
bundle.
"""

from __future__ import annotations


LEGACY_ARTIFACT = "finishing_position_model.pkl"
CANDIDATE_ARTIFACT = "finishing_position_model_candidate.pkl"
CONTRACT_VERSION = 1

# These fields contain target-race outcomes or are computed with target/future
# race outcomes in the legacy feature pipeline.
UNSAFE_FEATURES = frozenset(
    {
        "weather_sensitivity",
        "tire_degradation_rate",
        "safety_car_periods",
        "vsc_periods",
        "finishing_position",
        "points",
        "status",
        "gap_to_winner_seconds",
        "starting_grid_position",
        "grid_penalty",
        "racecraft_delta",
    }
)

# These are candidate-only, post-qualifying features.  Their artifact is never
# accepted by the season simulator, which operates before qualifying.
SAFE_POST_QUALIFYING_FEATURES = [
    "quali_position",
    "form_avg_finish",
    "form_std_finish",
    "team_pace_recent",
    "track_history_avg_finish",
    "track_history_avg_quali",
    "teammate_relative_skill",
    "difficulty_rating",
    "post_2022_era",
    "circuit_type_code",
]


class ModelSafetyError(RuntimeError):
    """Raised when an artifact cannot be used safely in production."""


def validate_bundle(bundle: dict, *, serving_mode: str) -> None:
    """Reject unsafe, unversioned, or timing-incompatible model bundles."""
    if not isinstance(bundle, dict):
        raise ModelSafetyError("Finishing-position artifact must be a dictionary bundle.")

    features = bundle.get("features")
    if not isinstance(features, list):
        raise ModelSafetyError("Finishing-position artifact has no valid feature list.")

    leaked = sorted(set(features) & UNSAFE_FEATURES)
    if leaked:
        raise ModelSafetyError(
            "Rejected finishing-position artifact contains unsafe outcome features: "
            + ", ".join(leaked)
        )

    metadata = bundle.get("metadata")
    if not isinstance(metadata, dict):
        raise ModelSafetyError(
            "Finishing-position artifact has no safety metadata and is not production-eligible."
        )

    if metadata.get("contract_version") != CONTRACT_VERSION:
        raise ModelSafetyError("Finishing-position artifact has an unsupported contract version.")

    if metadata.get("prediction_timing") != serving_mode:
        raise ModelSafetyError(
            "Finishing-position artifact timing does not match this serving path "
            f"({metadata.get('prediction_timing')!r} != {serving_mode!r})."
        )

    if metadata.get("status") != "production_approved":
        raise ModelSafetyError(
            "Finishing-position artifact is not production-approved; candidate artifacts cannot be served."
        )


def assert_season_publication_allowed() -> None:
    """Keep the legacy season-prediction publishing path fail-closed.

    The only available candidate contract is post-qualifying, while season
    simulation uses a pre-qualifying proxy.  A separately validated,
    production-approved pre-qualifying model is required before this hold can
    be removed.
    """
    raise ModelSafetyError(
        "Season prediction publication is disabled: no validated, production-approved "
        "pre-qualifying finishing-position model exists."
    )
