import pytest

from audit_race_strategy_tyre_model_walkforward_v3 import (
    StintScore,
    _aggregate_stint_scores,
)


def test_race_score_weights_stints_equally_across_compounds():
    scores = (
        StintScore("m1", "MEDIUM", 1.0, 0.0, 3),
        StintScore("h1", "HARD", 0.5, 1.0, 3),
        StintScore("h2", "HARD", 0.0, 3.0, 6),
        StintScore("h3", "HARD", -0.5, 5.0, 9),
    )

    race = _aggregate_stint_scores(scores)

    assert race.n_stints == 4
    assert race.n_points == 21
    assert race.rmse == pytest.approx(2.25)
    assert race.correlation == pytest.approx(0.25)


def test_empty_race_score_is_empty():
    race = _aggregate_stint_scores(tuple())
    assert race.n_stints == 0
    assert race.n_points == 0
    assert race.rmse is None
    assert race.correlation is None
