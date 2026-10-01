import pandas as pd
import pytest
from sqlalchemy import text

from repair_missing_race_results_v1 import INSERT_SQL, missing_results, result_row


def _results():
    return pd.DataFrame({
        "DriverNumber": ["63", "12", "18"],
        "Position": [3.0, 6.0, None],
        "Points": [15.0, 8.0, 0.0],
        "Status": ["Finished", "Finished", "Did not start"],
        "GridPosition": [3.0, 4.0, 0.0],
        "Time": [pd.Timedelta(seconds=11.1), pd.Timedelta(seconds=53.7), pd.NaT],
    })


def test_result_row_matches_by_car_number():
    row = result_row(_results(), 12, session_id=7, entry_id=99)
    assert row == {"s": 7, "e": 99, "p": 6, "pts": 8.0, "st": "Finished", "g": 4, "gs": pytest.approx(53.7),
                   "gd": "+53.700s"}
    assert result_row(_results(), 44, 7, 99) is None          # not classified by FastF1 -> nothing invented
    assert result_row(_results(), None, 7, 99) is None
    dns = result_row(_results(), 18, 7, 99)
    assert dns["p"] is None and dns["gd"] == "Did not start"


def test_missing_results_finds_a_dropped_result_and_insert_is_idempotent(fixture_db_url):
    from sqlalchemy import create_engine

    engine = create_engine(fixture_db_url)
    with engine.connect() as db:
        tx = db.begin()
        try:
            victim = db.execute(text("""
                SELECT rr.id, rr.session_id, rr.race_entry_id, r.season_year
                FROM race_results rr JOIN sessions s ON s.id = rr.session_id AND s.session_type = 'R'
                JOIN races r ON r.id = s.race_id JOIN race_entries re ON re.id = rr.race_entry_id
                WHERE re.role = 'race_driver'
                  AND EXISTS (SELECT 1 FROM qualifying_results q WHERE q.race_entry_id = re.id)
                LIMIT 1""")).mappings().first()
            assert missing_results(db, victim["season_year"]) == []
            db.execute(text("DELETE FROM laps WHERE race_entry_id = :e AND session_id = :s"),
                       {"e": victim["race_entry_id"], "s": victim["session_id"]})
            db.execute(text("DELETE FROM race_results WHERE id = :i"), {"i": victim["id"]})
            gaps = missing_results(db, victim["season_year"])
            assert [(g["session_id"], g["entry_id"]) for g in gaps] == [(victim["session_id"], victim["race_entry_id"])]
            row = {"s": victim["session_id"], "e": victim["race_entry_id"], "p": 6, "pts": 8.0, "st": "Finished",
                   "g": 4, "gs": 1.0, "gd": "+1.000s"}
            db.execute(text(INSERT_SQL), row)
            db.execute(text(INSERT_SQL), row)                  # second run: no duplicate
            assert missing_results(db, victim["season_year"]) == []
        finally:
            tx.rollback()
