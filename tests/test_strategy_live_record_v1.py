import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from strategy_live_record_v1 import MODEL_VERSION, freeze, freeze_stage, print_scorecard, score
from test_race_strategy_backtest_v2 import synthetic_db  # noqa: F401  (fixture)


def test_stage_follows_the_practice_wear():
    assert freeze_stage(None) == "pre_practice" and freeze_stage(0.0008) == "post_practice"


def _new_race(db, race_id=99):
    db.execute(text("""INSERT INTO races (id, track_id, season_year, round_number, race_date, regulation_era)
                       VALUES (:r, 1, 2025, 1, '2025-04-01', 'e')"""), {"r": race_id})


def _add_fp2(db, race_id):
    fp2 = db.execute(text("INSERT INTO sessions (race_id, session_type, start_time) VALUES (:r, 'FP2', now()) "
                          "RETURNING id"), {"r": race_id}).scalar()
    db.execute(text("INSERT INTO session_weather (session_id, rainfall) VALUES (:s, false)"), {"s": fp2})
    for driver in range(1, 7):
        entry = db.execute(text("""INSERT INTO race_entries (race_id, driver_id, team_id, role, car_number)
                                   VALUES (:r, :d, 1, 'race_driver', :d) RETURNING id"""),
                           {"r": race_id, "d": driver}).scalar()
        for n in range(1, 11):
            db.execute(text("""INSERT INTO laps (session_id, race_entry_id, lap_number, lap_time, tire_compound)
                               VALUES (:s, :e, :n, :t, 'MEDIUM')"""),
                       {"s": fp2, "e": entry, "n": n, "t": 92.0 + 0.05 * n + 0.01 * driver})


def test_freeze_pre_then_post_practice_and_never_after_the_race(synthetic_db):
    with synthetic_db.connect() as db:
        tx = db.begin()
        try:
            _new_race(db)
            assert freeze(db, 99) == ["pre_practice"]
            assert freeze(db, 99) == ["pre_practice"]                     # rewritten while open
            _add_fp2(db, 99)
            assert freeze(db, 99) == ["post_practice"]
            stored = dict(db.execute(text("""SELECT stage, prediction FROM winning_strategy_live
                                             WHERE race_id = 99 AND model_version = :v"""),
                                     {"v": MODEL_VERSION}).all())
            assert set(stored) == {"pre_practice", "post_practice"}
            assert "practice tyre wear" in stored["post_practice"]["source"]
            assert stored["post_practice"]["pit_windows"] and stored["post_practice"]["confidence"]
            db.execute(text("DELETE FROM laps WHERE session_id IN (SELECT id FROM sessions WHERE race_id = 99)"))
            assert freeze(db, 99) == []                                    # never back to pre_practice
            assert freeze(db, 6) == []                                     # race 6 has results
        finally:
            tx.rollback()


def test_score_reviews_a_frozen_plan_against_the_front_grid(synthetic_db, capsys):
    from race_winning_strategy_v1 import AlternativePlan, PitWindow, WinningStrategy, strategy_to_json

    plan = WinningStrategy(("MEDIUM", "HARD"), (PitWindow(1, 12, 16, 14),), {1: 1.0}, 10.0, 2, "track", "test",
                           AlternativePlan(("MEDIUM", "HARD", "HARD"), (PitWindow(1, 8, 10, 9), PitWindow(2, 20, 22, 21)), 0.0),
                           "high")
    with synthetic_db.connect() as db:
        tx = db.begin()
        try:
            from strategy_live_record_v1 import ensure_table
            ensure_table(db)
            db.execute(text("""INSERT INTO winning_strategy_live (race_id, stage, model_version, prediction, confidence_level)
                               VALUES (6, 'post_practice', :v, CAST(:p AS JSONB), 'high')"""),
                       {"v": MODEL_VERSION, "p": json.dumps(strategy_to_json(plan))})
            record = score(db)
        finally:
            tx.rollback()
    (call,) = record["calls"]
    assert call["race_id"] == 6 and call["predicted"] == ["MEDIUM", "HARD"]
    assert call["scored"]
    assert call["review"]["front_grid_cars"] == 5 and call["review"]["front_grid_plan_matches"] == 5
    assert call["winner_ran_plan"] is True
    # synthetic race 6 has no SC/VSC windows stored, so no car counts as green-flag
    assert call["green_front_cars"] == 0 and call["stops_with_window"] == 0
    print_scorecard(record)
    assert "LIVE RECORD: predicted winning strategies" in capsys.readouterr().out


@pytest.fixture()
def strategy_client(synthetic_db):
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

    previous = app.dependency_overrides.get(get_db)        # the shared api_client's, if set
    app.dependency_overrides[get_db] = override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        if previous is not None:
            app.dependency_overrides[get_db] = previous


def test_winning_strategy_and_live_strategy_endpoints(strategy_client):
    body = strategy_client.get("/strategy/winning", params={"race_id": 6}).json()
    assert body["race_id"] == 6 and body["prediction"]["sequence"] and body["prediction"]["pit_windows"]
    assert body["prediction"]["note"].startswith("Green-flag plan") and body["review"] is not None
    assert isinstance(body["track_record"], list)
    assert strategy_client.get("/strategy/winning", params={"race_id": 12345}).status_code == 404
    live = strategy_client.get("/records/live-strategy").json()
    assert live["model_version"] == MODEL_VERSION


def _front_grid_with_start_tyres(db, race_id, tyres):
    """Provisional grid P1.. for the race's entries (car order) and their starting tyres."""
    from start_tyres_v1 import store

    entries = [e for (e,) in db.execute(text("SELECT id FROM race_entries WHERE race_id = :r ORDER BY car_number"),
                                        {"r": race_id})]
    for slot, entry in enumerate(entries, start=1):
        db.execute(text("INSERT INTO race_provisional_grid (race_id, race_entry_id, grid_position) VALUES (:r, :e, :p)"),
                   {"r": race_id, "e": entry, "p": slot})
    store(db, race_id, {e: t for e, t in zip(entries, tyres)}, "manual")
    return entries


def test_pre_start_stage_is_frozen_once_starting_tyres_are_stored(synthetic_db):
    from race_winning_strategy_v1 import format_pre_start, predict_pre_start_for_race

    with synthetic_db.connect() as db:
        tx = db.begin()
        try:
            _new_race(db)
            _add_fp2(db, 99)
            assert freeze(db, 99) == ["post_practice"]                    # no starting tyres yet
            _front_grid_with_start_tyres(db, 99, ["MEDIUM", "MEDIUM", "HARD"])
            result = predict_pre_start_for_race(db, 99)
            assert [c["start"] for c in result["cars"]] == ["MEDIUM", "MEDIUM", "HARD"]
            assert result["plans"]["MEDIUM"].sequence[0] == "MEDIUM"
            assert "starts on HARD" in format_pre_start(result)
            assert freeze(db, 99) == ["post_practice", "pre_start"]
            stored = db.execute(text("""SELECT prediction, frozen_at_utc FROM winning_strategy_live
                                        WHERE race_id = 99 AND stage = 'pre_start'""")).one()
            assert set(stored[0]["plans"]) == {"MEDIUM", "HARD"} and len(stored[0]["cars"]) == 3
            assert stored[1] is not None
        finally:
            tx.rollback()


def test_pre_start_scoring_uses_each_cars_starting_tyre_and_skips_late_freezes(synthetic_db):
    from race_winning_strategy_v1 import PitWindow, WinningStrategy, strategy_to_json
    from strategy_live_record_v1 import ensure_table

    plan = WinningStrategy(("MEDIUM", "HARD"), (PitWindow(1, 12, 16, 14),), {1: 1.0}, 10.0, 2, "track", "test")
    body = json.dumps({"cars": [], "plans": {"MEDIUM": strategy_to_json(plan)}})
    with synthetic_db.connect() as db:
        tx = db.begin()
        try:
            ensure_table(db)
            for race_id, frozen in ((6, "2000-01-01"), (5, "2100-01-01")):
                db.execute(text("""INSERT INTO winning_strategy_live
                                   (race_id, stage, model_version, prediction, confidence_level, frozen_at_utc)
                                   VALUES (:r, 'pre_start', :v, CAST(:p AS JSONB), 'medium', :f)"""),
                           {"r": race_id, "v": MODEL_VERSION, "p": body, "f": frozen})
            record = score(db)
        finally:
            tx.rollback()
    by_race = {c["race_id"]: c for c in record["calls"]}
    assert by_race[6]["scored"] and by_race[6]["predicted"] == ["MEDIUM start: MEDIUM -> HARD"]
    assert by_race[6]["winner_ran_plan"] is True
    assert not by_race[5]["scored"] and "after the race start" in by_race[5]["why_not_scored"]
    assert record["summary"]["pre_start"]["weekends"] == 1


def test_pipeline_fetches_starting_tyres_only_when_due(synthetic_db, monkeypatch):
    import start_tyres_v1
    from update_latest_races import fetch_start_tyres_if_due

    calls = []

    def fake_fetch(race_date, entries):
        calls.append(race_date)
        return {e: "MEDIUM" for e in entries.values()}, []
    monkeypatch.setattr(start_tyres_v1, "fetch_start_tyres", fake_fetch)
    with synthetic_db.connect() as db:
        tx = db.begin()
        try:
            fetch_start_tyres_if_due(db, 6)                               # already raced
            _new_race(db)
            _add_fp2(db, 99)
            fetch_start_tyres_if_due(db, 99)                              # no qualifying yet
            assert calls == []
            q = db.execute(text("INSERT INTO sessions (race_id, session_type, start_time) VALUES (99, 'Q', now()) RETURNING id")).scalar()
            entry = db.execute(text("SELECT id FROM race_entries WHERE race_id = 99 LIMIT 1")).scalar()
            db.execute(text("INSERT INTO qualifying_results (session_id, race_entry_id, final_position) VALUES (:s, :e, 1)"),
                       {"s": q, "e": entry})
            fetch_start_tyres_if_due(db, 99)
            assert len(calls) == 1
            from start_tyres_v1 import load_start_tyres
            assert len(load_start_tyres(db, 99)) == 6
        finally:
            tx.rollback()


def test_pre_start_endpoint_404_without_starting_tyres(strategy_client):
    assert strategy_client.get("/strategy/winning/pre-start", params={"race_id": 6}).status_code == 404
