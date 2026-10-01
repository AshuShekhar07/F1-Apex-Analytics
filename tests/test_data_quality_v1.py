from sqlalchemy import text

from data_quality_v1 import check_season, evaluate
from test_race_strategy_backtest_v2 import synthetic_db  # noqa: F401  (fixture)
from update_latest_races import fill_regulation_era, loaders


def _facts(**over):
    base = {"race_id": 1, "round_number": 1, "track": "T", "regulation_era": "e", "race_distance_known": True,
            "quali_entries": 20, "quali_with_time": 20, "duplicate_car_numbers": [], "identity_mismatches": [],
            "results": 20, "results_without_status": 0, "grid_known": 20, "drivers_with_race_laps": 20,
            "starters": 20, "race_date_passed": True, "sessions": 5,
            "lap_metadata_share": 1.0, "race_weather": True, "safety_car_periods": 0, "winner_time": 5400.0,
            "practice_laps": True}
    return {**base, **over}


def test_clean_raced_weekend_passes():
    c = evaluate(_facts())
    assert c.stage == "raced" and c.ok and not c.warns


def test_stages_and_blocking_failures():
    assert evaluate(_facts(results=0, quali_entries=0, race_date_passed=False)).stage == "scheduled"
    upcoming = evaluate(_facts(results=0, race_date_passed=False))
    assert upcoming.stage == "qualified" and upcoming.ok
    interrupted = evaluate(_facts(results=0, race_date_passed=True))
    assert interrupted.stage == "qualified" and not interrupted.ok
    bad = evaluate(_facts(identity_mismatches=["#3 DB: Daniel Ricciardo FastF1: Max Verstappen"],
                          regulation_era=None, grid_known=10, duplicate_car_numbers=[3]))
    assert not bad.ok and len(bad.fails) == 4
    assert any("Ricciardo" in f for f in bad.fails)
    assert not evaluate(_facts(results=0, race_distance_known=False, race_date_passed=False)).ok  # new track


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
            # its date (Dec 2024) has passed and nothing was ever loaded: flagged, not hidden
            assert checks[50].stage == "not loaded" and not checks[50].ok
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


def test_normal_f1_gaps_pass_but_unloaded_weekends_fail():
    assert evaluate(_facts(quali_with_time=19, quali_entries=22)).ok           # 3 without a time
    assert evaluate(_facts(results=22, starters=18, drivers_with_race_laps=18)).ok   # 4 did not start
    zandvoort = evaluate(_facts(results=22, starters=22, drivers_with_race_laps=4))
    assert not zandvoort.ok and "repair_session_laps_v1" in zandvoort.fails[0]
    monza = evaluate(_facts(results=0, quali_entries=0, race_date_passed=True, sessions=5))
    assert monza.stage == "not loaded" and not monza.ok and "5 empty sessions" in monza.fails[0]
    future = evaluate(_facts(results=0, quali_entries=0, race_date_passed=False, sessions=0))
    assert future.stage == "scheduled" and future.ok


def test_repair_matches_fastf1_laps_by_car_number_for_missing_entries_only():
    import pandas as pd

    from repair_session_laps_v1 import lap_rows, started

    laps = pd.DataFrame([
        {"DriverNumber": "3", "LapNumber": 1, "LapTime": pd.Timedelta(seconds=80), "Sector1Time": pd.NaT,
         "Sector2Time": pd.NaT, "Sector3Time": pd.NaT, "Compound": "MEDIUM"},
        {"DriverNumber": "1", "LapNumber": 1, "LapTime": pd.Timedelta(seconds=79), "Sector1Time": pd.NaT,
         "Sector2Time": pd.NaT, "Sector3Time": pd.NaT, "Compound": "MEDIUM"},
    ])
    rows = lap_rows(laps, {3: 501}, session_id=9)          # only car 3 is missing laps
    assert rows == [{"s": 9, "e": 501, "n": 1, "lt": 80.0, "s1": None, "s2": None, "s3": None, "c": "MEDIUM"}]
    assert not started("Did not start") and started("Retired") and started(None)


def test_known_source_gaps_become_documented_warnings():
    monza = evaluate(_facts(season_year=2018, round_number=14, drivers_with_race_laps=0))
    assert monza.ok and any("FastF1 has no race timing for 2018 Monza" in w for w in monza.warns)
    other = evaluate(_facts(season_year=2019, round_number=14, drivers_with_race_laps=0))
    assert not other.ok                                     # only the documented weekend is excused


def test_repair_finds_only_sessions_with_drivers_missing_laps(synthetic_db):
    from repair_session_laps_v1 import incomplete_sessions, missing_entries

    with synthetic_db.connect() as db:
        tx = db.begin()
        try:
            assert incomplete_sessions(db, 2024) == []           # every synthetic starter has laps
            session, entry = db.execute(text("""
                SELECT s.id, re.id FROM sessions s JOIN race_entries re ON re.race_id = s.race_id
                WHERE s.race_id = 5 AND s.session_type = 'R' AND re.driver_id = 3""")).one()
            db.execute(text("DELETE FROM laps WHERE session_id = :s AND race_entry_id = :e"),
                       {"s": session, "e": entry})
            [gap] = incomplete_sessions(db, 2024)
            assert (gap["race_id"], gap["starters"], gap["with_laps"]) == (5, 12, 11)   # counts drivers, not laps
            assert list(missing_entries(db, session, 5).values()) == [entry]
        finally:
            tx.rollback()


def test_name_normalisation_ignores_accents_case_and_spaces():
    from driver_names import normalise_name

    assert normalise_name("Sergio Pérez") == normalise_name("sergio  perez")
    assert normalise_name("Nico Hülkenberg") == normalise_name("Nico Hulkenberg")
    assert normalise_name("Kimi Räikkönen") == "kimi raikkonen"
    assert normalise_name("Mick Schumacher") != normalise_name("Michael Schumacher")


def test_merge_duplicate_driver_moves_everything_without_duplicating(synthetic_db):
    from driver_names import find_driver_id
    from merge_duplicate_drivers_v1 import duplicate_groups, merge_driver

    with synthetic_db.connect() as db:
        tx = db.begin()
        try:
            db.execute(text("INSERT INTO drivers (id, name) VALUES (100, 'Drivér 3')"))
            assert find_driver_id(db, "DRIVER 3") == 3                       # accent-insensitive lookup
            assert duplicate_groups(db) == [[(3, "Driver 3"), (100, "Drivér 3")]]
            # the duplicate has (a) an FP2-only entry in race 5, where Driver 3 raced, and
            # (b) the only entry in a new race 60
            fp2 = db.execute(text("INSERT INTO sessions (race_id, session_type, start_time) "
                                  "VALUES (5, 'FP2', now()) RETURNING id")).scalar()
            dup_entry = db.execute(text("""INSERT INTO race_entries (race_id, driver_id, team_id, car_number)
                                           VALUES (5, 100, 2, 3) RETURNING id""")).scalar()
            db.execute(text("INSERT INTO laps (session_id, race_entry_id, lap_number, lap_time) "
                            "VALUES (:s, :e, 1, 90.0)"), {"s": fp2, "e": dup_entry})
            db.execute(text("""INSERT INTO races (id, track_id, season_year, round_number, race_date, regulation_era)
                               VALUES (60, 1, 2024, 12, make_date(2024, 12, 20), 'e')"""))
            only_entry = db.execute(text("""INSERT INTO race_entries (race_id, driver_id, team_id, car_number)
                                            VALUES (60, 100, 2, 3) RETURNING id""")).scalar()
            race_laps_before = db.execute(text("""SELECT COUNT(*) FROM laps l JOIN race_entries re ON re.id = l.race_entry_id
                                                   WHERE re.driver_id = 3""")).scalar()
            log = []
            merge_driver(db, 100, 3, log)
            assert db.execute(text("SELECT COUNT(*) FROM drivers WHERE id = 100")).scalar() == 0
            keep_entry = db.execute(text("SELECT id FROM race_entries WHERE race_id = 5 AND driver_id = 3")).scalar()
            assert db.execute(text("SELECT race_entry_id FROM laps WHERE session_id = :s"), {"s": fp2}).scalar() == keep_entry
            assert db.execute(text("SELECT driver_id FROM race_entries WHERE id = :e"), {"e": only_entry}).scalar() == 3
            assert db.execute(text("SELECT COUNT(*) FROM race_entries WHERE id = :e"), {"e": dup_entry}).scalar() == 0
            after = db.execute(text("""SELECT COUNT(*) FROM laps l JOIN race_entries re ON re.id = l.race_entry_id
                                        WHERE re.driver_id = 3""")).scalar()
            assert after == race_laps_before + 1                            # the FP2 lap, nothing duplicated
            assert duplicate_groups(db) == []
        finally:
            tx.rollback()
