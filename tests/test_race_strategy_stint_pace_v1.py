import pytest

from race_strategy_stint_pace_v1 import (
    StintPaceRow,
    _features,
    _fit_ridge,
    _predict_adjustment,
)


def _row(
    *,
    race_id: int,
    driver_id: int,
    lap: int,
    compound: str,
    age: int,
    lap_time: float,
    median_time: float = 90.0,
):
    return StintPaceRow(
        race_id=race_id,
        race_date=f"2024-0{race_id}-01",
        track_id=10,
        season_year=2024,
        era="era2",
        driver_id=driver_id,
        team_id=1,
        compound=compound,
        lap_number=lap,
        total_laps=10,
        tyre_age=age,
        lap_time=lap_time,
        driver_race_median=median_time,
    )


def test_features_change_with_compound_and_age():
    medium = _features("MEDIUM", 5, 5, 10)
    soft = _features("SOFT", 5, 5, 10)
    old_soft = _features("SOFT", 15, 5, 10)

    assert len(medium) == 10
    assert medium != soft
    assert soft != old_soft


def test_ridge_learns_synthetic_soft_age_penalty():
    rows = []
    for race_id in range(1, 8):
        for age in range(2, 10):
            baseline = 90.0
            rows.append(
                _row(
                    race_id=race_id,
                    driver_id=44,
                    lap=age + 1,
                    compound="SOFT",
                    age=age,
                    lap_time=baseline + 0.06 * age,
                )
            )
    for race_id in range(1, 8):
        for age in range(2, 10):
            rows.append(
                _row(
                    race_id=race_id,
                    driver_id=1,
                    lap=age + 1,
                    compound="MEDIUM",
                    age=age,
                    lap_time=baseline,
                )
            )

    model = _fit_ridge(tuple(rows), era="era2", ridge_alpha=0.1)
    prediction = _predict_adjustment(
        model,
        _row(
            race_id=99,
            driver_id=44,
            lap=9,
            compound="SOFT",
            age=8,
            lap_time=90.0,
        ),
    )
    neutral = _predict_adjustment(
        model,
        _row(
            race_id=99,
            driver_id=44,
            lap=9,
            compound="MEDIUM",
            age=2,
            lap_time=90.0,
        ),
    )
    assert prediction > neutral


def test_model_requires_sufficient_training_rows():
    rows = tuple(
        _row(
            race_id=i,
            driver_id=1,
            lap=3,
            compound="MEDIUM",
            age=2,
            lap_time=90.0,
        )
        for i in range(5)
    )
    with pytest.raises(ValueError, match="Insufficient training rows"):
        _fit_ridge(rows, era="era2")
