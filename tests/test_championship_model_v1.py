from datetime import date, timedelta

import numpy as np
import pytest
from sqlalchemy import text

from championship_model_v1 import (
    Result,
    Weekend,
    fit_grid_coef,
    fit_strengths,
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
    coef = fit_grid_coef(weekends, lambda before, w: fit_strengths(before, era=w.era))
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
    from championship_backtest_v1 import championship_backtest, strength_caches
    from championship_model_v1 import load_weekends

    with synthetic_db.connect() as db:
        weekends = load_weekends(db, start_year=2018)
    raced = [w for w in weekends if w.raced]
    assert len(raced) == 6 and all(len(w.race) == 12 for w in raced)
    out = championship_backtest(weekends, strength_caches(raced), 2022, 2024)
    assert [c[0] for c in out["checks"]] == [2022, 2023, 2024]
    from championship_backtest_v1 import DEFAULT_SETTING, SETTINGS
    assert out["chosen"][2022] == DEFAULT_SETTING and out["setting_now"] in SETTINGS
    assert out["chosen_drift"][2022] == DEFAULT_SETTING and out["fixed_log_loss"]
    from championship_backtest_v1 import summarise_titles
    summary = summarise_titles(out["rounds"], "drivers")
    assert summary["records"]["model, season cars + drift"] and summary["records"]["model, development drift"] and summary["records"]["points leader wins"]


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


def test_season_cars_let_a_new_car_overtake_last_years_dominant_one():
    # 2023: team 1 (drivers 1, 2) dominates 20 races; 2024: team 2 (drivers 3, 4) wins 6 races
    old = [_weekend(i, [1, 2, 3, 4, 5, 6], season=2023) for i in range(1, 21)]
    new = [_weekend(20 + i, [3, 4, 1, 2, 5, 6], season=2024) for i in range(1, 7)]
    shared = fit_strengths(old + new)
    season = fit_strengths(old + new, winter_sd=0.5, season=2024)
    gap_shared = shared.car[2] - shared.car[1]
    gap_season = season.car[2] - season.car[1]
    assert gap_season > gap_shared and gap_season > 0
    # before 2024 has been raced, the 2024 car is last year's car (the prior mean)
    pre = fit_strengths(old, winter_sd=0.5, season=2024)
    assert pre.car[1] > pre.car[2]
    # season cars change nothing within one season
    one = fit_strengths(old, winter_sd=0.5, season=2023)
    assert one.car[1] == pytest.approx(fit_strengths(old).car[1], abs=1e-3)


def test_project_season_uses_only_rounds_up_to_the_projection(synthetic_db):
    from championship_model_v1 import load_weekends, project_season

    with synthetic_db.connect() as db:
        weekends = load_weekends(db, start_year=2018)
    season = max(w.season for w in weekends if w.raced)
    rounds = sorted(w.round for w in weekends if w.season == season and w.raced)
    first = project_season(weekends, season, after_round=rounds[0], sims=2000)
    last = project_season(weekends, season, sims=2000)
    assert last.after_round == rounds[-1] and first.after_round == rounds[0]
    assert sum(first.projection.p_driver_title.values()) == pytest.approx(1.0)
    assert last.projection.remaining_weekends == 0
    # season over: the title goes to the points leader(s) with certainty
    top = max(last.driver_points.values())
    leaders = [d for d, p in last.driver_points.items() if p == top]
    assert sum(last.projection.p_driver_title[d] for d in leaders) == pytest.approx(1.0)
    with pytest.raises(ValueError):
        project_season(weekends, 2099)


def test_projection_is_stored_and_served(synthetic_db, monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker

    import championship_model_v1
    from championship_projection_v1 import update

    monkeypatch.setattr(championship_model_v1, "SIMS", 2000)
    with synthetic_db.begin() as db:
        season = db.execute(text("SELECT MAX(season_year) FROM races")).scalar()
        written = update(db, season, all_rounds=True)
        assert written and update(db, season, all_rounds=True) == []          # nothing left to do
        n = db.execute(text("SELECT COUNT(*) FROM championship_projections")).scalar()
        assert n > 0

    import os
    os.environ.setdefault("DATABASE_URL", synthetic_db.url.render_as_string(hide_password=False))
    from app.database import get_db
    from app.main import app

    Session = sessionmaker(bind=synthetic_db)

    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = override
    try:
        client = TestClient(app)
        body = client.get(f"/championship/{season}").json()
        assert body["after_round"] == max(written) and body["weekends_left"] == 0 and body["stored"]
        assert set(body["confidence"]) == {"drivers", "constructors"}
        assert sum(d["p_title"] for d in body["drivers"]) == pytest.approx(1.0, abs=0.01)
        assert body["constructors"][0]["team_name"] and body["validation"]["constructors_title"]
        history = client.get(f"/championship/{season}/history").json()
        assert len(history["drivers"][0]["rounds"]) == len(written)
        assert client.get("/championship/2099").status_code == 404
        # nothing stored for an earlier season: computed live from the data there is
        with synthetic_db.begin() as db:
            db.execute(text("DELETE FROM championship_projections"))
        live = client.get(f"/championship/{season}").json()
        assert live["stored"] is False and live["after_round"] == max(written)
        assert sum(d["p_title"] for d in live["drivers"]) == pytest.approx(1.0, abs=0.01)
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_db, None)
        else:                                   # other modules share the app: put theirs back
            app.dependency_overrides[get_db] = previous


def test_season_lineup_tops_a_team_up_to_two_cars():
    from championship_model_v1 import season_lineup

    full = _weekend(1, [1, 2, 3, 4, 5, 6])
    missing = _weekend(2, [1, 2, 3, 5, 6])          # driver 4 (team 2) has no result stored
    lineup = season_lineup([full, missing])
    assert lineup[4] == 2 and len(lineup) == 6
    # a seat swap is not undone: the new driver keeps the seat, the old one is not added back
    swapped = _weekend(3, [1, 2, 3, 7, 5, 6], teams={1: 1, 2: 1, 3: 2, 7: 2, 5: 3, 6: 3})
    lineup = season_lineup([full, swapped])
    assert lineup[7] == 2 and 4 not in lineup


def _second_season(order, races=8):
    from dataclasses import replace
    return [replace(_weekend(12 + i, order, season=2024), round=i) for i in range(1, races + 1)]


def _two_seasons():
    old = [_weekend(i, [1, 2, 3, 4, 5, 6], season=2023) for i in range(1, 13)]
    new = _second_season([3, 4, 1, 2, 5, 6])
    for w in new[4:]:
        w.race.clear()                                   # rounds 5-8 not raced yet
    return old + new


def test_pre_season_projection_uses_earlier_seasons_only():
    from championship_model_v1 import project_season

    weekends = _two_seasons()
    pre = project_season(weekends, 2024, after_round=0, sims=3000)
    assert pre.after_round == 0 and pre.driver_points == {} and pre.projection.remaining_weekends == 8
    # last season's dominant team is the pre-season favourite...
    assert max(pre.projection.p_team_title, key=pre.projection.p_team_title.get) == 1
    # ...and four races of the new pecking order move the chances the other way
    now = project_season(weekends, 2024, sims=3000)
    assert now.after_round == 4 and now.projection.p_team_title[2] > pre.projection.p_team_title[2]
    lineup = {d: (d + 1) // 2 for d in range(1, 7)}
    assert project_season(weekends, 2024, after_round=0, lineup=lineup, sims=500).lineup == lineup
    with pytest.raises(ValueError):
        project_season(weekends, 2030)


def test_title_confidence_by_stage():
    from championship_model_v1 import title_backtest, title_confidence, title_stage

    assert [title_stage(r, 20) for r in (0, 3, 5, 10, 15, 16, 20)] == \
        ["pre_season", "early", "early", "mid", "mid", "late", "late"]
    table = {"drivers": {"pre_season": {"rounds": 8, "favourite_won": 0.5, "mean_favourite_p": 0.4},
                         "late": {"rounds": 60, "favourite_won": 0.95, "mean_favourite_p": 0.9}}}
    assert title_confidence("drivers", 0, 20, table)["level"] == "medium"
    assert title_confidence("drivers", 19, 20, table)["level"] == "high"
    assert title_confidence("constructors", 5, 20, table)["measured"] is False
    weekends = [w for w in _two_seasons() if w.season == 2023] + _second_season([1, 2, 3, 4, 5, 6])
    out = title_backtest(weekends, 2024, 2024, sims=300)
    assert out["drivers"]["late"]["favourite_won"] == 1.0 and out["constructors"]["pre_season"]["rounds"] == 1
