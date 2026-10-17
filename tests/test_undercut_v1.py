import pytest
from sqlalchemy import create_engine, text

from undercut_v1 import find_duels


def _ends(times):
    """driver -> lap -> end time from per-lap lap times."""
    out = {}
    for d, laps in times.items():
        t, out[d] = 0.0, {}
        for n, lt in enumerate(laps, 1):
            t += lt
            out[d][n] = round(t, 3)
    return out


def test_undercut_that_works_and_one_that_fails():
    # car 2 runs 1.0 s behind car 1; car 2 pits on lap 3, car 1 on lap 5. Fresh tyres: 2 s a lap faster.
    base = [90.0] * 8
    one = base[:]
    two = [91.0] + base[1:]
    two[2] += 20; two[3] += 2 - 2; two[3] -= 2; two[4] -= 2      # in-lap +20 (pit), then 2 s faster
    one[4] += 20                                                  # car 1's in-lap is lap 5
    one[5] -= 2                                                   # its out-lap on fresh tyres
    ends = _ends({1: one, 2: two})
    duels, skipped = find_duels(ends, {1: [{"lap": 5, "compound": "HARD"}], 2: [{"lap": 3, "compound": "HARD"}]}, set())
    assert skipped == 0 and len(duels) == 1
    d = duels[0]
    assert (d["first"], d["second"], d["first_lap"], d["second_lap"], d["kind"]) == (2, 1, 3, 5, "undercut")
    assert d["gap_before"] == pytest.approx(1.0) and d["gap_after"] == pytest.approx(-1.0) and d["worked"] is True
    assert d["gain"] == pytest.approx(2.0) and d["second_compound"] == "HARD"

    # same, but car 2 gains nothing on fresh tyres: still behind
    slow = [91.0] + base[1:]
    slow[2] += 20
    duels, _ = find_duels(_ends({1: one, 2: slow}), {1: [{"lap": 5}], 2: [{"lap": 3}]}, set())
    assert duels[0]["kind"] == "undercut" and duels[0]["worked"] is False


def test_overcut_cover_and_the_rules():
    base = [90.0] * 8
    one, two = base[:], [90.8] + base[1:]                       # car 2 behind by 0.8 s
    one[2] += 20                                                  # the car ahead pits first (lap 3)
    two[5] += 20                                                  # car 2 stays out to lap 6
    stops = {1: [{"lap": 3}], 2: [{"lap": 6}]}
    d = find_duels(_ends({1: one, 2: two}), stops, set())[0][0]
    assert (d["first"], d["kind"]) == (1, "overcut") and d["worked"] is False      # covered: car 1 stayed ahead
    # a stop on a VSC lap: skipped and counted
    duels, skipped = find_duels(_ends({1: one, 2: two}), stops, neutral={6})
    assert duels == [] and skipped == 1
    # too far apart (5 s), outside the window, pitted together: no duel
    far = [95.0] + base[1:]
    far[5] += 20
    assert find_duels(_ends({1: one, 2: far}), stops, set())[0] == []
    assert find_duels(_ends({1: one, 2: two}), {1: [{"lap": 3}], 2: [{"lap": 3}]}, set())[0] == []
    assert find_duels(_ends({1: one, 2: two}), {1: [{"lap": 1}], 2: [{"lap": 8}]}, set())[0] == []


def test_undercuts_endpoint(api_client, fixture_db_url):
    engine = create_engine(fixture_db_url)
    session = "(SELECT id FROM sessions WHERE race_id = 1 AND session_type = 'R')"
    with engine.begin() as db:
        db.execute(text(f"""
            UPDATE laps l SET lap_start_time_seconds = 1000 + COALESCE((
                SELECT SUM(p.lap_time) FROM laps p
                WHERE p.session_id = l.session_id AND p.race_entry_id = l.race_entry_id AND p.lap_number < l.lap_number), 0)
            WHERE l.session_id = {session}"""))
    try:
        body = api_client.get("/races/1/undercuts").json()
        assert body["duels"] == [] and body["summary"]["undercut"] == {"tried": 0, "worked": 0}   # no stints stored
        assert body["reach"] == 3.5 and body["window"] == 6
        assert api_client.get("/races/999/undercuts").status_code == 404
    finally:
        with engine.begin() as db:
            db.execute(text(f"UPDATE laps SET lap_start_time_seconds = NULL WHERE session_id = {session}"))
        engine.dispose()
