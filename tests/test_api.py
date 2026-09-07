from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_races_valid():
    response = client.get("/races?season=2024")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0
    assert data[0]["season_year"] == 2024


def test_races_invalid_season():
    response = client.get("/races?season=2017")
    assert response.status_code == 422


def test_race_not_found():
    response = client.get("/races/999999")
    assert response.status_code == 404


def test_position_battle_valid():
    response = client.get("/races/137/position-battle")
    assert response.status_code == 200
    data = response.json()
    assert data["race_id"] == 137
    assert data["track_name"] == "Silverstone"
    assert isinstance(data["results"], list)


def test_strategy_valid():
    response = client.get(
        "/strategy/predict?track_id=10&race_date=2024-07-07"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "primary_strategy" in data
    assert "confidence_pct" in data


def test_driver_season_valid():
    response = client.get("/drivers/2/season/2023")
    assert response.status_code == 200
    data = response.json()
    assert data["driver"]["id"] == 2
    assert data["season"] == 2023
    assert "career_summary" not in data


def test_driver_invalid_season():
    response = client.get("/drivers/2/season/2017")
    assert response.status_code == 422


def test_driver_not_found():
    response = client.get("/drivers/999999/profile")
    assert response.status_code == 404


def test_driver_history_pagination():
    response = client.get("/drivers/2/history?limit=5&offset=0")
    assert response.status_code == 200
    data = response.json()
    assert len(data["race_history"]) <= 5


def test_driver_history_invalid_limit():
    response = client.get("/drivers/2/history?limit=0")
    assert response.status_code == 422


def test_team_season_valid():
    response = client.get("/teams/1/season/2023")
    assert response.status_code == 200
    data = response.json()
    assert data["team_id"] == 1
    assert data["season"] == 2023
    assert isinstance(data["drivers"], list)


def test_team_not_found():
    response = client.get("/teams/999999/season/2023")
    assert response.status_code == 404


def test_predict_and_compete_invalid_season():
    response = client.get("/predict-and-compete/2017/1")
    assert response.status_code == 422


def test_predict_and_compete_invalid_round():
    response = client.get("/predict-and-compete/2026/24")
    assert response.status_code == 422
