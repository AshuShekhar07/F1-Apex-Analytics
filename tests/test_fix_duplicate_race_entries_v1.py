"""fix_duplicate_race_entries_v1 against the fixture database.

Each test runs in a transaction that is rolled back, so the shared fixture
database is left untouched.
"""

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from fix_duplicate_race_entries_v1 import apply_repairs, plan_repairs


@pytest.fixture
def conn(fixture_db_url):
    engine = create_engine(fixture_db_url)
    with engine.connect() as connection:
        trans = connection.begin()
        # the unique constraint normally forbids duplicates; lift it to reproduce them
        connection.execute(text("ALTER TABLE race_entries DROP CONSTRAINT race_entries_race_driver_key"))
        yield connection
        trans.rollback()
    engine.dispose()


def _add_entry(conn, race_id, driver_id, team_id=1):
    return conn.execute(text("""
        INSERT INTO race_entries (race_id, driver_id, team_id, role, car_number)
        VALUES (:r, :d, :t, 'race_driver', :d) RETURNING id
    """), {"r": race_id, "d": driver_id, "t": team_id}).scalar()


def _entry_id(conn, race_id, driver_id):
    return conn.execute(text("SELECT MIN(id) FROM race_entries WHERE race_id = :r AND driver_id = :d"),
                        {"r": race_id, "d": driver_id}).scalar()


def test_clean_database_has_nothing_to_repair(conn):
    assert plan_repairs(conn) == ([], [])


def test_orphan_duplicate_is_deleted_and_real_entry_kept(conn):
    real = _entry_id(conn, 1, 1)
    orphan = _add_entry(conn, 1, 1)

    deletable, manual = plan_repairs(conn)
    assert manual == []
    assert [(d["entry_id"], d["keep"]) for d in deletable] == [(orphan, real)]
    assert "laps.race_entry_id" in deletable[0]["references"][real]

    assert apply_repairs(conn, deletable) == 1
    assert conn.execute(text("SELECT COUNT(*) FROM race_entries WHERE race_id = 1 AND driver_id = 1")).scalar() == 1
    assert _entry_id(conn, 1, 1) == real


def test_duplicates_that_both_hold_data_go_to_manual_review(conn):
    second = _add_entry(conn, 1, 2)
    session = conn.execute(text("SELECT id FROM sessions WHERE race_id = 1 AND session_type = 'FP1'")).scalar()
    conn.execute(text("INSERT INTO laps (session_id, race_entry_id, lap_number, lap_time) VALUES (:s, :e, 3, 91.0)"),
                 {"s": session, "e": second})

    deletable, manual = plan_repairs(conn)
    assert deletable == []
    assert [(m["race_id"], m["driver_id"]) for m in manual] == [(1, 2)]


def test_unique_constraint_blocks_new_duplicates(fixture_db_url):
    engine = create_engine(fixture_db_url)
    with engine.connect() as connection:
        with pytest.raises(IntegrityError):
            _add_entry(connection, 1, 1)
        connection.rollback()
    engine.dispose()
