"""API contract tests against a throwaway database seeded with synthetic rows.

See tests/fixture_data.py for the layout. These pin down SQL logic -- session
filters, sprint/race separation, ranking, gaps-and-islands -- and each
regression test names the bug it guards. Real-world facts live in tests/real_db/.
"""

import pytest


@pytest.fixture(scope="module")
def client(api_client):
    return api_client


def _by_name(rows, key, name):
    return next(r for r in rows if r[key] == name)


# --- races ---------------------------------------------------------------

def test_race_list_filters_by_season(client):
    races = client.get("/races?season=2021").json()
    assert [(r["season_year"], r["round_number"]) for r in races] == [(2021, 1), (2021, 2)]


def test_race_detail_uses_race_session_results_only(client):
    body = client.get("/races/2").json()
    # sprint winner (Driver C) must not leak into the race classification
    assert body["results"][0]["driver_name"] == "Driver A"
    assert [r["points"] for r in body["results"]] == [25, 18, 15, 12]


def test_race_detail_lapped_display(client):
    body = client.get("/races/1").json()
    lapped = _by_name(body["results"], "driver_name", "Driver D")
    assert lapped["display"] == "+1 Lap"
    winner = _by_name(body["results"], "driver_name", "Driver A")
    assert winner["display"] == "1:30:00.123"
    retired = _by_name(body["results"], "driver_name", "Driver C")
    assert retired["display"] == "Engine"


def test_position_battle_counts_lapped_finishers(client):
    """Regression: positions_gained was only computed for status 'Finished'."""
    results = client.get("/races/1/position-battle").json()["results"]
    lapped = _by_name(results, "driver_name", "Driver D")
    assert lapped["positions_gained"] == 1  # grid 4 -> finish 3
    retired = _by_name(results, "driver_name", "Driver C")
    assert retired["positions_gained"] is None
    assert lapped["best_position"] == 3 and lapped["worst_position"] == 4


def test_qualifying_gap_and_elimination_labels(client):
    """Regression: a Q3 participant with no Q3 time was labelled a Q2 elimination."""
    results = client.get("/races/3/qualifying").json()["results"]
    by_driver = {r["driver_name"]: r for r in results}
    assert by_driver["Driver A"]["gap_to_pole"] == 0.0
    assert by_driver["Driver A"]["improvement_q1_to_q3"] == 1.0
    assert by_driver["Driver B"]["eliminated_in"] is None  # P10, reached Q3, no time
    assert by_driver["Driver C"]["eliminated_in"] == "Q2"
    assert by_driver["Driver D"]["eliminated_in"] == "Q1"
    assert by_driver["Driver B"]["gap_to_pole"] == 0.6  # 87.6 (Q2) - 87.0


def test_qualifying_grid_penalty(client):
    results = client.get("/races/2/qualifying").json()["results"]
    by_driver = {r["driver_name"]: r for r in results}
    assert by_driver["Driver B"]["grid_penalty"] == 1  # qualified P1, started P2
    assert by_driver["Driver D"]["grid_penalty"] is None


def test_wet_qualifying_flag_absent_without_weather(client):
    assert client.get("/races/1/qualifying").json()["wet_qualifying"] is None


# --- practice ------------------------------------------------------------

def test_practice_includes_reserve_and_ignores_deleted_laps(client):
    """Regression: reserve drivers were dropped (and ranks recomputed without them);
    laps deleted for track limits counted as a driver's best lap."""
    body = client.get("/races/1/practice/fp1").json()
    order = [(r["position"], r["driver"], float(r["best_lap_time"])) for r in body["results"]]
    assert order == [
        (1, "Driver A", 90.2),
        (2, "Driver R", 90.3),
        (3, "Driver B", 90.4),
        (4, "Driver C", 91.5),
        (5, "Driver D", 92.0),
    ]
    reserve = _by_name(body["results"], "driver", "Driver R")
    assert reserve["role"] == "reserve"
    assert _by_name(body["results"], "driver", "Driver B")["total_laps"] == 2


def test_practice_delta_to_fastest_race_lap_at_track(client):
    body = client.get("/races/1/practice/FP1").json()
    fastest = body["results"][0]
    assert float(fastest["gap_to_fastest"]) == 0.0
    # fastest valid race lap at Test Circuit One is 94.5 (2021 R1)
    assert float(fastest["delta_to_track_record"]) == pytest.approx(90.2 - 94.5)


def test_practice_rejects_unknown_session(client):
    assert client.get("/races/1/practice/XYZ").status_code == 422


# --- drivers -------------------------------------------------------------

def test_driver_season_separates_race_and_sprint_points(client):
    body = client.get("/drivers/1/season/2021").json()
    assert body["races"] == 2
    assert body["wins"] == 2
    assert body["race_points"] == 50.0
    assert body["sprint_points"] == 1.0
    assert body["total_points"] == 51.0


def test_driver_season_counts_dnfs(client):
    assert client.get("/drivers/3/season/2021").json()["dnfs"] == 1


def test_driver_history_career_count_ignores_pagination(client):
    """Regression: career_summary.races was the page size, not the career total."""
    body = client.get("/drivers/1/history?limit=1&offset=0").json()
    assert len(body["race_history"]) == 1
    summary = body["career_summary"]
    assert summary["races"] == 4
    assert summary["wins"] == 3
    assert summary["race_points"] == 93.0  # 25 + 25 + 18 + 25
    assert summary["sprint_points"] == 1.0


def test_driver_profile_team_history_islands(client):
    """Driver C: Beta 2021, Alpha 2022, Beta 2023 -> three separate stints."""
    history = client.get("/drivers/3/profile").json()["team_history"]
    assert [(h["team_name"], h["start_year"], h["end_year"]) for h in history] == [
        ("Team Beta", 2021, 2021),
        ("Team Alpha", 2022, 2022),
        ("Team Beta", 2023, 2023),
    ]


def test_driver_profile_last_win_is_race_not_sprint(client):
    profile = client.get("/drivers/3/profile").json()
    assert profile["last_gp_win"] is None  # Driver C only won a sprint


# --- comparisons ---------------------------------------------------------

def test_compare_splits_race_and_sprint_points(client):
    body = client.get("/compare/1/3?season=2021").json()
    assert body["races_compared"] == 2
    assert body["race_points_total"] == {"1": 50.0, "3": 18.0}
    assert body["sprint_points_total"] == {"1": 1.0, "3": 3.0}
    assert body["combined_points_total"] == {"1": 51.0, "3": 21.0}
    assert body["race_h2h"] == {"1": 2, "3": 0}


def test_compare_teammates_only(client):
    body = client.get("/compare/1/3?teammates_only=true").json()
    assert body["were_teammates"] is True
    assert body["races_compared"] == 1
    assert body["teammate_periods"] == [{"team_name": "Team Alpha", "start_year": 2022, "end_year": 2022}]


def test_compare_same_driver_rejected(client):
    assert client.get("/compare/1/1").status_code == 422


# --- teams ---------------------------------------------------------------

def test_team_season_summary(client):
    body = client.get("/teams/1/season/2021").json()
    assert body["races"] == 2
    assert body["wins"] == 2
    assert body["race_points"] == 83.0  # A 50 + B 33
    assert body["sprint_points"] == 1.0
    assert [d["driver_name"] for d in body["drivers"]] == ["Driver A", "Driver B"]


def test_team_not_found(client):
    assert client.get("/teams/999/season/2021").status_code == 404


# --- records -------------------------------------------------------------

def test_records_use_race_sessions_only(client):
    records = {r["category"]: r for r in client.get("/records/all-time").json()["records"]}
    assert records["Most race wins"]["driver_name"] == "Driver A"
    assert records["Most race wins"]["value"] == 3
    assert records["Most pole positions"]["driver_name"] == "Driver A"
    assert records["Most pole positions"]["value"] == 3
    streak = records["Longest consecutive race-win streak"]
    assert (streak["driver_name"], streak["value"]) == ("Driver A", 2)
    team = records["Most race wins by a team in a single season"]
    assert (team["driver_name"], team["value"], team["context"]["season_year"]) == ("Team Alpha", 2, 2021)


# --- wet weather ---------------------------------------------------------

def test_wet_ranking_does_not_mix_sprint_and_race_results(client):
    """Regression: the teammate's result was joined without a session filter, so on
    sprint weekends the race result was also compared with the teammate's sprint result."""
    body = client.get(
        "/drivers/wet-weather-ranking?min_wet_races=1&min_dry_races=1&include_qualifying=false"
    ).json()
    a = _by_name(body["race_rankings"], "driver_name", "Driver A")
    assert a["wet_sessions_counted"] == 1
    assert a["wet_avg_margin_over_teammate"] == 2.0  # B finished P3 behind A's P1
    assert a["dry_sessions_counted"] == 3
    assert a["dry_avg_margin_over_teammate"] == pytest.approx(1.33, abs=0.01)


# --- tracks --------------------------------------------------------------

def test_overtaking_index_by_era(client):
    body = client.get("/tracks/1/overtaking-index").json()
    eras = {e["regulation_era"]: e for e in body["overtaking"]["by_regulation_era"]}
    # |2-1| + |1-2| + |4-3| + |3-4| over 4 cars; note the retired car (C) is counted
    assert eras["era1_13inch"]["avg_position_change"] == 1.0
    assert eras["era2_18inch_groundeffect"]["avg_position_change"] == 0.0


def test_track_stats_winners(client):
    body = client.get("/tracks/1/stats").json()
    assert body["total_races_recorded"] == 2
    assert [(w["season_year"], w["driver_name"]) for w in body["historical_winners"]] == [
        (2022, "Driver B"),
        (2021, "Driver A"),
    ]


# --- predictions ---------------------------------------------------------

def test_predict_and_compete_uses_prediction_made_before_the_round(client):
    """Regression: next_race rows are stored under as_of_round = last completed
    round, so round N's prediction is as_of_round N-1. The endpoint read
    as_of_round N -- the prediction for the *following* race."""
    body = client.get("/predict-and-compete/2021/2").json()
    assert body["model_prediction"]["top_pick"]["driver_name"] == "Driver A"
    assert float(body["model_prediction"]["top_pick"]["probability_pct"]) == 60.0
    assert body["actual_result"]["winner"]["driver_name"] == "Driver A"
    assert body["actual_result"]["model_top_pick_was_correct"] is True


def test_predict_and_compete_round_without_prior_prediction(client):
    body = client.get("/predict-and-compete/2021/1").json()
    assert body["model_prediction"] is None
    assert body["race_has_happened"] is True


def test_predict_and_compete_accepts_round_24(client):
    """Regression: rounds were capped at 23, but 2024-2026 calendars have 24 rounds."""
    body = client.get("/predict-and-compete/2022/24").json()
    assert body["race_has_happened"] is False
    assert client.get("/predict-and-compete/2022/23").status_code == 404


def test_prediction_history_empty_is_ok(client):
    assert client.get("/predictions/2021/wdc/history").json()["history"] == []
    assert client.get("/predictions/2021/wdc").status_code == 404
    assert client.get("/predictions/2021/bogus").status_code == 422


# --- validation ----------------------------------------------------------

@pytest.mark.parametrize("path,status", [
    ("/races?season=2017", 422),
    ("/races/999999", 404),
    ("/drivers/999999/profile", 404),
    ("/drivers/1/season/2017", 422),
    ("/predict-and-compete/2017/1", 422),
    ("/predict-and-compete/2021/31", 422),
])
def test_invalid_requests(client, path, status):
    assert client.get(path).status_code == status
