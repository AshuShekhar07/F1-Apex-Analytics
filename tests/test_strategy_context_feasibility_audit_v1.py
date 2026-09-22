
"""Unit tests for the research-only strategy context feasibility audit."""

import pandas as pd

from strategy_context_feasibility_audit_v1 import (
    BucketStats,
    build_buckets,
    grid_band,
    pit_timing_bucket,
    reconstruct_observed_strategy_patterns,
    sparsity_label,
)


class Dummy:
    pass


def test_grid_band_fixed_boundary():
    assert grid_band(1) == "Top10"
    assert grid_band(10) == "Top10"
    assert grid_band(11) == "11+"


def test_pit_timing_bucket_boundaries():
    # Thirds are defined by exact fractions: <1/3, <2/3, otherwise late.
    assert pit_timing_bucket(10, 100) == "early"
    assert pit_timing_bucket(33, 100) == "early"
    assert pit_timing_bucket(34, 100) == "middle"
    assert pit_timing_bucket(66, 100) == "middle"
    assert pit_timing_bucket(67, 100) == "late"


def test_sparsity_boundaries():
    assert sparsity_label(4) == "<5"
    assert sparsity_label(5) == "5-7"
    assert sparsity_label(7) == "5-7"
    assert sparsity_label(8) == "8-9"
    assert sparsity_label(9) == "8-9"
    assert sparsity_label(10) == ">=10"


def test_real_median():
    stats = BucketStats(obs_per_race={1: 2, 2: 4, 3: 6, 4: 8})
    assert stats.median_obs_per_race() == 5.0


def test_strategy_pattern_uses_actual_pit_table_and_keeps_same_compound_stop():
    stints = pd.DataFrame(
        [
            {
                "race_id": 1,
                "race_entry_id": 10,
                "driver_id": 5,
                "stint_number": 1,
                "compound": "MEDIUM",
                "start_lap": 1,
                "end_lap": 20,
                "stint_length": 20,
                "total_race_laps": 60,
            },
            {
                "race_id": 1,
                "race_entry_id": 10,
                "driver_id": 5,
                "stint_number": 2,
                "compound": "MEDIUM",
                "start_lap": 21,
                "end_lap": 40,
                "stint_length": 20,
                "total_race_laps": 60,
            },
            {
                "race_id": 1,
                "race_entry_id": 10,
                "driver_id": 5,
                "stint_number": 3,
                "compound": "HARD",
                "start_lap": 41,
                "end_lap": 60,
                "stint_length": 20,
                "total_race_laps": 60,
            },
        ]
    )
    pits = pd.DataFrame(
        [
            {"race_id": 1, "race_entry_id": 10, "pit_lap": 20, "source": "fastf1"},
            {"race_id": 1, "race_entry_id": 10, "pit_lap": 40, "source": "fastf1"},
        ]
    )
    context = pd.DataFrame(
        [
            {
                "race_id": 1,
                "race_entry_id": 10,
                "driver_id": 5,
                "finishing_status": "Finished",
            }
        ]
    )

    patterns, all_obs, usable = reconstruct_observed_strategy_patterns(
        stints, pits, context, {1: 60}, {(1, 10): 60}
    )

    key = (1, 10)
    assert key in all_obs
    assert key in usable
    assert patterns[key]["stop_count"] == 2
    assert patterns[key]["compounds"] == ("MEDIUM", "MEDIUM", "HARD")
    assert patterns[key]["pit_buckets"] == ("middle", "late")


def test_unclassified_driver_is_not_usable():
    stints = pd.DataFrame(
        [
            {
                "race_id": 1,
                "race_entry_id": 10,
                "driver_id": 5,
                "stint_number": 1,
                "compound": "HARD",
                "start_lap": 1,
                "end_lap": 50,
                "stint_length": 50,
                "total_race_laps": 50,
            }
        ]
    )
    pits = pd.DataFrame(columns=["race_id", "race_entry_id", "pit_lap", "source"])
    context = pd.DataFrame(
        [
            {
                "race_id": 1,
                "race_entry_id": 10,
                "driver_id": 5,
                "finishing_status": "Retired",
            }
        ]
    )

    patterns, all_obs, usable = reconstruct_observed_strategy_patterns(
        stints, pits, context, {1: 50}, {(1, 10): 50}
    )

    assert (1, 10) in all_obs
    assert (1, 10) not in usable
    assert (1, 10) not in patterns


def test_bucket_builder_separates_all_and_usable_observations():
    context = pd.DataFrame(
        [
            {
                "race_id": 1,
                "race_entry_id": 10,
                "driver_id": 5,
                "track_id": 1,
                "regulation_era": "era2",
                "grid_band": "Top10",
            },
            {
                "race_id": 1,
                "race_entry_id": 11,
                "driver_id": 6,
                "track_id": 1,
                "regulation_era": "era2",
                "grid_band": "Top10",
            },
        ]
    )
    all_driver_races = {(1, 10), (1, 11)}
    usable_driver_races = {(1, 10)}
    patterns = {
        (1, 10): {
            "compounds": ("MEDIUM", "HARD"),
            "stop_count": 1,
            "pit_buckets": ("middle",),
        }
    }

    buckets = build_buckets(
        context,
        patterns,
        all_driver_races,
        usable_driver_races,
        lambda row: (row.track_id, row.regulation_era, row.grid_band),
    )

    stats = buckets[(1, "era2", "Top10")]
    assert stats.all_obs == 2
    assert stats.usable_obs == 1
    assert stats.median_obs_per_race() == 2.0
    assert len(stats.patterns) == 1


def test_observed_race_distance_not_nominal_track_distance():
    stints = pd.DataFrame(
        [
            {
                "race_id": 2,
                "race_entry_id": 20,
                "driver_id": 7,
                "stint_number": 1,
                "compound": "MEDIUM",
                "start_lap": 1,
                "end_lap": 40,
                "stint_length": 40,
            },
            {
                "race_id": 2,
                "race_entry_id": 20,
                "driver_id": 7,
                "stint_number": 2,
                "compound": "HARD",
                "start_lap": 41,
                "end_lap": 50,
                "stint_length": 10,
            },
        ]
    )
    pits = pd.DataFrame(
        [
            {"race_id": 2, "race_entry_id": 20, "pit_lap": 40, "source": "fastf1"},
        ]
    )
    context = pd.DataFrame(
        [
            {
                "race_id": 2,
                "race_entry_id": 20,
                "driver_id": 7,
                "finishing_status": "Finished",
            }
        ]
    )

    patterns, _, usable = reconstruct_observed_strategy_patterns(
        stints, pits, context, {2: 50}, {(2, 20): 50}
    )

    assert (2, 20) in usable
    assert patterns[(2, 20)]["pit_buckets"] == ("late",)

def test_classified_lapped_driver_uses_driver_laps_for_coverage():
    stints = pd.DataFrame(
        [
            {
                "race_id": 3,
                "race_entry_id": 30,
                "driver_id": 8,
                "stint_number": 1,
                "compound": "SOFT",
                "start_lap": 1,
                "end_lap": 48,
                "stint_length": 48,
            }
        ]
    )
    pits = pd.DataFrame(columns=["race_id", "race_entry_id", "pit_lap", "source"])
    context = pd.DataFrame(
        [
            {
                "race_id": 3,
                "race_entry_id": 30,
                "driver_id": 8,
                "finishing_status": "Lapped",
            }
        ]
    )

    patterns, _, usable = reconstruct_observed_strategy_patterns(
        stints,
        pits,
        context,
        {3: 50},
        {(3, 30): 48},
        pd.DataFrame(),
    )

    assert (3, 30) in usable
    assert patterns[(3, 30)]["stop_count"] == 0
