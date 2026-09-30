from datetime import date

import pytest
from sqlalchemy import text

from fetch_starting_grid_v1 import parse_grid, pick_grid_rows, race_session
from test_race_strategy_backtest_v2 import synthetic_db  # noqa: F401  (fixture)

SESSIONS = [
    {"session_key": 11, "session_name": "Sprint", "meeting_key": 1, "date_start": "2026-10-10T15:00:00+00:00"},
    {"session_key": 12, "session_name": "Qualifying", "meeting_key": 1, "date_start": "2026-10-10T19:00:00+00:00"},
    {"session_key": 13, "session_name": "Race", "meeting_key": 1, "date_start": "2026-10-11T19:00:00+00:00"},
    {"session_key": 23, "session_name": "Race", "meeting_key": 2, "date_start": "2026-10-25T19:00:00+00:00"},
]


def test_race_session_is_the_grand_prix_closest_to_the_date():
    assert race_session(SESSIONS, date(2026, 10, 11))["session_key"] == 13
    assert race_session(SESSIONS, date(2026, 10, 18)) is None          # nothing within 3 days


def test_grid_rows_prefer_the_race_then_qualifying_never_the_sprint():
    rows = [{"session_key": 11, "position": 1, "driver_number": 4}, {"session_key": 12, "position": 1, "driver_number": 1}]
    assert pick_grid_rows(rows, SESSIONS, 13) == [rows[1]]              # grid set by (GP) qualifying
    rows.append({"session_key": 13, "position": 1, "driver_number": 16})
    assert pick_grid_rows(rows, SESSIONS, 13) == [rows[2]]
    assert pick_grid_rows(rows[:1], SESSIONS, 13) == []                 # sprint grid only: not used


def test_parse_grid_validates_coverage_and_duplicates():
    entries = {n: 100 + n for n in range(1, 21)}
    rows = [{"driver_number": n, "position": p} for p, n in enumerate(range(1, 21), start=1)]
    grid, issues = parse_grid(rows, entries, 20)
    assert grid[101] == 1 and len(grid) == 20 and issues == []
    assert parse_grid(rows[:10], entries, 20)[0] == {}                  # half a grid: rejected
    dup = rows[:19] + [{"driver_number": 20, "position": 1}]
    assert parse_grid(dup, entries, 20) == ({}, ["position 1 listed twice or invalid"])
    grid, issues = parse_grid(rows + [{"driver_number": 99, "position": 21}], entries, 20)
    assert len(grid) == 20 and issues == ["car #99 not entered in this race"]


def test_forecast_uses_the_provisional_grid_before_the_results(synthetic_db):
    from race_forecast_v1 import compute_forecast

    with synthetic_db.connect() as db:
        tx = db.begin()
        try:
            # race 6 before the race: no results yet; qualifying P1 (driver 1) gets a 10-place penalty
            db.execute(text("""DELETE FROM race_results WHERE session_id IN
                               (SELECT id FROM sessions WHERE race_id = 6 AND session_type = 'R')"""))
            entries = dict(db.execute(text("""SELECT re.driver_id, re.id FROM race_entries re WHERE re.race_id = 6""")).all())
            quali = dict(db.execute(text("""SELECT re.driver_id, q.final_position FROM qualifying_results q
                                            JOIN race_entries re ON re.id = q.race_entry_id WHERE re.race_id = 6""")).all())
            order = sorted(quali, key=quali.get)
            penalised = order[0]
            grid = order[1:11] + [penalised] + order[11:]
            for pos, d in enumerate(grid, start=1):
                db.execute(text("INSERT INTO race_provisional_grid (race_id, race_entry_id, grid_position) VALUES (6, :e, :p)"),
                           {"e": entries[d], "p": pos})
            f = compute_forecast(db, 6)
            assert f["grid_source"] == "provisional_grid"
            assert {d["driver_id"]: d["grid"] for d in f["drivers"]}[penalised] == 11
            assert any("provisional starting grid" in n and "Driver" in n for n in f["inputs"]["data_notes"])
            db.execute(text("DELETE FROM race_provisional_grid WHERE race_id = 6"))
            f = compute_forecast(db, 6)
            assert f["grid_source"] == "qualifying"
            assert any("grid penalties are not in" in n for n in f["inputs"]["data_notes"])
        finally:
            tx.rollback()


def test_rate_limit_is_waited_out_and_sessions_are_fetched_once(monkeypatch):
    import fetch_starting_grid_v1 as fg

    calls = []

    class Resp:
        def __init__(self, code, data=None):
            self.status_code, self._data, self.headers = code, data, {"Retry-After": "0"}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(self.status_code)

        def json(self):
            return self._data

    replies = [Resp(429), Resp(200, [{"session_key": 1}])]
    import requests
    monkeypatch.setattr(requests, "get", lambda url, params, timeout: (calls.append(url), replies.pop(0))[1])
    monkeypatch.setattr(fg, "REQUEST_PAUSE", 0.0)
    monkeypatch.setattr(fg.time, "sleep", lambda s: None)
    fg._SESSIONS.clear()
    assert fg.season_sessions(2026) == [{"session_key": 1}]
    assert fg.season_sessions(2026) == [{"session_key": 1}]      # cached: no third call
    assert len(calls) == 2


def test_live_record_is_written_before_the_race_and_frozen_after(synthetic_db):
    from prediction_scoreboard_v1 import live_scores, load_meta, load_truth
    from race_forecast_v1 import compute_forecast, store_forecast

    with synthetic_db.connect() as db:
        tx = db.begin()
        try:
            results = db.execute(text("""SELECT rr.* FROM race_results rr JOIN sessions s ON s.id = rr.session_id
                                         WHERE s.race_id = 6 AND s.session_type = 'R'""")).mappings().all()
            db.execute(text("""DELETE FROM race_results WHERE session_id IN
                               (SELECT id FROM sessions WHERE race_id = 6 AND session_type = 'R')"""))
            store_forecast(db, compute_forecast(db, 6))                    # before the race: recorded
            before = db.execute(text("SELECT driver_id, predicted_position FROM race_forecast_live WHERE race_id = 6")).all()
            assert len(before) == 12
            for r in results:                                              # the race is run
                db.execute(text("""INSERT INTO race_results (session_id, race_entry_id, finishing_position,
                                   starting_grid_position, points, status) VALUES (:s, :e, :p, :g, 0, 'Finished')"""),
                           {"s": r["session_id"], "e": r["race_entry_id"], "p": r["finishing_position"],
                            "g": r["starting_grid_position"]})
            db.execute(text("UPDATE race_forecast_live SET computed_at = '2000-01-01' WHERE race_id = 6"))
            store_forecast(db, compute_forecast(db, 6))                    # recomputed after: live untouched
            assert db.execute(text("SELECT MIN(computed_at) FROM race_forecast_live WHERE race_id = 6")).scalar().year == 2000
            live = live_scores(db, load_meta(db), load_truth(db))
            assert {s.race_id for s in live} == {6} and len(live) == 12 and {s.model for s in live} == {"live"}
        finally:
            tx.rollback()
