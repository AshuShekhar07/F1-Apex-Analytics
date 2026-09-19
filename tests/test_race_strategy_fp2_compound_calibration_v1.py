import pytest

from race_strategy_fp2_compound_calibration_v1 import FP2CompoundObservation, calibrate_fp2_compound_pace


def _row(session, race, driver, lap, compound, offset, age):
    return FP2CompoundObservation(
        session_id=session,
        race_id=race,
        season_year=2024,
        regulation_era="era2_18inch_groundeffect",
        driver_key=driver,
        lap_number=lap,
        stint_age=age,
        compound=compound,
        lap_time_seconds=90.0 + 0.01 * lap + offset,
    )


def test_fp2_calibration_recovers_offsets():
    rows = []
    for driver in ("1", "2", "3"):
        for age, lap in enumerate((10, 11, 12), start=1):
            rows.append(_row(1, 1, driver, lap, "MEDIUM", 0.0, age))
        for age, lap in enumerate((20, 21, 22), start=1):
            rows.append(_row(1, 1, driver, lap, "SOFT", -0.25, age))
        for age, lap in enumerate((30, 31, 32), start=1):
            rows.append(_row(1, 1, driver, lap, "HARD", 0.20, age))
    result = calibrate_fp2_compound_pace(rows, min_pairs_per_group=1, max_lap_distance=15)
    assert result.offsets_seconds["SOFT"] == pytest.approx(-0.25, abs=0.02)
    assert result.offsets_seconds["HARD"] == pytest.approx(0.20, abs=0.02)


def test_empty_comparisons_fail_loudly():
    rows = [
        _row(1, 1, "1", 10, "MEDIUM", 0.0, 1),
        _row(1, 1, "1", 11, "MEDIUM", 0.0, 2),
        _row(1, 1, "1", 12, "MEDIUM", 0.0, 3),
    ]
    result = calibrate_fp2_compound_pace(rows)
    assert result.group_counts["SOFT"] == 0
    assert result.group_counts["HARD"] == 0
    assert any("No usable" in warning for warning in result.warnings)
