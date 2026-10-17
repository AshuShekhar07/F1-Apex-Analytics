import pytest

import car_ratings_v1 as cr
import cars_catalogue_v1 as catalogue


def test_ratings_rank_every_team_season_together():
    r = cr.rank_ratings({"a": 0.1, "b": -0.5, "c": 0.9, "d": 0.1}, higher_is_better=False)
    assert r == {"b": 100, "a": 50, "d": 50, "c": 0}            # ties share the average rank
    assert cr.rank_ratings({"x": 3.0}, True) == {"x": 100}
    assert cr.rank_ratings({}, True) == {}


def test_overall_weights_pace_twice_and_needs_pace():
    assert cr.overall({"quali": 100, "race": 90, "reliability": 50, "speed": 0}) == 86   # (200 + 180 + 50) / 5
    assert cr.overall({"quali": None, "race": 80, "reliability": 40, "speed": 10}) == 67  # (160 + 40) / 3
    assert cr.overall({"quali": None, "race": None, "reliability": 90, "speed": 50}) is None


def test_character_from_top_speed_against_lap_pace():
    assert cr.character(1.5, 0.2)["kind"] == "low-drag"
    assert cr.character(-1.0, 0.5)["kind"] == "high-downforce"
    assert cr.character(0.4, 0.1)["kind"] == "balanced"
    assert cr.character(None, 0.1) is None


def test_gaps_to_the_median_team_per_race():
    # one race, five teams: times 100, 100.5, 101, 101.5, 102 -> median 101
    rows = [(2020, 1, t, v) for t, v in enumerate([100.0, 100.5, 101.0, 101.5, 102.0], start=1)]
    gaps, best = cr._gaps_by_race(rows)
    assert round(gaps[(2020, 1)][0], 3) == round((100 / 101 - 1) * 100, 3)
    assert gaps[(2020, 3)] == [0.0]
    assert best == {(2020, 1): 1}
    speed, fastest = cr._gaps_by_race([(2020, 1, t, v) for t, v in enumerate([320.0, 318, 330, 325, 322], 1)], False)
    assert speed[(2020, 3)] == [8.0] and fastest == {(2020, 3): 1}
    assert cr._gaps_by_race(rows[:4]) == ({}, {})                # too few teams to compare


def test_catalogue_matches_database_team_names_by_season():
    assert catalogue.find(2020, "Mercedes").car == "Mercedes-AMG F1 W11 EQ Performance"
    assert catalogue.find(2024, "RB").car == "RB VCARB 01"
    assert catalogue.find(2025, "Racing Bulls").car == "Racing Bulls VCARB 02"
    assert catalogue.find(2018, "Toro Rosso").car == "Toro Rosso STR13"
    assert catalogue.find(2026, "Haas F1 Team").car == "Haas VF-26"
    assert catalogue.find(2026, "Kick Sauber").car == "Audi R26"       # the team became Audi
    assert catalogue.find(2023, "Audi") is None
    keys = [c.key for c in catalogue.CARS]
    assert len(keys) == len(set(keys))
    assert all(len(p) == 7 and p.startswith("#") for c in catalogue.CARS for p in (c.palette or ()))


def test_results_and_reliability_on_the_fixture(fixture_db_url):
    from sqlalchemy import create_engine

    engine = create_engine(fixture_db_url)
    with engine.connect() as db:
        out = cr.compute(db)
    engine.dispose()
    by = {(c["season"], c["team_name"]): c for c in out["cars"]}
    alpha, beta = by[(2021, "Team Alpha")], by[(2021, "Team Beta")]
    assert alpha["results"] == {"position": 1, "points": 84.0, "races": 2, "season_races": 2, "wins": 2, "poles": 2,
                                "podiums": 4, "one_twos": 1}
    assert beta["metrics"]["mechanical"] == 1 and beta["metrics"]["starts"] == 4 and beta["metrics"]["finish_rate"] == 0.75
    assert alpha["metrics"]["quali_gap_pct"] is None           # two teams: too few to rate pace
    assert alpha["car"] is None and alpha["key"] == "2021-teamalpha"
    assert out["seasons"] == [2023, 2022, 2021]


def test_cars_endpoint(api_client):
    r = api_client.get("/cars")
    assert r.status_code == 200 and r.json()["count"] >= 6
    one = api_client.get("/cars", params={"season": 2021}).json()
    assert {c["season"] for c in one["cars"]} == {2021}
    assert api_client.get("/cars", params={"season": 2030}).status_code == 404
    assert api_client.get("/cars", params={"season": 2017}).status_code == 422


@pytest.mark.parametrize("status", ["Accident", "Collision", "Spun off"])
def test_crashes_are_not_held_against_the_car(status):
    assert status in cr.INCIDENT_STATUSES and status not in cr.MECHANICAL_STATUSES
    assert "Engine" in cr.MECHANICAL_STATUSES and "Retired" not in cr.MECHANICAL_STATUSES


def test_only_free_photo_licences_are_used():
    from fetch_livery_photos_v1 import free_licence

    for ok in ("CC BY-SA 4.0", "CC BY 2.0", "CC0", "Public domain", "CC BY-SA 3.0 de", "CC-BY-SA-4.0", "PD-self"):
        assert free_licence(ok), ok
    for bad in ("Fair use", "CC BY-NC 2.0", "CC BY-ND 4.0", "CC BY-NC-SA 2.0", "GFDL", "", None):
        assert not free_licence(bad), bad
