from datetime import timedelta

from sqlalchemy import create_engine, text

from fix_missing_quali_times_v1 import MISSING_SQL, apply_updates, planned_updates


def test_planned_updates_match_car_numbers_and_skip_empty():
    results = [
        {"DriverNumber": "1", "Q1": timedelta(seconds=80.5), "Q2": timedelta(seconds=80.1), "Q3": float("nan")},
        {"DriverNumber": "44", "Q1": None, "Q2": None, "Q3": None},       # no times: nothing to write
        {"DriverNumber": "99", "Q1": timedelta(seconds=81.0)},              # not entered in this race
    ]
    updates, unmatched = planned_updates(results, {1: 10, 44: 11})
    assert updates == [{"entry": 10, "q1": 80.5, "q2": 80.1, "q3": None}]
    assert unmatched == [99]


def test_apply_only_fills_missing_times(fixture_db_url):
    engine = create_engine(fixture_db_url)
    with engine.connect() as db:
        trans = db.begin()
        session = db.execute(text("SELECT id FROM sessions WHERE race_id = 1 AND session_type = 'Q'")).scalar()
        entries = dict(db.execute(text("SELECT driver_id, id FROM race_entries WHERE race_id = 1")).all())
        db.execute(text("UPDATE qualifying_results SET q1_time = NULL, q2_time = NULL, q3_time = NULL WHERE session_id = :s"),
                   {"s": session})
        db.execute(text("UPDATE qualifying_results SET q3_time = 70.0 WHERE race_entry_id = :e"), {"e": entries[2]})
        assert [r[0] for r in db.execute(text(MISSING_SQL)).all()] == []    # driver 2 still has a time
        changed = apply_updates(db, session, [
            {"entry": entries[1], "q1": 90.5, "q2": 90.0, "q3": 89.5},
            {"entry": entries[2], "q1": 90.6, "q2": 90.1, "q3": 99.9},      # existing q3 must survive
        ])
        rows = dict(db.execute(text("""
            SELECT race_entry_id, ARRAY[q1_time, q2_time, q3_time] FROM qualifying_results WHERE session_id = :s
        """), {"s": session}).all())
        trans.rollback()
    engine.dispose()
    assert changed == 2
    assert [float(x) for x in rows[entries[1]]] == [90.5, 90.0, 89.5]
    assert [float(x) for x in rows[entries[2]]] == [90.6, 90.1, 70.0]


def test_results_from_lap_timing_uses_fastest_non_deleted_lap_per_segment():
    import pandas as pd

    from fix_missing_quali_times_v1 import results_from_lap_timing

    def laps(rows):
        return pd.DataFrame(rows, columns=["DriverNumber", "LapTime", "Deleted"])

    q1 = laps([("1", timedelta(seconds=81.0), False), ("1", timedelta(seconds=80.2), True),   # deleted
               ("1", timedelta(seconds=80.6), False), ("44", timedelta(seconds=80.9), None),
               ("44", pd.NaT, False)])
    q2 = laps([("1", timedelta(seconds=80.1), False)])
    rows = {r["DriverNumber"]: r for r in results_from_lap_timing([q1, q2, None])}
    assert rows["1"] == {"DriverNumber": "1", "Q1": timedelta(seconds=80.6), "Q2": timedelta(seconds=80.1)}
    assert rows["44"] == {"DriverNumber": "44", "Q1": timedelta(seconds=80.9)}
    updates, _ = planned_updates(list(rows.values()), {1: 10, 44: 11})
    assert {u["entry"]: (u["q1"], u["q2"], u["q3"]) for u in updates} == {10: (80.6, 80.1, None), 11: (80.9, None, None)}


def test_season_mode_repairs_or_skips_without_stopping_the_pipeline(fixture_db_url, monkeypatch, capsys):
    """Pipeline step: qualifying loaded before the official times were out (2026 R17) is
    found by season; when FastF1 cannot give times yet the race is skipped and exit is 0."""
    import sys

    import fastf1
    from sqlalchemy import create_engine

    import fix_missing_quali_times_v1 as fix

    engine = create_engine(fixture_db_url)
    with engine.begin() as db:
        saved = db.execute(text("""
            SELECT q.id, q.q1_time, q.q2_time, q.q3_time FROM qualifying_results q
            JOIN sessions s ON s.id = q.session_id WHERE s.race_id = 1""")).all()
        db.execute(text("""UPDATE qualifying_results SET q1_time = NULL, q2_time = NULL, q3_time = NULL
                           WHERE session_id = (SELECT id FROM sessions WHERE race_id = 1 AND session_type = 'Q')"""))
    try:
        monkeypatch.setenv("DATABASE_URL", fixture_db_url)
        monkeypatch.setattr(fastf1.Cache, "enable_cache", lambda *a, **k: None)

        def unavailable(*args, **kwargs):
            raise ConnectionError("not published yet")
        monkeypatch.setattr(fastf1, "get_session", unavailable)
        monkeypatch.setattr(sys, "argv", ["fix_missing_quali_times_v1.py", "--season", "2021", "--apply"])
        assert fix.main() == 0
        out = capsys.readouterr().out
        assert "race 1 Test Circuit One: not repaired (ConnectionError: not published yet)" in out
        monkeypatch.setattr(sys, "argv", ["fix_missing_quali_times_v1.py", "--season", "2022", "--apply"])
        assert fix.main() == 0
        assert "2022: every race with qualifying rows has times" in capsys.readouterr().out
    finally:
        with engine.begin() as db:
            for row in saved:
                db.execute(text("UPDATE qualifying_results SET q1_time = :a, q2_time = :b, q3_time = :c WHERE id = :i"),
                           {"i": row[0], "a": row[1], "b": row[2], "c": row[3]})
        engine.dispose()
