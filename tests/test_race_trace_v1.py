import pytest
from sqlalchemy import create_engine, text

from race_trace_v1 import build_trace, lap_end_times, pit_laps


def test_lap_end_times_prefer_the_sector_3_line_then_the_next_lap_start():
    rows = [
        (1, 1, 95.0, 1000.0, 1095.2),     # sector 3 time wins
        (1, 2, 94.0, 1095.2, None),       # next lap's start
        (1, 3, 94.5, 1189.0, None),       # last lap: start + lap time
        (1, 4, None, None, None),         # nothing to go on: missing
        (2, 1, float("nan"), None, None),  # NaN is missing too
    ]
    ends = lap_end_times(rows)
    assert ends[1] == {1: 1095.2, 2: 1189.0, 3: pytest.approx(1283.5)}
    assert ends[2] == {}


def test_gap_to_leader_and_trace_against_the_winners_average_pace():
    ends = {1: {1: 100.0, 2: 190.0, 3: 280.0},          # the winner: 90 s laps after lap 1
            2: {1: 101.0, 2: 193.0, 3: 284.0},
            3: {1: 102.0}}                               # retired after lap 1
    series, ref = build_trace(ends)
    assert ref == {"total_laps": 3, "reference_lap": 90.0}
    assert series[1] == {"gap": [0.0, 0.0, 0.0], "trace": [0.0, 0.0, 0.0]}
    assert series[2] == {"gap": [1.0, 3.0, 4.0], "trace": [-1.0, -3.0, -4.0]}
    assert series[3] == {"gap": [2.0], "trace": [-2.0]}           # the line ends where the car stopped
    assert build_trace({}) == ({}, {"total_laps": 0, "reference_lap": None})


def test_pit_laps_are_the_end_of_each_stint_but_the_last():
    stints = [(7, 2, 40, "HARD"), (7, 1, 18, "MEDIUM"), (7, 3, 57, "SOFT"), (8, 1, 57, "HARD")]
    assert pit_laps(stints) == {7: [{"lap": 18, "compound": "HARD"}, {"lap": 40, "compound": "SOFT"}], 8: []}


def test_race_trace_endpoint(api_client, fixture_db_url):
    engine = create_engine(fixture_db_url)
    session = """(SELECT id FROM sessions WHERE race_id = 1 AND session_type = 'R')"""
    with engine.begin() as db:
        # lap starts on the session clock: everyone starts lap 1 at 1000 s, then adds lap times
        db.execute(text(f"""
            UPDATE laps l SET lap_start_time_seconds = 1000 + COALESCE((
                SELECT SUM(p.lap_time) FROM laps p
                WHERE p.session_id = l.session_id AND p.race_entry_id = l.race_entry_id AND p.lap_number < l.lap_number), 0)
            WHERE l.session_id = {session}"""))
        db.execute(text("""
            INSERT INTO race_stints (race_id, race_entry_id, finishing_position, stint_number, compound, start_lap, end_lap, stint_length)
            SELECT 1, re.id, 1, s.n, s.c, s.a, s.b, s.b - s.a + 1
            FROM race_entries re, (VALUES (1, 'SOFT', 1, 2), (2, 'HARD', 3, 3)) AS s(n, c, a, b)
            WHERE re.race_id = 1 AND re.driver_id = 1"""))
    try:
        resp = api_client.get("/races/1/race-trace")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total_laps"] == 3 and body["reference_lap"] == pytest.approx(94.65)
        assert body["neutralised"] == [] and body["neutralised_known"] is False
        drivers = {d["driver_name"]: d for d in body["drivers"]}
        assert [d["driver_name"] for d in body["drivers"]] == ["Driver A", "Driver B", "Driver D", "Driver C"]
        assert drivers["Driver A"]["gap"] == [0.0, 0.0, 0.0]
        assert drivers["Driver B"]["gap"] == pytest.approx([0.1, 0.5, 0.7])
        assert drivers["Driver B"]["trace"] == pytest.approx([-0.1, -0.35, -0.7])
        assert drivers["Driver D"]["gap"] == pytest.approx([1.0, 2.3])                 # one lap down: two laps
        assert drivers["Driver C"]["gap"] == pytest.approx([0.5]) and drivers["Driver C"]["classified"] is False
        assert drivers["Driver A"]["pits"] == [{"lap": 2, "compound": "HARD"}]
        assert drivers["Driver A"]["color_hex"] == "#111111" and drivers["Driver A"]["finish"] == 1

        # a race without lap-end timing, and one that does not exist
        no_timing = api_client.get("/races/2/race-trace").json()
        assert no_timing["drivers"] == [] and "no lap-end timing" in no_timing["note"]
        assert api_client.get("/races/5/race-trace").json()["drivers"] == []          # not run yet
        assert api_client.get("/races/999/race-trace").status_code == 404
    finally:
        with engine.begin() as db:
            db.execute(text(f"UPDATE laps SET lap_start_time_seconds = NULL WHERE session_id = {session}"))
            db.execute(text("DELETE FROM race_stints WHERE race_id = 1"))
        engine.dispose()
