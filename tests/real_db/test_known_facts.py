"""Real-data regression tests: the API must reproduce publicly known F1 facts.

Runs only with APEX21_REAL_DB_TESTS=1 against DATABASE_URL (the real Apex21
database). Entities are looked up by name, never by hard-coded id, so the
tests survive re-backfills. Each fact below is a matter of public record.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.database import SessionLocal
from app.main import app

client = TestClient(app)


def _one(sql, params, what):
    with SessionLocal() as db:
        rows = db.execute(text(sql), params).all()
    if len(rows) != 1:
        pytest.fail(f"expected exactly one {what}, found {len(rows)}: {rows[:5]}")
    return rows[0][0]


def driver_id(pattern):
    return _one("SELECT id FROM drivers WHERE name ILIKE :p", {"p": pattern}, f"driver matching {pattern!r}")


def race_id(season, *patterns):
    clauses = " OR ".join(f"t.name ILIKE :p{i} OR t.country ILIKE :p{i}" for i in range(len(patterns)))
    params = {f"p{i}": p for i, p in enumerate(patterns)} | {"season": season}
    return _one(
        f"SELECT r.id FROM races r JOIN tracks t ON t.id = r.track_id WHERE r.season_year = :season AND ({clauses})",
        params, f"{season} race at {patterns}",
    )


def track_id(*patterns):
    clauses = " OR ".join(f"name ILIKE :p{i}" for i in range(len(patterns)))
    return _one(f"SELECT id FROM tracks WHERE {clauses}", {f"p{i}": p for i, p in enumerate(patterns)},
                f"track matching {patterns}")


# --- championship points (race + sprint, no double counting) -------------

def test_2021_final_points_verstappen_hamilton():
    ver = client.get(f"/drivers/{driver_id('%Verstappen%')}/season/2021").json()
    ham = client.get(f"/drivers/{driver_id('%Hamilton%')}/season/2021").json()
    assert ver["total_points"] == 395.5
    assert ham["total_points"] == 387.5


def test_2021_compare_matches_championship_totals():
    ver, ham = driver_id("%Verstappen%"), driver_id("%Hamilton%")
    body = client.get(f"/compare/{ver}/{ham}?season=2021").json()
    assert body["combined_points_total"] == {str(ver): 395.5, str(ham): 387.5}


# --- 2024 British Grand Prix ----------------------------------------------

def test_2024_british_gp_qualifying_top_three_and_pole_time():
    rid = race_id(2024, "%Silverstone%", "%Great Britain%", "%United Kingdom%")
    results = client.get(f"/races/{rid}/qualifying").json()["results"]
    top3 = [r["driver_name"] for r in results[:3]]
    assert "Russell" in top3[0] and "Hamilton" in top3[1] and "Norris" in top3[2], top3
    assert results[0]["q3_time"] == "1:25.819"
    assert results[0]["gap_to_pole"] == 0.0


def test_2024_british_gp_winner():
    rid = race_id(2024, "%Silverstone%", "%Great Britain%", "%United Kingdom%")
    results = client.get(f"/races/{rid}").json()["results"]
    assert "Hamilton" in results[0]["driver_name"]
    assert results[0]["finishing_position"] == 1


# --- records ----------------------------------------------------------------

def test_record_verstappen_ten_race_win_streak():
    records = {r["category"]: r for r in client.get("/records/all-time").json()["records"]}
    streak = records["Longest consecutive race-win streak"]
    assert "Verstappen" in streak["driver_name"]
    assert streak["value"] == 10


def test_record_red_bull_2023_team_wins():
    records = {r["category"]: r for r in client.get("/records/all-time").json()["records"]}
    team = records["Most race wins by a team in a single season"]
    assert "Red Bull" in team["driver_name"]
    assert team["value"] == 21
    assert team["context"]["season_year"] == 2023


# --- driver profile ---------------------------------------------------------

def test_perez_red_bull_stint_2021_to_2024():
    history = client.get(f"/drivers/{driver_id('Sergio P%rez')}/profile").json()["team_history"]
    red_bull = [h for h in history if "Red Bull" in h["team_name"]]
    assert [(h["start_year"], h["end_year"]) for h in red_bull] == [(2021, 2024)]


# --- track intelligence -----------------------------------------------------

def test_monaco_has_less_position_change_than_austin():
    monaco = client.get(f"/tracks/{track_id('%Monaco%')}/overtaking-index").json()
    austin = client.get(f"/tracks/{track_id('%Americas%', '%Austin%')}/overtaking-index").json()
    assert monaco["overtaking"]["overall"]["avg_position_change"] < austin["overtaking"]["overall"]["avg_position_change"]


# --- data integrity invariants ---------------------------------------------

INVARIANTS = {
    "duplicate race_results per session/entry": """
        SELECT session_id, race_entry_id FROM race_results
        GROUP BY session_id, race_entry_id HAVING COUNT(*) > 1
    """,
    "duplicate race_entries per race/driver": """
        SELECT race_id, driver_id FROM race_entries
        GROUP BY race_id, driver_id HAVING COUNT(*) > 1
    """,
    "race/sprint session with results but not exactly one winner": """
        SELECT rr.session_id FROM race_results rr
        GROUP BY rr.session_id HAVING COUNT(*) FILTER (WHERE rr.finishing_position = 1) <> 1
    """,
    "race_results attached to a non R/S session": """
        SELECT rr.id FROM race_results rr JOIN sessions s ON s.id = rr.session_id
        WHERE s.session_type NOT IN ('R', 'S')
    """,
    "qualifying_results attached to a non-Q session": """
        SELECT qr.id FROM qualifying_results qr JOIN sessions s ON s.id = qr.session_id
        WHERE s.session_type <> 'Q'
    """,
    "sprint result worth more than 8 points": """
        SELECT rr.id FROM race_results rr JOIN sessions s ON s.id = rr.session_id
        WHERE s.session_type = 'S' AND rr.points > 8
    """,
    "race result worth more than 26 points": """
        SELECT rr.id FROM race_results rr JOIN sessions s ON s.id = rr.session_id
        WHERE s.session_type = 'R' AND rr.points > 26
    """,
    "duplicate session type within a race weekend": """
        SELECT race_id, session_type FROM sessions
        GROUP BY race_id, session_type HAVING COUNT(*) > 1
    """,
}


@pytest.mark.parametrize("name", sorted(INVARIANTS))
def test_data_invariant(name):
    with SessionLocal() as db:
        violations = db.execute(text(INVARIANTS[name])).all()
    assert violations == [], f"{name}: {len(violations)} violations, e.g. {violations[:5]}"
