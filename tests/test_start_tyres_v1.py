from sqlalchemy import text

from start_tyres_v1 import compare, lap1_tyres, load_start_tyres, normalise, parse_manual, parse_start_tyres, store
from test_race_strategy_backtest_v2 import synthetic_db  # noqa: F401  (fixture)


def test_first_stint_per_car_from_openf1_rows():
    rows = [{"driver_number": 1, "stint_number": 2, "compound": "HARD"},
            {"driver_number": 1, "stint_number": 1, "compound": "medium"},
            {"driver_number": 4, "stint_number": 1, "compound": "UNKNOWN"},
            {"driver_number": 16, "stint_number": 1, "compound": "SOFT"},
            {"driver_number": 99, "stint_number": 1, "compound": "SOFT"},
            {"driver_number": None, "stint_number": 1, "compound": "SOFT"}]
    assert parse_start_tyres(rows, {1: 10, 4: 40, 16: 160}) == {10: "MEDIUM", 160: "SOFT"}
    assert normalise(" wet ") == "WET" and normalise("TEST_UNKNOWN") is None


def test_manual_entry_and_compare():
    tyres, problems = parse_manual("1=medium, 4=HARD, 7=SOFT, 16=PURPLE", {1: 10, 4: 40, 16: 160})
    assert tyres == {10: "MEDIUM", 40: "HARD"} and len(problems) == 2
    assert compare({10: "MEDIUM", 40: "SOFT"}, {10: "MEDIUM", 40: "HARD", 50: "HARD"}) == (2, 1, 1)


def test_store_and_load_and_lap1_truth(synthetic_db):
    with synthetic_db.connect() as db:
        tx = db.begin()
        try:
            entry = db.execute(text("SELECT id FROM race_entries WHERE race_id = 6 LIMIT 1")).scalar()
            store(db, 6, {entry: "MEDIUM"}, "manual")
            store(db, 6, {entry: "HARD"}, "openf1")
            assert load_start_tyres(db, 6) == {entry: "HARD"}
            assert lap1_tyres(db, 6)                 # every synthetic car has a lap-1 compound
        finally:
            tx.rollback()
