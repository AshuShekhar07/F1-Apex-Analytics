"""Forecast service and API, on the synthetic multi-season database."""

import uuid

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from race_forecast_v1 import (
    VALIDATION,
    ForecastError,
    Scenario,
    compute_forecast,
    run_what_if,
    shrunk_slot_rates,
    store_forecast,
    strategy_from_json,
)
from test_race_strategy_backtest_v2 import _seed_synthetic_seasons


def test_strategy_from_json_validates():
    s = strategy_from_json({"sequence": ["medium", "hard"], "stop_laps": [12]}, 30)
    assert s.sequence == ("MEDIUM", "HARD") and s.stop_laps == (12,)
    for bad in ({"sequence": ["MEDIUM", "HARD"], "stop_laps": []},
                {"sequence": ["MEDIUM", "HARD"], "stop_laps": [30]},
                {"sequence": ["SOFT", "MEDIUM", "HARD"], "stop_laps": [20, 10]}):
        with pytest.raises(ValueError):
            strategy_from_json(bad, 30)


@pytest.fixture(scope="module")
def forecast_db():
    import os

    from schema_migrations import apply_schema

    server_url = os.getenv("APEX21_TEST_DATABASE_URL")
    if not server_url:
        pytest.skip("APEX21_TEST_DATABASE_URL not set")
    name = f"apex21_fc_{uuid.uuid4().hex[:8]}"
    admin = create_engine(server_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    engine = create_engine(make_url(server_url).set(database=name))
    try:
        with engine.begin() as conn:
            apply_schema(conn)
            _seed_synthetic_seasons(conn, np.random.default_rng(4))
            # race 7: upcoming 2024 race with qualifying only; race 8: no qualifying yet
            for race_id, day in ((7, 20), (8, 27)):
                conn.execute(text("""
                    INSERT INTO races (id, track_id, season_year, round_number, race_date, regulation_era)
                    VALUES (:r, 1, 2024, :r, make_date(2024, 10, :d), 'e')
                """), {"r": race_id, "d": day})
            q = conn.execute(text("INSERT INTO sessions (race_id, session_type, start_time) VALUES (7, 'Q', now()) RETURNING id")).scalar()
            for d in range(1, 13):
                entry = conn.execute(text("""
                    INSERT INTO race_entries (race_id, driver_id, team_id, role, car_number)
                    VALUES (7, :d, :t, 'race_driver', :d) RETURNING id
                """), {"d": d, "t": (d + 1) // 2}).scalar()
                conn.execute(text("""
                    INSERT INTO qualifying_results (session_id, race_entry_id, q1_time, final_position)
                    VALUES (:s, :e, :t, :p)
                """), {"s": q, "e": entry, "t": 80 + 0.1 * d, "p": 13 - d})   # reversed: slowest on pole
            # race 9: first race of a NEW regulation era, qualifying only
            conn.execute(text("""
                INSERT INTO races (id, track_id, season_year, round_number, race_date, regulation_era)
                VALUES (9, 2, 2025, 1, make_date(2025, 3, 1), 'new'),
                       (10, 1, 2024, 10, make_date(2024, 11, 3), 'e')
            """))
            for race_id, with_times in ((9, True), (10, False)):
                q = conn.execute(text("INSERT INTO sessions (race_id, session_type, start_time) VALUES (:r, 'Q', now()) RETURNING id"),
                                 {"r": race_id}).scalar()
                for d in range(1, 13):
                    entry = conn.execute(text("""
                        INSERT INTO race_entries (race_id, driver_id, team_id, role, car_number)
                        VALUES (:r, :d, :t, 'race_driver', :d) RETURNING id
                    """), {"r": race_id, "d": d, "t": (d + 1) // 2}).scalar()
                    conn.execute(text("""
                        INSERT INTO qualifying_results (session_id, race_entry_id, q1_time, final_position)
                        VALUES (:s, :e, :t, :p)
                    """), {"s": q, "e": entry, "t": (80 + 0.1 * d) if with_times else None, "p": d})
        with engine.begin() as conn:
            for race_id in (5, 7):
                store_forecast(conn, compute_forecast(conn, race_id))
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture(scope="module")
def client(forecast_db):
    import os

    os.environ.setdefault("DATABASE_URL", forecast_db.url.render_as_string(hide_password=False))
    from app.database import get_db
    from app.main import app

    Session = sessionmaker(bind=forecast_db)

    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_finished_race_uses_official_grid(forecast_db):
    with forecast_db.connect() as db:
        forecast = compute_forecast(db, 5)
    assert forecast["grid_source"] == "starting_grid"
    assert sorted(d["predicted_position"] for d in forecast["drivers"]) == list(range(1, 13))
    assert forecast["strategy"]["most_common"]["sequence"] == ["MEDIUM", "HARD"]
    pole = min(forecast["drivers"], key=lambda d: d["grid"])
    back = max(forecast["drivers"], key=lambda d: d["grid"])
    assert pole["p_win"] > back["p_win"]


def test_upcoming_race_uses_qualifying_order(forecast_db):
    with forecast_db.connect() as db:
        forecast = compute_forecast(db, 7)
    assert forecast["grid_source"] == "qualifying"
    grid = {d["driver_id"]: d["grid"] for d in forecast["drivers"]}
    assert grid[12] == 1 and grid[1] == 12
    # predicted position = rank(grid rank + w * (pace rank - grid rank)), w chosen on the previous season
    from race_form_signal_v1 import ranks
    w = forecast["inputs"]["blend_weight"]
    drivers = forecast["drivers"]
    g = ranks([d["grid"] for d in drivers])
    p = ranks([d["pace"]["mean"] for d in drivers])
    assert [d["predicted_position"] for d in drivers] == ranks([a + w * (b - a) for a, b in zip(g, p)])


def test_no_qualifying_means_no_forecast(forecast_db):
    with forecast_db.connect() as db:
        with pytest.raises(ForecastError, match="qualifying"):
            compute_forecast(db, 8)


def test_forecast_endpoint(client):
    body = client.get("/races/7/forecast").json()
    assert body["grid_source"] == "qualifying" and body["model_version"] == "forecast_v1"
    positions = [d["predicted_position"] for d in body["drivers"]]
    assert positions == sorted(positions) and len(positions) == 12
    assert body["drivers"][0]["driver_name"].startswith("Driver")
    assert body["validation"] == VALIDATION
    assert body["strategy_alternatives"][0]["sequence"] == ["MEDIUM", "HARD"]
    assert client.get("/races/8/forecast").status_code == 404


def test_what_if_safety_car_and_strategy(client):
    body = client.post("/races/5/what-if", json={"safety_car_laps": [10, 11, 12], "simulations": 300}).json()
    assert len(body["results"]) == 12
    first = body["results"][0]
    assert {"baseline", "scenario", "expected_position_change"} <= set(first)

    three_stop = {"sequence": ["SOFT", "MEDIUM", "SOFT", "HARD"], "stop_laps": [7, 14, 21]}
    body = client.post("/races/5/what-if", json={"strategies": {"1": three_stop}, "simulations": 300}).json()
    driver_1 = next(r for r in body["results"] if r["driver_id"] == 1)
    assert driver_1["expected_position_change"] > 0      # two extra stops cost positions


@pytest.mark.parametrize("payload", [
    {"strategies": {"999": {"sequence": ["MEDIUM", "HARD"], "stop_laps": [10]}}},
    {"strategies": {"1": {"sequence": ["MEDIUM", "HARD"], "stop_laps": [40]}}},
    {"safety_car_laps": [99]},
])
def test_what_if_rejects_invalid_scenarios(client, payload):
    assert client.post("/races/5/what-if", json={**payload, "simulations": 100}).status_code == 422


def test_run_what_if_is_reproducible(forecast_db):
    with forecast_db.connect() as db:
        inputs = db.execute(text("SELECT inputs FROM race_forecasts WHERE race_id = 5")).scalar()
    a = run_what_if(inputs, Scenario(safety_car_laps=(5, 6)), sims=200, seed=3)
    b = run_what_if(inputs, Scenario(safety_car_laps=(5, 6)), sims=200, seed=3)
    assert a == b


def test_new_regulation_era_uses_recorded_fallbacks(forecast_db):
    with forecast_db.connect() as db:
        forecast = compute_forecast(db, 9)
    notes = " | ".join(forecast["inputs"]["data_notes"])
    assert "pace model: too few new races yet, using e relation" in notes
    assert "strategy: no dry new races yet, using e strategies" in notes
    assert "using all earlier eras" in notes and "using all eras" in notes
    assert "pit loss (green): no new evidence yet, using e" in notes
    assert forecast["strategy"]["most_common"]["sequence"] == ["MEDIUM", "HARD"]


def test_qualifying_rows_without_times_are_refused(forecast_db):
    with forecast_db.connect() as db:
        with pytest.raises(ForecastError, match="lap times"):
            compute_forecast(db, 10)


def test_partial_official_grid_is_mixed(forecast_db):
    with forecast_db.connect() as db:
        trans = db.begin()
        db.execute(text("""
            UPDATE race_results SET starting_grid_position = NULL
            WHERE race_entry_id = (SELECT id FROM race_entries WHERE race_id = 5 AND driver_id = 12)
        """))
        forecast = compute_forecast(db, 5)
        trans.rollback()
    assert forecast["grid_source"] == "mixed"
    assert any("official slot for 11/12" in n for n in forecast["inputs"]["data_notes"])


def test_forecast_endpoint_exposes_data_notes(client, forecast_db):
    with forecast_db.connect() as db:
        stored = db.execute(text("SELECT inputs -> 'data_notes' FROM race_forecasts WHERE race_id = 5")).scalar()
    assert client.get("/races/5/forecast").json()["data_notes"] == stored


def test_slot_rates_shrink_thin_era_toward_long_run():
    all_history = [(3, 1)] * 10 + [(3, 5)] * 90          # long run: 10% wins from P3
    thin_era = [(3, 4)] * 12                               # new era: 12 starts, no win
    rates = shrunk_slot_rates(thin_era, all_history, field_size=20)
    assert 0.03 < rates[3][0] < 0.06                       # not ~0, not the full 10%
    rich_era = [(3, 4)] * 400
    rich = shrunk_slot_rates(rich_era, all_history, field_size=20)[3][0]
    assert rich < 0.5 * rates[3][0]                        # era record dominates


def test_what_if_baseline_is_consistent_with_grid_order(client):
    body = client.post("/races/5/what-if", json={"simulations": 400}).json()
    by_grid = sorted(body["results"], key=lambda r: r["grid"])
    assert by_grid[0]["baseline"]["expected_position"] < by_grid[-1]["baseline"]["expected_position"]
    assert by_grid[0]["baseline"]["expected_position"] < 4        # not scrambled to mid-field
    assert by_grid[0]["driver_name"].startswith("Driver") and "team_name" in by_grid[0]
    assert body["simulation_settings"]["race_day_spread_scale"] == 0.25


def test_what_if_reports_median_and_most_likely_position(client):
    body = client.post("/races/5/what-if", json={"simulations": 200}).json()
    base = body["results"][0]["baseline"]
    assert {"median_position", "most_likely_position"} <= set(base)
    assert 1 <= base["median_position"] <= 12 and 1 <= base["most_likely_position"] <= 12


def test_forecast_endpoint_reports_strategy_method(client):
    assert client.get("/races/7/forecast").json()["strategy_extras"]["method"] == "era_mode"


def test_hierarchy_switch_gives_each_driver_a_strategy(forecast_db, monkeypatch):
    import race_forecast_v1
    monkeypatch.setattr(race_forecast_v1, "STRATEGY_METHOD", "hierarchy")
    with forecast_db.connect() as db:
        forecast = compute_forecast(db, 7)
    assert forecast["inputs"]["strategy_extras"]["method"] == "hierarchy"
    assert all(d["strategy"]["sequence"] == ["MEDIUM", "HARD"] for d in forecast["drivers"])
    assert all(d["strategy"]["source"].startswith("track") for d in forecast["drivers"])


def test_rain_forecast_adds_weather_and_a_warning_not_a_wet_strategy(forecast_db, monkeypatch):
    from datetime import date

    import race_forecast_v1
    monkeypatch.setattr(race_forecast_v1, "_today", lambda: date(2024, 10, 18))
    monkeypatch.setattr(race_forecast_v1, "_race_conditions", lambda t, d: {
        "status": "ok", "tier": "forecast", "rain_expected": True, "rain_onset_lap": 12,
        "wet_strategy_trigger": True, "target_track_temp": 24.0})
    with forecast_db.connect() as db:
        forecast = compute_forecast(db, 7)
    extras = forecast["inputs"]["strategy_extras"]
    assert extras["weather"]["wet_strategy_trigger"] is True
    assert "wet" not in extras                        # wet precedent failed its backtest
    assert any("low confidence" in n for n in forecast["inputs"]["data_notes"])

    monkeypatch.setattr(race_forecast_v1, "_race_conditions", lambda t, d: {"status": "error", "message": "offline"})
    with forecast_db.connect() as db:
        forecast = compute_forecast(db, 7)
    assert "weather" not in forecast["inputs"]["strategy_extras"]
    assert any("weather: unavailable (offline)" in n for n in forecast["inputs"]["data_notes"])


def test_optimiser_switch_without_tyre_data_is_a_note(forecast_db, monkeypatch):
    import race_forecast_v1
    monkeypatch.setattr(race_forecast_v1, "OPTIMISER_ENABLED", True)
    with forecast_db.connect() as db:
        forecast = compute_forecast(db, 7)
    assert "model_optimal" not in forecast["inputs"]["strategy_extras"]     # synthetic laps carry no tyre life
    assert any("model-optimal strategy" in n for n in forecast["inputs"]["data_notes"])


def test_slot_rates_never_increase_down_the_grid():
    from race_forecast_v1 import non_increasing, shrunk_slot_rates
    assert non_increasing([0.5, 0.3, 0.4, 0.1], [1, 1, 1, 1]) == pytest.approx([0.5, 0.35, 0.35, 0.1])
    assert non_increasing([0.2, 0.6], [3, 1]) == pytest.approx([0.3, 0.3])
    # era luck: P4 reached the podium every time, P3 never
    era = [(3, 10)] * 12 + [(4, 2)] * 12 + [(1, 1)] * 12 + [(2, 2)] * 12
    all_eras = [(g, g) for g in range(1, 21)] * 20
    rates = shrunk_slot_rates(era, all_eras, field_size=20)
    pods = [rates[s][1] for s in sorted(rates)]
    wins = [rates[s][0] for s in sorted(rates)]
    assert all(a >= b for a, b in zip(pods, pods[1:])) and all(a >= b for a, b in zip(wins, wins[1:]))


def test_race_list_shows_winner_and_predicted_winner(client):
    races = {r["id"]: r for r in client.get("/races?season=2024").json()}
    assert races[5]["winner_name"].startswith("Driver") and races[5]["has_forecast"] is True
    assert races[5]["predicted_winner_name"].startswith("Driver")
    assert races[7]["winner_name"] is None and races[7]["has_forecast"] is True     # upcoming, forecast only
    assert races[8]["has_forecast"] is False and races[8]["predicted_winner_name"] is None


def test_practice_correction_is_reported_even_without_practice_data(forecast_db):
    with forecast_db.connect() as db:
        forecast = compute_forecast(db, 7)
    notes = forecast["inputs"]["data_notes"]
    assert any(n.startswith("practice: no usable long runs") for n in notes)   # synthetic: no FP sessions


def test_win_chances_fall_back_to_grid_slots_with_little_history(forecast_db):
    with forecast_db.connect() as db:
        forecast = compute_forecast(db, 7)
    assert forecast["inputs"]["win_method"] == "grid_slot"
    assert any("too few earlier races for the strength model" in n for n in forecast["inputs"]["data_notes"])


def test_win_chances_from_the_strength_model(forecast_db, monkeypatch):
    import championship_model_v1

    monkeypatch.setattr(championship_model_v1, "MIN_HISTORY", 3)
    with forecast_db.connect() as db:
        forecast = compute_forecast(db, 7)
    assert forecast["inputs"]["win_method"] == "strength"
    assert any(n.startswith("win/podium chances: strength model") for n in forecast["inputs"]["data_notes"])
    p_win = {d["driver_id"]: d["p_win"] for d in forecast["drivers"]}
    assert sum(p_win.values()) == pytest.approx(1.0, abs=0.01)
    assert all(d["p_podium"] >= d["p_win"] for d in forecast["drivers"])
    assert forecast["predicted_winner"] == max(p_win, key=p_win.get)


def test_forecast_endpoint_names_the_predicted_winner(client):
    body = client.get("/races/7/forecast").json()
    best = max(body["drivers"], key=lambda d: d["p_win"])
    assert body["predicted_winner"]["driver_id"] == best["driver_id"]
    assert body["predicted_winner"]["p_win"] == best["p_win"]


def test_production_check_report_runs(forecast_db, capsys):
    from production_check_v1 import race_winners, team_id_changes, teams_short_of_cars, title_history

    with forecast_db.connect() as db:
        race_winners(db, 2024)
        title_history(db, 2024)
        team_id_changes(db, [2024])
        teams_short_of_cars(db, [2024])
    out = capsys.readouterr().out
    assert "favourite" in out and "not raced yet" in out and "favourite won" in out
    assert "nothing stored" in out or "round" in out


def test_winner_prediction_before_and_after_qualifying(forecast_db, monkeypatch):
    import championship_model_v1
    import winner_prediction_v1 as wp

    monkeypatch.setattr(championship_model_v1, "MIN_HISTORY", 3)
    with forecast_db.connect() as db:
        after = wp.predict(db, 7)                  # qualifying in, race not run
        before = wp.predict(db, 8)                 # no qualifying yet
    assert after["stage"] == "post_qualifying" and set(after["stages"]) == set(wp.STAGES)
    assert after["grid_source"] == "qualifying"
    assert before["stage"] == "pre_qualifying" and set(before["stages"]) == {"pre_qualifying"}
    assert before["lineup_source"].startswith("line-up of round")   # no entry list loaded for race 8
    for pred in (after, before):
        block = pred["stages"][pred["stage"]]
        assert sum(d["p_win"] for d in block["drivers"]) == pytest.approx(1.0, abs=0.01)
        assert pred["predicted_winner"] == block["favourite"] == {k: block["drivers"][0][k] for k in block["favourite"]}
        assert pred["confidence"]["level"] in ("high", "medium", "low")
    # the grid only exists after qualifying
    assert all(d["grid"] is None for d in after["stages"]["pre_qualifying"]["drivers"])
    assert all(d["grid"] for d in after["stages"]["post_qualifying"]["drivers"])


def test_winner_confidence_uses_measured_bins():
    import winner_prediction_v1 as wp

    table = {"pre_qualifying": {"races": 100, "favourite_won": 0.45,
                                "bins": [{"lo": 0.25, "hi": 0.35, "races": 40, "mean_p": 0.3, "favourite_won": 0.30},
                                         {"lo": 0.45, "hi": 0.6, "races": 5, "mean_p": 0.5, "favourite_won": 1.0}]},
             "post_qualifying": {"races": 100, "favourite_won": 0.6, "bins": []}}
    assert wp.confidence("pre_qualifying", 0.3, table)["level"] == "low"          # bin: 30%
    small = wp.confidence("pre_qualifying", 0.5, table)                            # bin too small -> stage record
    assert small["favourite_won_historically"] == 0.45 and small["level"] == "medium"
    assert wp.confidence("post_qualifying", 0.4, table)["level"] == "high"
    assert wp.confidence("post_qualifying", 0.4, wp.FALLBACK)["stage_record"] == 0.572


def test_winner_backtest_measures_both_stages():
    import winner_prediction_v1 as wp
    from test_championship_model_v1 import _weekend

    # the pole sitter always wins and is a different driver each race: only the grid tells
    weekends = []
    for i in range(1, 25):
        order = [((i + k) % 6) + 1 for k in range(6)]
        weekends.append(_weekend(i, order, season=2023 + (i > 12), grid={d: p for p, d in enumerate(order, start=1)}))
    out = wp.backtest(weekends, 2024, 2024, sims=500)
    assert out["post_qualifying"]["favourite_won"] > out["pre_qualifying"]["favourite_won"]
    assert out["post_qualifying"]["races"] == out["pre_qualifying"]["races"] == 12


def test_winner_prediction_endpoints(client, monkeypatch):
    import championship_model_v1

    monkeypatch.setattr(championship_model_v1, "MIN_HISTORY", 3)
    body = client.get("/races/7/winner-prediction").json()
    assert body["stage"] == "post_qualifying" and body["predicted_winner"]["driver_name"].startswith("Driver")
    assert client.get("/races/8/winner-prediction").json()["stage"] == "pre_qualifying"
    assert client.get("/races/999/winner-prediction").status_code == 404


def test_system_check_runs_on_a_small_database(forecast_db, client, capsys, monkeypatch):
    import championship_model_v1
    import system_check_v1 as sc

    monkeypatch.setattr(championship_model_v1, "MIN_HISTORY", 3)
    with forecast_db.connect() as db:
        sc.coverage(db, 2025)
        sc.quality(db, 2025)
        sc.api(db)
    out = capsys.readouterr().out
    print(out)                     # shown with -s: the endpoint table is the useful part
    assert "DATA COVERAGE" in out and "API" in out
    api_fails = [p for p in sc.problems if p.startswith("API")]
    assert api_fails == [], api_fails


def test_sharpening_keeps_the_order_and_is_fitted_on_winners():
    import winner_prediction_v1 as wp

    p = {1: 0.3, 2: 0.25, 3: 0.2, 4: 0.15, 5: 0.1}
    q = wp.sharpen(p, 2.0)
    assert sum(q.values()) == pytest.approx(1.0) and q[1] > p[1] and q[5] < p[5]
    assert sorted(q, key=q.get) == sorted(p, key=p.get) and wp.sharpen(p, 1.0) == p
    # the favourite always wins -> k well above 1; winners spread like p -> k near 1
    assert wp.fit_power([(p, 1)] * 30) > 3
    spread = [(p, d) for d, n in ((1, 6), (2, 5), (3, 4), (4, 3), (5, 2)) for _ in range(n)]
    assert wp.fit_power(spread) == pytest.approx(1.0, abs=0.35)
    table = {"post_qualifying": {"calibration": {"power": 1.8, "promoted": False}}}
    assert wp.win_power("post_qualifying", table) == 1.0
    table["post_qualifying"]["calibration"]["promoted"] = True
    assert wp.win_power("post_qualifying", table) == 1.8


def test_confidence_grows_with_data():
    import winner_prediction_v1 as wp
    from test_championship_model_v1 import _weekend

    weekends = [_weekend(i, [1, 2, 3, 4, 5, 6], season=2023 + (i > 12)) for i in range(1, 25)]
    first = wp.data_stage(weekends, weekends[12].race_date, 2024, "e")
    late = wp.data_stage(weekends, weekends[23].race_date, 2024, "e")
    assert first["bucket"] == "season_start" and first["races_this_season"] == 0
    assert late["bucket"] == "established" and late["races_this_season"] == 11
    assert wp.data_stage(weekends, weekends[12].race_date, 2024, "brand_new")["new_regulations"]
    table = {"pre_qualifying": {"races": 100, "favourite_won": 0.6, "bins": [],
                                "by_data_stage": {"season_start": {"races": 30, "favourite_won": 0.3},
                                                  "established": {"races": 60, "favourite_won": 0.7}}}}
    assert wp.confidence("pre_qualifying", 0.4, table, first)["level"] == "low"      # little data
    assert wp.confidence("pre_qualifying", 0.4, table, late)["level"] == "high"
    out = wp.backtest(weekends, 2024, 2024, sims=300)
    assert set(out["pre_qualifying"]["by_data_stage"]) >= {"season_start", "established"}
