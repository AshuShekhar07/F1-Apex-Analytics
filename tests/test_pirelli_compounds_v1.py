import pytest
from sqlalchemy import create_engine, text

from pirelli_compounds_v1 import compound_key, run_audit, same_rubber_known, validate_nominations

VALID = [("HARD", "C2"), ("MEDIUM", "C3"), ("SOFT", "C4")]


def test_compound_identity_is_season_scoped():
    assert compound_key(2024, "c3") == "2024:C3"
    assert same_rubber_known(2024, "C3", 2024, "C3")
    # the 2023 renumbering means equal numbers across seasons are not assumed equal
    assert not same_rubber_known(2022, "C3", 2023, "C3")
    assert not same_rubber_known(2023, "C3", 2024, "C3")


def test_valid_nomination():
    assert validate_nominations(2024, VALID) == []


@pytest.mark.parametrize("season,rows,fragment", [
    (2018, VALID, "predates C-numbers"),
    (2024, [("HARD", "C2"), ("MEDIUM", "C3")], "labels"),
    (2024, [("HARD", "C4"), ("MEDIUM", "C3"), ("SOFT", "C5")], "order"),
    (2024, [("HARD", "C4"), ("MEDIUM", "C5"), ("SOFT", "C6")], "C6"),
    (2024, [("HARD", "X1"), ("MEDIUM", "C3"), ("SOFT", "C4")], "Not a Pirelli"),
])
def test_invalid_nominations(season, rows, fragment):
    assert any(fragment in issue for issue in validate_nominations(season, rows))


def test_c6_allowed_in_2025():
    assert validate_nominations(2025, [("HARD", "C4"), ("MEDIUM", "C5"), ("SOFT", "C6")]) == []


def test_audit_against_fixture_db(fixture_db_url):
    engine = create_engine(fixture_db_url)
    with engine.connect() as db:
        trans = db.begin()
        for label, c in VALID:
            db.execute(text("INSERT INTO race_compound_nominations (race_id, label, c_compound) VALUES (3, :l, :c)"),
                       {"l": label, "c": c})
        db.execute(text("INSERT INTO race_compound_nominations (race_id, label, c_compound) VALUES (1, 'SOFT', 'C5')"))
        by_season, problems = run_audit(db, start_year=2021, end_year=2022)
        trans.rollback()
    engine.dispose()
    assert by_season[2022] == {"races": 1, "nominated": 1, "invalid": 0}
    assert by_season[2021] == {"races": 2, "nominated": 1, "invalid": 1}
    assert len(problems) == 1 and "labels" in problems[0]
