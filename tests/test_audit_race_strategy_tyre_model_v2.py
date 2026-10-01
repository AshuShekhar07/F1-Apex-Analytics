from audit_race_strategy_tyre_model_v2 import LapRow, audit_pooled_rows, build_field_relative_rows


def _row(race, driver, stint, compound, start, end, lap, time, era="era2"):
    return LapRow(race, 2024, era, driver, f"{race}:{driver}:{stint}", compound, start, end, lap, time)


def test_field_relative_rows_remove_shared_race_lap_pace():
    rows = []
    # Shared race-lap pace rises by 0.5s/lap; driver 1 has a 0.1s/lap tyre-age effect.
    # Four other cars anchor the field median so the tested car cannot determine it.
    for lap in range(1, 9):
        shared = 90.0 + 0.5 * lap
        rows.extend([
            _row(1, 1, 1, "MEDIUM", 1, 8, lap, shared + 0.1 * max(0, lap - 1)),
            _row(1, 2, 1, "MEDIUM", 1, 8, lap, shared - 0.30),
            _row(1, 3, 1, "MEDIUM", 1, 8, lap, shared - 0.10),
            _row(1, 4, 1, "SOFT", 1, 8, lap, shared + 0.10),
            _row(1, 5, 2, "SOFT", 5, 8, lap, shared + 0.30 if lap >= 5 else shared),
        ])
    diversity = {1: 4}
    pooled, coverage = build_field_relative_rows(rows, diversity, min_unique_pit_laps=4, min_stint_age_span=2)

    assert coverage["eligible_races"] == 1
    medium = pooled["era2|MEDIUM"]
    assert medium
    report = audit_pooled_rows(pooled)["era2|MEDIUM"]
    assert report.slope is not None
    assert report.slope > 0


def test_races_without_strategy_diversity_are_excluded():
    rows = [
        _row(1, 1, 1, "MEDIUM", 1, 10, lap, 90.0 + lap)
        for lap in range(1, 11)
    ]
    pooled, coverage = build_field_relative_rows(rows, {1: 2}, min_unique_pit_laps=4)
    assert coverage["eligible_races"] == 0
    assert not pooled


def test_pooled_slope_is_not_clipped_to_positive():
    pooled = {
        "era2|HARD": [
            (1, 0.10, "s1", "1"),
            (2, 0.00, "s1", "1"),
            (3, -0.10, "s1", "1"),
            (1, 0.12, "s2", "2"),
            (2, 0.02, "s2", "2"),
            (3, -0.08, "s2", "2"),
        ]
    }
    report = audit_pooled_rows(pooled)["era2|HARD"]
    assert report.slope < 0
    assert report.ci_low is not None
    assert report.ci_high is not None
