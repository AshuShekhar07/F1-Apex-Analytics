from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import strategy_service_v7
from test_race_strategy_backtest_v2 import synthetic_db  # noqa: F401  (fixture)


@pytest.fixture(autouse=True)
def fresh_cache():
    strategy_service_v7.clear_cache()
    yield
    strategy_service_v7.clear_cache()


def test_prediction_uses_track_real_stints_and_median_stops(synthetic_db):
    with synthetic_db.connect() as db:
        r = strategy_service_v7.predict(db, 1, date(2025, 4, 1), today=date(2020, 1, 1))
    assert r["stage"] == "pre_qualifying" and r["regulation_era"] == "e" and r["total_laps"] == 30
    assert r["source"].startswith("track(n=36)")                       # 3 earlier races x 12 cars
    assert r["primary_strategy"]["sequence"] == ["MEDIUM", "HARD"]
    # synthetic stop laps are 12 + d % 6 -> median 14.5 -> round(14.5/30*30)
    assert r["primary_strategy"]["stop_laps"] == [14]
    assert r["weather"] is None and r["wet_strategy"] is None


def test_walk_forward_and_stages(synthetic_db):
    with synthetic_db.connect() as db:
        early = strategy_service_v7.predict(db, 1, date(2023, 4, 1), today=date(2020, 1, 1))
        assert early["source"].startswith("track(n=12)") is False       # only 1 earlier track race: widened
        assert any("widened" in n for n in early["data_notes"])
        pre_start = strategy_service_v7.predict(db, 1, date(2025, 4, 1), grid=3, start_compound="medium",
                                                today=date(2020, 1, 1))
        assert pre_start["stage"] == "pre_start" and pre_start["start_compound"] == "MEDIUM"
        wrong_tyre = strategy_service_v7.predict(db, 1, date(2025, 4, 1), start_compound="SOFT",
                                                 today=date(2020, 1, 1))
        assert any("started on SOFT" in n for n in wrong_tyre["data_notes"])
        with pytest.raises(strategy_service_v7.StrategyNotFound):
            strategy_service_v7.predict(db, 99, date(2025, 4, 1))


def test_weather_outage_is_a_note_and_rain_without_wet_history_invents_nothing(synthetic_db, monkeypatch):
    def outage(*_):
        raise RuntimeError("offline")
    monkeypatch.setattr(strategy_service_v7, "_weather", outage)
    with synthetic_db.connect() as db:
        r = strategy_service_v7.predict(db, 1, date(2025, 4, 1), today=date(2025, 3, 30))
    assert r["primary_strategy"]["sequence"] == ["MEDIUM", "HARD"]
    assert any("weather unavailable (offline)" in n for n in r["data_notes"])

    monkeypatch.setattr(strategy_service_v7, "_weather", lambda *_: {
        "status": "ok", "tier": "forecast", "days_out": 2, "rain_expected": True, "rain_onset_lap": 5,
        "wet_strategy_trigger": True, "target_track_temp": 20.0})
    with synthetic_db.connect() as db:
        r = strategy_service_v7.predict(db, 1, date(2025, 4, 1), today=date(2025, 3, 30))
    assert r["weather"]["wet_strategy_trigger"] is True and r["wet_strategy"] is None
    assert any("no wet races" in n for n in r["data_notes"])


def test_endpoint(synthetic_db, monkeypatch):
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

    app.dependency_overrides[get_db] = override
    try:
        client = TestClient(app)
        body = client.get("/strategy/predict?track_id=1&race_date=2025-04-01&grid=2").json()
        assert body["model_version"] == "strategy_v7" and body["stage"] == "post_qualifying"
        assert body["primary_strategy"]["stints"][0] == {"compound": "MEDIUM", "start_lap": 1, "end_lap": 14}
        assert client.get("/strategy/predict?track_id=99&race_date=2025-04-01").status_code == 404
        assert client.get("/strategy/predict?track_id=1&race_date=2025-04-01&start_compound=WET").status_code == 422
    finally:
        app.dependency_overrides.pop(get_db, None)
