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
