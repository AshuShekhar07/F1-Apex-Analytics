from sqlalchemy import create_engine, text

from repair_missing_points_v1 import apply, missing_points, parse_points, plan


def _payload(key, rows):
    return {"MRData": {"RaceTable": {"Races": [{key: [{"number": n, "points": p} for n, p in rows]}]}}}


def test_parse_points_reads_race_and_sprint_classifications():
    assert parse_points(_payload("Results", [("1", "25"), ("44", "18")]), sprint=False) == {"1": 25.0, "44": 18.0}
    assert parse_points(_payload("SprintResults", [("81", "8"), ("4", "0")]), sprint=True) == {"81": 8.0, "4": 0.0}
    assert parse_points(_payload("Results", [("1", "25")]), sprint=True) is None      # no sprint in the reply
    assert parse_points({"MRData": {"RaceTable": {"Races": []}}}, sprint=False) is None   # not published yet


def test_nan_points_are_filled_from_the_official_classification_or_cleared(fixture_db_url):
    engine = create_engine(fixture_db_url)
    with engine.connect() as db:
        tx = db.begin()
        try:
            # 2021 R2 sprint loaded before the results service had points: NaN for cars 3 and 4,
            # NULL for car 1
            sprint = db.execute(text("""
                SELECT s.id FROM sessions s JOIN races r ON r.id = s.race_id
                WHERE r.season_year = 2021 AND r.round_number = 2 AND s.session_type = 'S'""")).scalar()
            db.execute(text("""
                UPDATE race_results rr SET points = CASE WHEN re.car_number = 1 THEN NULL ELSE 'NaN'::numeric END
                FROM race_entries re
                WHERE re.id = rr.race_entry_id AND rr.session_id = :s AND re.car_number IN (1, 3, 4)"""), {"s": sprint})
            rows = missing_points(db, 2021)
            assert sorted(r["car_number"] for r in rows) == [1, 3, 4]

            # published: every value comes from the official classification, matched by car number
            urls = []

            def published(url):
                urls.append(url)
                return _payload("SprintResults", [("3", "3"), ("4", "2"), ("1", "1")])
            updates, report = plan(rows, 2021, fetch=published)
            assert urls == ["https://api.jolpi.ca/ergast/f1/2021/2/sprint.json?limit=100"]   # one call per session
            assert sorted(u["pts"] for u in updates) == [1.0, 2.0, 3.0]
            assert all("official" in line for line in report)

            # not published yet: NaN cleared to missing (NULL), an existing NULL left alone
            updates, report = plan(rows, 2021, fetch=lambda url: {"MRData": {"RaceTable": {"Races": []}}})
            assert len(updates) == 2 and all(u["pts"] is None for u in updates)
            # unreachable: same, and nothing raised
            def offline(url):
                raise ConnectionError("no network")
            updates, report = plan(rows, 2021, fetch=offline)
            assert len(updates) == 2 and "Jolpica unreachable" in report[0]

            apply(db, updates)
            assert db.execute(text("SELECT COUNT(*) FROM race_results WHERE points = 'NaN'")).scalar() == 0
            apply(db, plan(missing_points(db, 2021), 2021, fetch=published)[0])
            got = dict(db.execute(text("""
                SELECT re.car_number, rr.points FROM race_results rr JOIN race_entries re ON re.id = rr.race_entry_id
                WHERE rr.session_id = :s"""), {"s": sprint}).all())
            assert {k: float(v) for k, v in got.items()} == {1: 1.0, 2: 0.0, 3: 3.0, 4: 2.0}
            assert missing_points(db, 2021) == []
        finally:
            tx.rollback()


def test_title_chances_wait_for_missing_points_and_are_redone_after_nan(fixture_db_url):
    from update_latest_races import points_state

    engine = create_engine(fixture_db_url)
    with engine.connect() as db:
        tx = db.begin()
        try:
            assert points_state(db, 2021) == (None, False)
            db.execute(text("""
                UPDATE race_results SET points = 'NaN' WHERE id = (
                    SELECT rr.id FROM race_results rr JOIN sessions s ON s.id = rr.session_id
                    JOIN races r ON r.id = s.race_id
                    WHERE r.season_year = 2021 AND r.round_number = 2 AND s.session_type = 'R' LIMIT 1)"""))
            assert points_state(db, 2021) == (2, False)                # latest round incomplete: wait
            db.execute(text("UPDATE race_results SET points = 0 WHERE points = 'NaN'"))
            # a NaN in an earlier round (the round 1 race) also holds the title chances back;
            # a NULL there (official points never published) does not
            first = """id = (SELECT rr.id FROM race_results rr JOIN sessions s ON s.id = rr.session_id
                            JOIN races r ON r.id = s.race_id
                            WHERE r.season_year = 2021 AND r.round_number = 1 AND s.session_type = 'R'
                            ORDER BY rr.id LIMIT 1)"""
            db.execute(text(f"UPDATE race_results SET points = 'NaN' WHERE {first}"))
            assert points_state(db, 2021) == (1, False)
            db.execute(text(f"UPDATE race_results SET points = NULL WHERE {first}"))
            assert points_state(db, 2021) == (None, False)
            db.execute(text((__import__("pathlib").Path(__file__).resolve().parents[1]
                             / "migration_championship_projections_v1.sql").read_text()))
            db.execute(text("""
                INSERT INTO championship_projections (season_year, after_round, model_version, kind, entity_id,
                                                      p_title, points_now, expected_points, weekends_left)
                VALUES (2021, 2, 'test', 'drivers', 1, 0.5, 'NaN', 'NaN', 20)"""))
            assert points_state(db, 2021) == (None, True)              # stored from NaN points: redo
        finally:
            tx.rollback()
