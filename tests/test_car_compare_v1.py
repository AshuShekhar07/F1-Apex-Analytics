import math

import pytest

import car_compare_v1 as cc


def test_car_only_averages_both_cars_unless_one_did_not_show_the_pace():
    assert cc.team_value([90.0, 90.5], car_only=False) == 90.0           # the quicker car
    assert cc.team_value([90.0, 90.5], car_only=True) == pytest.approx(90.25)
    assert cc.team_value([90.0, 92.0], car_only=True) == 90.0            # 2.2% off: a crash or failure
    assert cc.team_value([90.0], car_only=True) == 90.0
    assert cc.team_value([], car_only=True) is None


def test_race_standing_is_relative_to_the_field_and_its_spread():
    tight = {1: 90.0, 2: 90.1, 3: 90.2, 4: 90.3, 5: 90.4}
    loose = {1: 89.0, 2: 90.0, 3: 91.0, 4: 92.0, 5: 93.0}
    t, lo = cc.race_standing(tight), cc.race_standing(loose)
    # the leader is ahead of the median team in both, and stands out by the same number of spreads
    assert t[1]["gap"] < 0 and t[1]["z"] > 0 and lo[1]["z"] == pytest.approx(t[1]["z"], rel=0.01)
    assert t[3]["z"] == pytest.approx(0)
    # margin: + ahead of the best other car, - behind the quickest
    assert t[1]["margin"] == pytest.approx((90.1 / 90.2 - 90.0 / 90.2) * 100)
    assert t[2]["margin"] < 0
    assert cc.race_standing({1: 90.0, 2: 91.0}) == {}                    # too few teams to compare
    fast = cc.race_standing({1: 330.0, 2: 320, 3: 321, 4: 322, 5: 323}, lower_is_better=False)
    assert fast[1]["z"] > 0 and fast[1]["margin"] == pytest.approx(7.0)


def test_circuit_types_are_terciles_over_every_race():
    kinds = cc.speed_classes({1: 160.0, 2: 180, 3: 200, 4: 220, 5: 240, 6: 250})
    assert kinds[1] == "slow" and kinds[6] == "fast" and set(kinds.values()) == {"slow", "medium", "fast"}
    assert cc.speed_classes({1: 200.0}) == {}


def test_tyre_wear_slope():
    laps = [(n, 90 + 0.05 * n) for n in range(5, 15)]
    assert cc.stint_slope(laps) == pytest.approx(0.05)
    assert cc.stint_slope(laps[:3]) is None


def test_average_driver_win_share_sums_to_one():
    share = cc.softmax({1: 1.0, 2: 0.5, 3: 0.0})
    assert sum(share.values()) == pytest.approx(1.0)
    assert share[1] == pytest.approx(math.e / (math.e + math.exp(0.5) + 1))


@pytest.mark.parametrize("season,races,sprints,expected", [
    (2018, 21, 0, 21 * 43),                 # 25 + 18, no sprints, no fastest-lap point
    (2021, 22, 3, 22 * 44 + 3 * 5),         # fastest-lap point; sprints 3-2-1
    (2023, 22, 6, 22 * 44 + 6 * 15),        # sprints 8-7-...
    (2026, 24, 6, 24 * 43 + 6 * 15),        # no fastest-lap point
])
def test_points_ceiling_follows_each_seasons_points_system(season, races, sprints, expected):
    assert cc.points_ceiling(season, races, sprints) == expected


def test_cars_endpoint_carries_the_era_fair_measures(api_client):
    body = api_client.get("/cars").json()
    assert set(body["eras"]) >= {"era1_13inch", "era2_18inch_groundeffect", "era3_2026regs"}
    car = body["cars"][0]
    assert {"dominance", "margin", "model", "fingerprint", "achievements", "drivers", "era", "pace_rank"} <= set(car["fair"])
    one = api_client.get(f"/cars/{car['key']}").json()
    assert one["key"] == car["key"] and isinstance(one["races"], list)
    assert api_client.get("/cars/2021-nosuchteam").status_code == 404
    assert api_client.get("/cars/not-a-key").status_code == 422
