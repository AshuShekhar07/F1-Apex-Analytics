from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_root():
    r = client.get("/")
    assert r.status_code == 200
    assert r.json()["status"] == "Apex21 API is running"


def test_compare():
    r = client.get("/compare/18/16")
    assert r.status_code == 200


def test_wet_weather_ranking():
    r = client.get("/drivers/wet-weather-ranking")
    assert r.status_code == 200


def test_driver_history():
    r = client.get("/drivers/2/history?limit=5&offset=0")
    assert r.status_code == 200


def test_driver_profile():
    r = client.get("/drivers/2/profile")
    assert r.status_code == 200


def test_driver_season():
    r = client.get("/drivers/2/season/2023")
    assert r.status_code == 200


def test_predict_and_compete():
    r = client.get("/predict-and-compete/2024/12")
    assert r.status_code == 200


def test_constructor_prediction():
    r = client.get("/predictions/2023/constructors")
    assert r.status_code in (200, 404)


def test_prediction():
    r = client.get("/predictions/2023/wdc")
    assert r.status_code in (200, 404)


def test_prediction_history():
    r = client.get("/predictions/2023/wdc/history")
    assert r.status_code in (200, 404)


def test_races():
    r = client.get("/races?season=2024")
    assert r.status_code == 200


def test_race():
    r = client.get("/races/137")
    assert r.status_code == 200


def test_position_battle():
    r = client.get("/races/137/position-battle")
    assert r.status_code == 200


def test_practice():
    r = client.get("/races/137/practice/FP1")
    assert r.status_code == 200


def test_qualifying():
    r = client.get("/races/137/qualifying")
    assert r.status_code == 200


def test_race_strategy():
    r = client.get("/races/137/strategy")
    assert r.status_code == 200


def test_records():
    r = client.get("/records/all-time")
    assert r.status_code == 200


def test_strategy_prediction():
    r = client.get(
        "/strategy/predict?track_id=10&race_date=2024-07-07"
    )
    assert r.status_code == 200


def test_team_season():
    r = client.get("/teams/1/season/2023")
    assert r.status_code == 200


def test_overtaking_index():
    r = client.get("/tracks/10/overtaking-index")
    assert r.status_code == 200


def test_track_stats():
    r = client.get("/tracks/10/stats")
    assert r.status_code == 200


def test_global_invalid_cases():
    cases = [
        ("/races?season=2017", 422),
        ("/predict-and-compete/2017/1", 422),
        ("/predict-and-compete/2026/24", 422),
        ("/races/137/practice/XYZ", 422),
        ("/compare/10/10", 422),
        ("/drivers/999999/profile", 404),
        ("/teams/999999/season/2023", 404),
        ("/races/999999", 404),
    ]

    for path, expected_status in cases:
        r = client.get(path)
        assert r.status_code == expected_status, (
            f"{path}: expected {expected_status}, got {r.status_code}"
        )
