"""Race-form signal test: must find a planted race-pace edge and not invent one."""

import uuid
from datetime import date

import numpy as np
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from race_form_signal_v1 import (
    DriverForm,
    build_rows,
    choose_weight,
    evaluate,
    form_signal,
    predictions,
    ranks,
    team_form,
)
from race_pace_from_quali_v1 import load_observations
from test_race_strategy_backtest_v2 import _seed_synthetic_seasons


def test_team_form_uses_only_earlier_races_and_shrinks():
    history = [(date(2024, 1, d), d, -0.01) for d in range(1, 11)]
    # last 5 races before Jan 8 are days 3..7, all -0.01 -> -0.05 / (5 + 2)
    assert team_form(history, 1, date(2024, 1, 8)) == pytest.approx(-0.05 / 7)
    assert team_form(history, 1, date(2024, 1, 1)) == 0.0          # nothing earlier
    assert team_form([(date(2024, 1, 1), 1, 0.02)], 1, date(2024, 2, 1)) == pytest.approx(0.02 / 3)


def test_ranks_and_blend():
    assert ranks([3.0, 1.0, 2.0]) == [3, 1, 2]
    rows = [DriverForm(1, 2024, d, g, g, True, gap, 0.0, None)
            for d, (g, gap) in enumerate([(1, 0.02), (2, 0.0), (3, 0.01)])]
    p = predictions(rows, 0.0)
    assert p["grid"] == [1, 2, 3] and p["blend"] == [1, 2, 3]
    assert p["pace_form"] == [3, 1, 2]
    assert predictions(rows, 1.0)["blend"] == [3, 1, 2]


def test_choose_weight_prefers_what_worked_before():
    grid_right = {1: [DriverForm(1, 2023, d, d, d, True, -d, 0.0, None) for d in range(1, 6)]}
    assert choose_weight(grid_right) == 0.0
    pace_right = {1: [DriverForm(1, 2023, d, d, 6 - d, True, -d, 0.0, None) for d in range(1, 6)]}
    assert choose_weight(pace_right) > 0.5   # smallest weight that already reproduces the pace order


def test_form_signal_needs_data():
    assert np.isnan(form_signal([])[0])


def _synthetic(race_bonus):
    import os

    from schema_migrations import apply_schema

    server_url = os.getenv("APEX21_TEST_DATABASE_URL")
    if not server_url:
        pytest.skip("APEX21_TEST_DATABASE_URL not set")
    name = f"apex21_form_{uuid.uuid4().hex[:8]}"
    admin = create_engine(server_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    engine = create_engine(make_url(server_url).set(database=name))
    try:
        with engine.begin() as conn:
            apply_schema(conn)
            _seed_synthetic_seasons(conn, np.random.default_rng(4), race_bonus=race_bonus)
        with engine.connect() as db:
            observations = load_observations(db, start_year=2018, end_year=2024)
            rows = build_rows(db, observations, start_year=2023, end_year=2024)
        return evaluate(rows, score_from=2024)
    finally:
        engine.dispose()
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def test_planted_race_pace_edge_is_detected_and_beats_grid():
    # team 6 (drivers 11-12, back of the grid) is 0.6 s/lap faster in races than quali implies
    result = _synthetic({6: 0.6})
    r, low, high, n = result["signal"]
    assert low > 0, result["signal"]
    assert result["mae"]["pace_form"] < result["mae"]["grid"]


def test_no_edge_means_no_signal():
    result = _synthetic({})
    r, low, high, n = result["signal"]
    assert low <= 0 <= high or abs(r) < 0.3, result["signal"]
