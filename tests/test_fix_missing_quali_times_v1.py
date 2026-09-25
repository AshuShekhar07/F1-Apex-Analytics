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
