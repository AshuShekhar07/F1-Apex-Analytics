import pytest

from ideal_lap_v1 import best_laps, best_sectors, ideal_table


def _laps():
    # driver, lap, lap time, s1, s2, s3, deleted
    return [
        (1, 3, 90.30, 30.00, 30.20, 30.10, False),   # best lap
        (1, 5, 90.40, 29.90, 30.40, 30.10, False),   # best sector 1
        (1, 7, 90.50, 30.10, 30.30, 30.00, False),   # best sector 3
        (1, 8, 89.00, 29.00, 30.00, 30.00, True),    # deleted for track limits: ignored
        (1, 9, 95.00, 20.00, 30.00, 30.00, False),   # sectors do not add up to the lap: ignored
        (2, 4, 90.60, 30.10, 30.20, 30.30, False),
        (2, 6, None, 30.05, None, None, None),       # no lap time: its sector 1 still counts
        (3, 2, 91.00, 30.30, 30.40, 30.30, None),
        (3, 4, None, 20.00, None, None, None),       # sector 1 of 20 s: a timing glitch, ignored
    ]


def test_best_sectors_come_from_any_valid_lap():
    best = best_sectors(_laps())
    assert best[1] == [(29.90, 5), (30.20, 3), (30.00, 7)]
    assert best[2][0] == (30.05, 6)
    assert best[3][0] == (30.30, 2)
    assert best_laps(_laps()) == {1: 90.30, 2: 90.60, 3: 91.00}


def test_ideal_lap_time_left_on_the_table_and_ranks():
    rows, facts = ideal_table(best_sectors(_laps()), best_laps(_laps()))
    by = {r["driver_id"]: r for r in rows}
    assert by[1]["ideal_lap"] == pytest.approx(90.10) and by[1]["left_on_table"] == pytest.approx(0.20)
    assert by[2]["ideal_lap"] == pytest.approx(90.55) and by[2]["left_on_table"] == pytest.approx(0.05)
    assert by[3]["left_on_table"] == 0.0
    assert [r["driver_id"] for r in rows] == [1, 2, 3]                    # by best lap without official positions
    assert [by[d]["ideal_rank"] for d in (1, 2, 3)] == [1, 2, 3]
    # purple sectors: the session's fastest, and who set them
    assert [s["session_best"] for s in by[1]["sectors"]] == [True, True, True]
    assert facts["ultimate_lap"] == pytest.approx(90.10)
    assert [s["driver_id"] for s in facts["ultimate_sectors"]] == [1, 1, 1]


def test_official_positions_and_a_best_lap_without_sectors():
    sectors = {1: [(30.0, 1), (30.0, 1), (30.0, 1)], 2: [(30.1, 2), None, (30.1, 2)]}
    rows, facts = ideal_table(sectors, {1: 90.0, 2: 90.5, 3: 91.0}, positions={2: 1, 1: 2, 3: 3})
    assert [r["driver_id"] for r in rows] == [2, 1, 3]                    # official order
    by = {r["driver_id"]: r for r in rows}
    assert by[2]["ideal_lap"] is None and by[3]["ideal_lap"] is None      # a sector missing / no laps
    assert facts["ultimate_lap"] == pytest.approx(90.0)
    # sectors that add up to more than the best lap: that lap's sectors are missing -> no ideal
    rows, _ = ideal_table({1: [(31.0, 1), (31.0, 1), (31.0, 1)]}, {1: 90.0})
    assert rows[0]["ideal_lap"] is None and rows[0]["left_on_table"] is None
    # a thousandth over (sector rounding) is the best lap itself
    rows, _ = ideal_table({1: [(30.067, 1), (30.067, 1), (30.067, 1)]}, {1: 90.2})
    assert rows[0]["ideal_lap"] == 90.2 and rows[0]["left_on_table"] == 0.0


def test_ideal_lap_endpoint(api_client):
    body = api_client.get("/races/1/ideal-lap").json()
    assert body["session"] == "FP1" and body["sessions_available"] == ["FP1"]   # Q has no lap timing here
    assert body["deleted_known"] is True
    by = {d["driver_name"]: d for d in body["drivers"]}
    assert by["Driver B"]["best_lap"] == pytest.approx(90.4)            # his 89.9 was deleted
    assert by["Driver A"]["ideal_lap"] == pytest.approx(90.2) and by["Driver A"]["left_on_table"] == 0.0
    assert [d["driver_name"] for d in body["drivers"]][:2] == ["Driver A", "Driver R"]
    assert body["ultimate_lap"] == pytest.approx(90.2) and body["pole_lap"] == pytest.approx(90.2)
    assert body["ultimate_sectors"][0]["driver_name"] == "Driver A"

    assert api_client.get("/races/1/ideal-lap?session=Q").json()["drivers"] == []
    assert api_client.get("/races/1/ideal-lap?session=R").status_code == 422
    assert api_client.get("/races/999/ideal-lap").status_code == 404
