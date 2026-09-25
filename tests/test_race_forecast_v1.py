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
