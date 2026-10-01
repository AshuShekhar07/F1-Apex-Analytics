from datetime import date

import pytest

from race_reliability_v1 import K, Start, expected_position_ranks, field_rate, race_probabilities, team_dnf_probability


def _history():
    """SYNTHETIC: 30 races, teams 1 and 2 (two cars each); team 2 retires one car every race."""
    out = []
    for r in range(30):
        when = date(2024, 1, 1).replace(day=1 + r % 28, month=1 + r // 28)
        for team in (1, 2):
            out.append(Start(when, r, team, True))
            out.append(Start(when, r, team, team == 1))
    return out


def test_team_rate_is_shrunk_toward_the_field_and_walk_forward():
    history = _history()
    as_of = date(2024, 3, 1)
    base = field_rate(history, as_of)
    assert base == pytest.approx(0.25)                       # 1 of 4 cars per race
    p1 = team_dnf_probability(history, 1, as_of, base=base)
    p2 = team_dnf_probability(history, 2, as_of, base=base)
    assert p1 == pytest.approx((0 + K * 0.25) / (20 + K))   # last 10 races x 2 cars, no retirements
    assert p2 == pytest.approx((10 + K * 0.25) / (20 + K))
    assert p1 < base < p2
    # nothing from the race date itself or later is used
    assert race_probabilities(history, date(2024, 1, 1), [1, 2]) == {1: 0.15, 2: 0.15}


def test_expected_position_moves_fragile_cars_down_only_when_close():
    assert expected_position_ranks([1, 2, 3], [0.3, 0.05, 0.05]) == [1, 2, 3]   # 0.7 + 1.2 = 1.9 < 2.1: kept
    assert expected_position_ranks([1, 2, 3], [0.5, 0.05, 0.05]) == [2, 1, 3]   # 0.5 + 2.0 = 2.5 > 2.1: drops
    assert expected_position_ranks([1, 5, 9], [0.3, 0.05, 0.05]) == [1, 2, 3]   # far apart: order kept
