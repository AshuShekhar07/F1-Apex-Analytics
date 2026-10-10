"""Endpoints added for the new frontend: the season roster and the track list (synthetic fixture)."""



def test_roster_has_latest_team_and_season_and_career_stats(api_client):
    resp = api_client.get("/drivers/roster/2021")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    names = [d["name"] for d in body["drivers"]]
    assert names == ["Driver A", "Driver B", "Driver D", "Driver C"]          # by 2021 points, reserve left out
    a = body["drivers"][0]
    assert a["team"]["name"] == "Team Alpha" and a["team"]["color_hex"] == "#111111" and a["number"] == 1
    assert a["season"] == {"races": 2, "wins": 2, "podiums": 2, "points": 51.0, "poles": 1, "best_finish": 1,
                           "average_finish": 1.0, "average_qualifying": 1.5, "dnfs": 0}
    assert a["career_since_2018"]["races"] == 4 and a["career_since_2018"]["wins"] == 3
    assert a["career_since_2018"]["points"] == 94.0 and a["career_since_2018"]["poles"] == 3
    c = next(d for d in body["drivers"] if d["name"] == "Driver C")
    assert c["team"]["name"] == "Team Beta" and c["season"]["dnfs"] == 1 and c["season"]["points"] == 21.0
    assert api_client.get("/drivers/roster/2019").status_code == 404


def test_track_list(api_client):
    resp = api_client.get("/tracks")
    assert resp.status_code == 200, resp.text
    tracks = {t["name"]: t for t in resp.json()}
    assert tracks["Test Circuit One"]["races"] == 2 and tracks["Test Circuit One"]["first_season"] == 2021
    assert tracks["Test Circuit Two"]["races"] == 3 and float(tracks["Test Circuit Two"]["length_km"]) == 4.0


def test_season_results_matrix(api_client):
    resp = api_client.get("/seasons/2021/results")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [r["round"] for r in body["rounds"]] == [1, 2] and body["latest_round"] == 2
    assert [r["sprint"] for r in body["rounds"]] == [False, True]
    assert [d["name"] for d in body["drivers"]] == ["Driver A", "Driver B", "Driver D", "Driver C"]
    a = body["drivers"][0]
    assert a["points"] == 51.0 and a["wins"] == 2 and a["podiums"] == 2 and a["team"]["name"] == "Team Alpha"
    r2 = next(c for c in a["results"] if c["round"] == 2)
    assert r2["race"]["position"] == 1 and r2["race"]["grid"] == 3 and r2["sprint"]["position"] == 3
    assert r2["sprint"]["points"] == 1.0
    c = next(d for d in body["drivers"] if d["name"] == "Driver C")
    r1 = next(x for x in c["results"] if x["round"] == 1)
    assert r1["race"]["dnf"] is True and r1["race"]["classified"] is False and r1["race"]["status"] == "Engine"
    teams = {t["name"]: t for t in body["teams"]}
    assert [t["name"] for t in body["teams"]] == ["Team Alpha", "Team Beta"]
    assert teams["Team Alpha"]["points"] == 25 + 18 + 1 + 25 + 15 and teams["Team Alpha"]["wins"] == 2
    assert teams["Team Beta"]["by_round"] == [{"round": 1, "points": 15.0}, {"round": 2, "points": 35.0}]
    assert api_client.get("/seasons/2019/results").status_code == 404


def test_season_results_follow_team_changes(api_client):
    body = api_client.get("/seasons/2022/results").json()
    # 2022: Driver C drove for Alpha and Driver B for Beta
    c = next(d for d in body["drivers"] if d["name"] == "Driver C")
    assert c["team"]["name"] == "Team Alpha" and c["results"][0]["team_id"] == 1
    assert body["drivers"][0]["name"] == "Driver B" and body["drivers"][0]["team"]["name"] == "Team Beta"
    rounds = [r["round"] for r in body["rounds"]]
    assert rounds == [1, 24] and [r["raced"] for r in body["rounds"]] == [True, False]


def test_race_sessions_in_running_order(api_client):
    body = api_client.get("/races/1/sessions").json()
    assert body["weekend_format"] == "conventional"
    assert [s["type"] for s in body["sessions"]] == ["FP1", "FP2", "FP3", "Q", "R"]
    loaded = {s["type"]: s for s in body["sessions"]}
    assert loaded["FP1"]["loaded"] and loaded["FP1"]["has_laps"] and not loaded["FP1"]["has_results"]
    assert not loaded["FP2"]["loaded"] and loaded["FP2"]["start_time_utc"] is None
    assert loaded["Q"]["has_results"] and loaded["R"]["has_results"] and loaded["R"]["start_time_utc"]
    sprint = api_client.get("/races/2/sessions").json()
    assert [s["type"] for s in sprint["sessions"]] == ["FP1", "Q", "FP2", "S", "R"]
    assert [s["name"] for s in sprint["sessions"]][3] == "Sprint"
    assert api_client.get("/races/999/sessions").status_code == 404


def test_teammate_head_to_heads(api_client):
    body = api_client.get("/seasons/2021/teammates").json()
    pairs = {p["team"]["name"]: p for p in body["pairs"]}
    alpha = {d["name"]: d for d in pairs["Team Alpha"]["drivers"]}
    assert pairs["Team Alpha"]["rounds"] == 2
    assert alpha["Driver A"]["quali_wins"] == 1 and alpha["Driver B"]["quali_wins"] == 1
    assert alpha["Driver A"]["race_wins"] == 2 and alpha["Driver B"]["race_wins"] == 0
    assert alpha["Driver A"]["points"] == 51.0 and alpha["Driver B"]["points"] == 33.0
    beta = {d["name"]: d for d in pairs["Team Beta"]["drivers"]}
    # round 1: C retired, so only round 2 counts for the race head-to-head
    assert beta["Driver C"]["quali_wins"] == 2 and beta["Driver C"]["race_wins"] == 1 and beta["Driver D"]["race_wins"] == 0
    assert beta["Driver C"]["points"] == 21.0 and beta["Driver D"]["points"] == 29.0
    assert api_client.get("/seasons/2019/teammates").status_code == 404


def test_long_runs_and_calibration(api_client):
    # the fixture's practice has no 5-lap runs: an empty, well-formed answer
    body = api_client.get("/races/1/long-runs").json()
    assert body["session"] == "FP1" and body["drivers"] == []
    assert api_client.get("/races/999/long-runs").status_code == 404
    cal = api_client.get("/records/calibration").json()
    post = cal["stages"]["post_qualifying"]
    assert post["races"] > 100 and all(0 <= b["favourite_won"] <= 1 for b in post["bins"])
    assert sum(b["races"] for b in post["bins"]) <= post["races"]
