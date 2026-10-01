from types import SimpleNamespace

from audit_race_strategy_tyre_model_sensitivity_v3 import strategy_pattern_diversity


def row(race_id, driver_id, stint_key, start_lap, compound):
    return SimpleNamespace(
        race_id=race_id,
        driver_id=driver_id,
        stint_key=stint_key,
        start_lap=start_lap,
        compound=compound,
    )


def test_strategy_pattern_diversity_counts_distinct_sequences():
    rows = [
        row(1, 10, "1:10:1", 1, "MEDIUM"),
        row(1, 10, "1:10:2", 20, "HARD"),
        row(1, 11, "1:11:1", 1, "SOFT"),
        row(1, 11, "1:11:2", 15, "MEDIUM"),
        row(1, 12, "1:12:1", 1, "MEDIUM"),
        row(1, 12, "1:12:2", 20, "HARD"),
    ]
    assert strategy_pattern_diversity(rows) == {1: 2}


def test_strategy_pattern_diversity_is_race_specific():
    rows = [
        row(1, 10, "1:10:1", 1, "MEDIUM"),
        row(1, 10, "1:10:2", 20, "HARD"),
        row(2, 11, "2:11:1", 1, "SOFT"),
    ]
    assert strategy_pattern_diversity(rows) == {1: 1, 2: 1}
