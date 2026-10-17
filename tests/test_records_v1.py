from records_v1 import led_every_lap, streak_runs, streak_tables


def test_streak_runs():
    assert streak_runs([True, True, False, True, False, False, True, True, True]) == [(0, 1), (3, 3), (6, 8)]
    assert streak_runs([]) == [] and streak_runs([False]) == []


def _race(rid, finish, quali=None, points=0.0, classified=True):
    return {"race_id": rid, "season": 2024, "round": rid, "track": f"T{rid}", "race_date": f"2024-01-{rid:02d}",
            "driver_name": "X", "team_name": "T", "color_hex": None, "finish": finish, "quali": quali,
            "points": points, "classified": classified}


def test_longest_and_active_streaks():
    by_driver = {
        1: [_race(1, 1, 1), _race(2, 1, 2), _race(3, 1, 1), _race(4, 4, 1), _race(5, 1, 1)],
        2: [_race(1, 2), _race(2, 3), _race(3, 2), _race(4, 1), _race(5, 2)],
        3: [_race(1, 3), _race(2, 2)],                       # left after race 2: never active
    }
    t = streak_tables(by_driver, latest_race=5)
    wins = t["wins"]
    assert [(s["driver_id"], s["length"], s["from"]["race_id"], s["to"]["race_id"]) for s in wins["longest"]] == \
        [(1, 3, 1, 3), (2, 1, 4, 4)]
    assert [(s["driver_id"], s["length"]) for s in wins["active"]] == [(1, 1)]
    podiums = t["podiums"]
    assert podiums["longest"][0]["driver_id"] == 2 and podiums["longest"][0]["length"] == 5
    assert [s["driver_id"] for s in podiums["active"]] == [2, 1]
    assert all(s["driver_id"] != 3 for s in podiums["active"])
    assert t["poles"]["longest"][0]["length"] == 3                 # races 3-5 (race 1 on its own, not race 2)


def test_led_every_lap():
    assert led_every_lap({1: [7, 8], 2: [7, 8]}, 7) is True
    assert led_every_lap({1: [8, 7], 2: [7, 8]}, 7) is False
    assert led_every_lap({1: [7], 2: [8]}, 7) is None              # no time for the winner on lap 2
    assert led_every_lap({}, 7) is None


def test_records_endpoint(api_client):
    body = api_client.get("/records/history").json()
    assert body["races"] == 4 and (body["first_season"], body["last_season"]) == (2021, 2023)
    assert body["latest_race"]["race_id"] == 4
    # race 1: pole, win and fastest lap for Driver A, but Driver B led lap 1 -> hat-trick, no Grand Chelem
    assert [(h["race_id"], h["driver_name"], h["grand_chelem"]) for h in body["hat_tricks"]] == [(1, "Driver A", False)]
    wins = body["streaks"]["wins"]
    assert (wins["longest"][0]["driver_name"], wins["longest"][0]["length"]) == ("Driver A", 2)
    assert [(s["driver_name"], s["length"]) for s in wins["active"]] == [("Driver A", 1)]
    podiums = {s["driver_name"]: s["length"] for s in body["streaks"]["podiums"]["active"]}
    assert podiums == {"Driver A": 4, "Driver B": 4, "Driver C": 3}  # C's engine failure in race 1 broke his
    assert body["streaks"]["poles"]["active"][0]["length"] == 2
    assert body["leaders"]["wins"][0] == {**body["leaders"]["wins"][0], "driver_name": "Driver A", "count": 3}
    assert body["leaders"]["fastest_laps"][0]["count"] == 1 and body["leaders"]["hat_tricks"][0]["count"] == 1
    assert {t["track"]: (t["races"], t["pole_won"]) for t in body["pole_to_win"]} == \
        {"Test Circuit One": (2, 1), "Test Circuit Two": (2, 1)}
    assert body["pole_to_win_overall"] == {"races": 4, "pole_won": 2}
    assert (body["furthest_back_wins"][0]["race_id"], body["furthest_back_wins"][0]["grid"]) == (2, 3)
