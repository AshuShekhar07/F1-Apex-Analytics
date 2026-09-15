from math import isclose

import pytest

from race_strategy_pace_normalization_v1 import (
    PaceObservation,
    build_target_pace_distribution,
    normalize_race_pace,
)


def test_normalizes_driver_median_against_same_race_reference():
    rows = [
        PaceObservation(1, 10, 2024, "era2_18inch_groundeffect", "44", 1, 90.0),
        PaceObservation(1, 10, 2024, "era2_18inch_groundeffect", "44", 2, 90.2),
        PaceObservation(1, 10, 2024, "era2_18inch_groundeffect", "1", 1, 91.0),
        PaceObservation(1, 10, 2024, "era2_18inch_groundeffect", "1", 2, 91.2),
        PaceObservation(1, 10, 2024, "era2_18inch_groundeffect", "16", 1, 90.6),
    ]

    normalized = normalize_race_pace(rows)
    hamilton = next(row for row in normalized if row.driver_key == "44")
    max_ref = max(row.track_reference_seconds for row in normalized)

    assert isclose(hamilton.relative_gap_seconds, hamilton.driver_median_seconds - hamilton.track_reference_seconds)
    assert hamilton.normalized_lap_seconds == hamilton.driver_median_seconds
    assert max_ref == 90.6


def test_invalid_and_out_of_range_laps_are_filtered():
    rows = [
        PaceObservation(1, 10, 2024, "era2", "44", 1, 90.0, is_valid=True),
        PaceObservation(1, 10, 2024, "era2", "44", 2, 0.0, is_valid=True),
        PaceObservation(1, 10, 2024, "era2", "44", 3, 90.5, is_valid=False),
    ]

    normalized = normalize_race_pace(rows)
    assert len(normalized) == 1
    assert normalized[0].driver_key == "44"


def test_target_distribution_respects_track_and_era():
    rows = []
    for race_id, value in ((1, 90.0), (2, 90.2), (3, 89.9)):
        rows.append(
            __import__("race_strategy_pace_normalization_v1").NormalizedPaceObservation(
                race_id=race_id,
                track_id=10,
                season_year=2024,
                regulation_era="era2",
                driver_key="44",
                track_reference_seconds=value - 0.5,
                driver_median_seconds=value,
                relative_gap_seconds=0.5,
                normalized_lap_seconds=value,
            )
        )

    dist, warnings = build_target_pace_distribution(
        rows,
        target_track_id=10,
        target_era="era2",
        target_driver_key="44",
        min_races=3,
    )

    assert 89.9 <= dist.mean <= 90.2
    assert dist.std >= 0.05
    assert warnings == ()


def test_target_distribution_requires_matching_history():
    from race_strategy_pace_normalization_v1 import NormalizedPaceObservation

    rows = [
        NormalizedPaceObservation(1, 10, 2024, "era1", "44", 90.0, 90.5, 0.5, 90.5),
    ]

    with pytest.raises(ValueError, match="No target pace observations"):
        build_target_pace_distribution(
            rows,
            target_track_id=99,
            target_era="era1",
            target_driver_key="44",
        )
