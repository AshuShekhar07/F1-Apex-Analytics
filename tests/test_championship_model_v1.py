from datetime import date, timedelta

import numpy as np
import pytest

from championship_model_v1 import (
    Result,
    Weekend,
    fit_grid_coef,
    fit_strengths,
    new_car_sd,
    pl_loglik_grad,
    points_for,
    project_championship,
    race_probabilities,
    simulate_order,
    sprint_points,
    standings,
)


def _weekend(i, order, season=2024, era="e", sprint=False, teams=None, grid=None, dnf=()):
    teams = teams or {d: (d + 1) // 2 for d in order}
    race = [Result(d, teams[d], (grid or {}).get(d, pos), None if d in dnf else pos, d not in dnf,
                   (25, 18, 15, 12, 10, 8, 6, 4, 2, 1)[pos - 1] if pos <= 10 and d not in dnf else 0)
            for pos, d in enumerate(order, start=1)]
    return Weekend(i, season, i, date(season, 1, 1) + timedelta(days=7 * i), era, sprint, race, [])


def test_gradient_matches_numeric():
    s = np.array([0.3, -0.4, 1.1, 0.0, -0.2])
    ll, g = pl_loglik_grad(s)
    num = [(pl_loglik_grad(s + 1e-6 * np.eye(5)[i])[0] - ll) / 1e-6 for i in range(5)]
    assert np.allclose(g, num, atol=1e-4)


def test_strengths_rank_the_consistent_winner_first_and_split_car_from_driver():
    # driver 1 (team 1) wins every race; teammate 2 is second; team 2 (3, 4) next
    weekends = [_weekend(i, [1, 2, 3, 4, 5, 6]) for i in range(1, 11)]
    st = fit_strengths(weekends)
    assert st.of(1, 1) > st.of(2, 1) > st.of(3, 2) > st.of(6, 3)
    assert st.car[1] > st.car[2] > st.car[3]
    # a driver moving to the best team inherits its car
    assert st.of(6, 1) > st.of(6, 3)


def test_a_retirement_does_not_lower_strength():
    clean = [_weekend(i, [1, 2, 3, 4]) for i in range(1, 9)]
    with_dnf = clean[:-1] + [_weekend(8, [2, 3, 4, 1], dnf={1})]
    a, b = fit_strengths(clean), fit_strengths(with_dnf)
    assert b.of(1, 1) == pytest.approx(a.of(1, 1) * 1.0, rel=0.25)       # barely moves
    assert b.of(1, 1) > b.of(2, 1)


def test_simulated_orders_and_points():
    rng = np.random.default_rng(1)
    pos = simulate_order(np.array([5.0, 0.0, -5.0]), 0.0, rng, 2000)
    assert (np.sort(pos, axis=1) == [1, 2, 3]).all() and np.mean(pos[:, 0] == 1) > 0.95
    retired = simulate_order(np.zeros(3), 1.0, rng, 10)
    assert (retired == 0).all()
    assert list(points_for(np.array([1, 2, 0, 11]), (25, 18, 15))) == [25, 18, 0, 0]
    assert sprint_points(2021) == (3, 2, 1) and sprint_points(2023)[0] == 8 and sprint_points(2019) == ()


def test_race_probabilities_use_strength_and_grid():
    st = fit_strengths([_weekend(i, [1, 2, 3, 4]) for i in range(1, 9)])
    entrants = [(1, 1, 4), (2, 1, 1), (3, 2, 2), (4, 2, 3)]
    no_grid = race_probabilities(st, entrants, grid_coef=0.0, dnf=0.0, sims=4000)
    strong_grid = race_probabilities(st, entrants, grid_coef=3.0, dnf=0.0, sims=4000)
    assert no_grid.favourite == 1
    assert strong_grid.p_win[2] > no_grid.p_win[2]                        # pole helps
    assert sum(no_grid.p_win.values()) == pytest.approx(1.0, abs=1e-9)


def test_grid_coef_is_learned_from_earlier_races():
    # the pole sitter always wins regardless of who it is -> a clearly positive grid effect
    weekends = []
    for i in range(1, 25):
        order = [((i + k) % 6) + 1 for k in range(6)]
        weekends.append(_weekend(i, order, grid={d: p for p, d in enumerate(order, start=1)}))
    coef = fit_grid_coef(weekends, lambda before, era: fit_strengths(before, era=era))
    assert coef > 1.0


def test_championship_reflects_lead_and_races_left():
    weekends = [_weekend(i, [1, 2, 3, 4]) for i in range(1, 9)]
    st = fit_strengths(weekends)
    lineup = {1: 1, 2: 1, 3: 2, 4: 2}
    remaining = [_weekend(i, [1, 2, 3, 4]) for i in range(9, 12)]
    big_lead = project_championship(st, lineup, remaining, driver_points={1: 200, 2: 100}, team_points={1: 300},
                                    dnf=0.1, sims=4000)
    assert big_lead.p_driver_title[1] == pytest.approx(1.0) and big_lead.p_team_title[1] == pytest.approx(1.0)
    close = project_championship(st, lineup, remaining, driver_points={1: 100, 2: 95}, team_points={1: 195},
                                 dnf=0.1, sims=4000)
    assert 0.5 < close.p_driver_title[1] < 1.0 and close.p_driver_title[2] > 0.0
    assert sum(close.p_driver_title.values()) == pytest.approx(1.0)
    # a dropped driver keeps their points and can still be champion on paper
    dropped = project_championship(st, {1: 1, 3: 2}, [], driver_points={1: 10, 9: 50}, team_points={},
                                   dnf=0.1, sims=100)
    assert dropped.p_driver_title[9] == 1.0


def test_standings_include_sprints():
    w = _weekend(1, [1, 2])
    w.sprint = [Result(1, 1, 1, 1, True, 8.0)]
    d, t = standings([w], 2024)
    assert d[1] == 33 and t[1] == 51



def test_backtest_runs_on_the_synthetic_database(synthetic_db):
    from championship_backtest_v1 import StrengthCache, championship_backtest
    from championship_model_v1 import load_weekends

    with synthetic_db.connect() as db:
        weekends = load_weekends(db, start_year=2018)
    raced = [w for w in weekends if w.raced]
    assert len(raced) == 6 and all(len(w.race) == 12 for w in raced)
    out = championship_backtest(weekends, StrengthCache(raced), 2022, 2024)
    assert [c[0] for c in out["checks"]] == [2022, 2023, 2024]
    from championship_backtest_v1 import DEFAULT_SETTING, SETTINGS
    assert out["chosen"][2022] == DEFAULT_SETTING and out["setting_now"] in SETTINGS
    assert out["chosen_drift"][2022][1] == 0.0
    from championship_backtest_v1 import summarise_titles
    summary = summarise_titles(out["rounds"], "drivers")
    assert summary["records"]["model, drift + new-car doubt"] and summary["records"]["model, development drift"] and summary["records"]["points leader wins"]


from test_race_strategy_backtest_v2 import synthetic_db  # noqa: E402,F401  (fixture)


def test_development_drift_lowers_overconfidence_far_from_the_end():
    weekends = [_weekend(i, [1, 2, 3, 4, 5, 6]) for i in range(1, 9)]
    st = fit_strengths(weekends)
    lineup = {d: (d + 1) // 2 for d in range(1, 7)}
    remaining = [_weekend(i, [1, 2, 3, 4, 5, 6]) for i in range(9, 25)]
    pts = {1: 60, 3: 40}
    static = project_championship(st, lineup, remaining, driver_points=pts, team_points={1: 60, 2: 40}, dnf=0.1,
                                  sims=4000, drift=0.0)
    drifting = project_championship(st, lineup, remaining, driver_points=pts, team_points={1: 60, 2: 40}, dnf=0.1,
                                    sims=4000, drift=0.15)
    assert drifting.p_team_title[1] < static.p_team_title[1]
    assert sum(drifting.p_team_title.values()) == pytest.approx(1.0)


def test_new_car_doubt_shrinks_with_races_and_gives_the_rival_a_chance():
    assert new_car_sd(0.6, 0) == pytest.approx(0.6)
    assert new_car_sd(0.6, 4) == pytest.approx(0.6 / 2 ** 0.5) and new_car_sd(0.6, 20) < new_car_sd(0.6, 4)
    # team 1 dominated last season; before round 1 (no points yet) it must not be near-certain
    weekends = [_weekend(i, [1, 2, 3, 4, 5, 6]) for i in range(1, 21)]
    st = fit_strengths(weekends)
    lineup = {d: (d + 1) // 2 for d in range(1, 7)}
    remaining = [_weekend(i, [1, 2, 3, 4, 5, 6], season=2025) for i in range(1, 23)]
    sure = project_championship(st, lineup, remaining, driver_points={}, team_points={}, dnf=0.1, sims=3000)
    doubt = project_championship(st, lineup, remaining, driver_points={}, team_points={}, dnf=0.1, sims=3000,
                                 car_uncertainty=new_car_sd(1.0, 0))
    assert doubt.p_team_title[1] < sure.p_team_title[1] and doubt.p_team_title[2] > sure.p_team_title[2]
    assert sum(doubt.p_team_title.values()) == pytest.approx(1.0)
