import pytest

from race_strategy_compound_calibration_v1 import CompoundPaceObservation, calibrate_compound_pace


def _row(race, driver, lap, compound, offset):
    return CompoundPaceObservation(
        race_id=race,
        season_year=2024,
        regulation_era="era2_18inch_groundeffect",
        driver_key=driver,
        lap_number=lap,
        tyre_age_laps=1 + (lap % 3),
        compound=compound,
        lap_time_seconds=90.0 + 0.01 * lap + offset,
    )


def test_recovers_compound_offsets_after_race_driver_fixed_effects():
    rows = []
    for race in range(1, 9):
        for driver in ("1", "2", "3"):
            rows.extend([
                _row(race, driver, 1, "MEDIUM", 0.0),
                _row(race, driver, 2, "MEDIUM", 0.0),
                _row(race, driver, 3, "MEDIUM", 0.0),
                _row(race, driver, 4, "SOFT", -0.25),
                _row(race, driver, 5, "SOFT", -0.25),
                _row(race, driver, 6, "SOFT", -0.25),
                _row(race, driver, 7, "HARD", 0.20),
                _row(race, driver, 8, "HARD", 0.20),
                _row(race, driver, 9, "HARD", 0.20),
            ])
    result = calibrate_compound_pace(rows, min_group_observations=3, max_lap_distance=6)
    assert result.reference_compound == "MEDIUM"
    assert result.offsets_seconds["SOFT"] == pytest.approx(-0.22, abs=0.02)
    assert result.offsets_seconds["HARD"] == pytest.approx(0.26, abs=0.02)


def test_requires_usable_groups():
    with pytest.raises(ValueError):
        calibrate_compound_pace([
            CompoundPaceObservation(
                race_id=1, season_year=2024, regulation_era="era2",
                driver_key="1", lap_number=2, tyre_age_laps=1,
                compound="MEDIUM", lap_time_seconds=90.0,
            )
        ])


def test_single_compound_groups_do_not_identify_offsets():
    rows = []
    for race in range(1, 5):
        rows.extend([
            _row(race, "1", 2, "MEDIUM", 0.0),
            _row(race, "1", 3, "MEDIUM", 0.0),
            _row(race, "1", 4, "MEDIUM", 0.0),
        ])
        rows.extend([
            _row(race, "2", 2, "SOFT", -0.25),
            _row(race, "2", 3, "SOFT", -0.25),
            _row(race, "2", 4, "SOFT", -0.25),
        ])
    with pytest.raises(ValueError, match="comparisons against MEDIUM"):
        calibrate_compound_pace(rows, min_group_observations=3)
