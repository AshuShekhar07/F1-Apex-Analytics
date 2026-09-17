from audit_race_strategy_strategy_diversity_v1 import (
    build_race_diversity,
    build_strategy_sequences,
    threshold_summary,
)
from audit_race_strategy_tyre_model_v2 import LapRow


def _row(race_id, driver_id, stint_number, compound):
    start = 1 if stint_number == 1 else 10 * stint_number
    return LapRow(
        race_id=race_id,
        season_year=2025,
        era="era2_18inch_groundeffect",
        driver_id=driver_id,
        stint_key=f"{race_id}:{driver_id}:{stint_number}",
        compound=compound,
        start_lap=start,
        end_lap=start + 5,
        lap_number=start + 1,
        lap_time=90.0,
    )


def test_distinct_strategy_sequences_are_counted_once():
    rows = [
        _row(1, 10, 1, "MEDIUM"),
        _row(1, 10, 2, "HARD"),
        _row(1, 11, 1, "MEDIUM"),
        _row(1, 11, 2, "HARD"),
        _row(1, 12, 1, "SOFT"),
        _row(1, 12, 2, "MEDIUM"),
    ]

    sequences = build_strategy_sequences(rows)
    assert sequences[1] == {("MEDIUM", "HARD"), ("SOFT", "MEDIUM")}
    assert build_race_diversity(rows)[1] == 2


def test_threshold_summary_filters_on_pattern_count():
    result = threshold_summary({1: 1, 2: 2, 3: 3}, thresholds=(1, 2, 3))
    assert result[0]["eligible_races"] == 3
    assert result[1]["eligible_races"] == 2
    assert result[2]["eligible_races"] == 1
