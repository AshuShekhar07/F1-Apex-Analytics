from sqlalchemy import text

from data_quality_v1 import check_season, evaluate
from test_race_strategy_backtest_v2 import synthetic_db  # noqa: F401  (fixture)
from update_latest_races import fill_regulation_era, loaders


def _facts(**over):
    base = {"race_id": 1, "round_number": 1, "track": "T", "regulation_era": "e", "race_distance_known": True,
            "quali_entries": 20, "quali_with_time": 20, "duplicate_car_numbers": [], "identity_mismatches": [],
            "results": 20, "results_without_status": 0, "grid_known": 20, "drivers_with_race_laps": 20,
            "lap_metadata_share": 1.0, "race_weather": True, "safety_car_periods": 0, "winner_time": 5400.0,
            "practice_laps": True}
    return {**base, **over}


def test_clean_raced_weekend_passes():
    c = evaluate(_facts())
    assert c.stage == "raced" and c.ok and not c.warns


def test_stages_and_blocking_failures():
    assert evaluate(_facts(results=0, quali_entries=0)).stage == "scheduled"
    upcoming = evaluate(_facts(results=0))
    assert upcoming.stage == "qualified" and upcoming.ok
    bad = evaluate(_facts(identity_mismatches=["#3 DB: Daniel Ricciardo FastF1: Max Verstappen"],
                          regulation_era=None, grid_known=10, duplicate_car_numbers=[3]))
    assert not bad.ok and len(bad.fails) == 4
    assert any("Ricciardo" in f for f in bad.fails)
    assert not evaluate(_facts(results=0, race_distance_known=False)).ok     # new track, no distance


def test_warnings_do_not_block():
    c = evaluate(_facts(race_weather=False, safety_car_periods=None, practice_laps=False, lap_metadata_share=0.1))
    assert c.ok and len(c.warns) == 4


def test_loader_order_starts_with_results_and_ends_with_derived_stints():
    steps = [cmd[0] for _, cmd in loaders(2026)]
    assert steps[0] == "backfill_history.py" and steps[-1] == "extract_race_stints.py"
    assert ["backfill_race_strategy_pit_stops_v1.py", "--start-year", "2026", "--end-year", "2026"] in \
        [cmd for _, cmd in loaders(2026)]


def test_era_fill_and_season_check_on_the_database(synthetic_db):
    with synthetic_db.connect() as db:
        tx = db.begin()
        try:
            db.execute(text("""INSERT INTO races (id, track_id, season_year, round_number, race_date)
                               VALUES (50, 1, 2024, 9, make_date(2024, 12, 1))"""))
            assert fill_regulation_era(db, 2024) == [50]
            assert db.execute(text("SELECT regulation_era FROM races WHERE id = 50")).scalar() == "e"
            checks = {c.race_id: c for c in check_season(db, 2024, identity=False)}
            assert checks[50].stage == "scheduled"
            # synthetic weekends have 12 cars: complete otherwise, but below a real F1 field
            assert checks[5].stage == "raced"
            assert checks[5].fails == ["only 12 qualifying entries", "only 12 race results"]
            assert "no practice laps (forecast uses qualifying pace only)" in checks[5].warns
        finally:
            tx.rollback()


def test_era_fill_leaves_ambiguous_seasons_alone(synthetic_db):
    with synthetic_db.connect() as db:
        tx = db.begin()
        try:
            db.execute(text("UPDATE races SET regulation_era = 'other' WHERE id = 5"))
            db.execute(text("""INSERT INTO races (id, track_id, season_year, round_number, race_date)
                               VALUES (51, 1, 2024, 10, make_date(2024, 12, 8))"""))
            assert fill_regulation_era(db, 2024) == []
        finally:
            tx.rollback()
