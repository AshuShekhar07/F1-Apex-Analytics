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
