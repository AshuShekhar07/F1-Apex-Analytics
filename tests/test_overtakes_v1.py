from overtakes_v1 import driver_table, find_battles, find_overtakes, running_order


def test_running_order_uses_official_positions_else_line_crossings():
    positions = {1: {1: 2, 2: 1}, 2: {1: 1, 2: 2}}
    ends = {1: {1: 101.0, 2: 200.0, 3: 300.5}, 2: {1: 100.0, 2: 200.4, 3: 300.0}}
    order = running_order(positions, ends)
    assert order == {1: [2, 1], 2: [1, 2], 3: [2, 1]}             # lap 3 has no positions: by crossing


def _order():
    # A=1 B=2 C=3 D=4
    return {1: [2, 1, 3, 4], 2: [1, 2, 3, 4], 3: [1, 3, 2, 4], 4: [1, 3, 4, 2], 5: [1, 4, 3, 2], 6: [1, 4, 3]}


def test_overtakes_on_track_only():
    order = _order()
    passes = find_overtakes(order, pit_laps={}, neutral=set(), last_lap={1: 6, 2: 5, 3: 6, 4: 6}, classified={1, 3, 4})
    assert [(p["lap"], p["by"], p["on"], p["for_position"]) for p in passes] == [
        (2, 1, 2, 1),          # A passes B for the lead
        (3, 3, 2, 2),          # C passes B
        (5, 4, 3, 2),          # D passes C (B is about to retire on lap 5/6: D passing B on lap 4 not counted)
    ]
    # B pitted at the end of lap 3: C getting ahead on B's in-lap and D getting ahead on B's out-lap
    # (lap 4) are pit-stop swaps; lap 2 under a VSC: A taking the lead is not counted either
    passes = find_overtakes(order, pit_laps={2: {3}}, neutral={2}, last_lap={1: 6, 2: 6, 3: 6, 4: 6}, classified={1, 2, 3, 4})
    assert [(p["lap"], p["by"], p["on"]) for p in passes] == [(5, 4, 3)]


def test_battles_and_their_outcome():
    order = {n: [1, 2, 3] for n in range(1, 8)}
    order[6] = [2, 1, 3]                                           # 2 gets past 1 on lap 6
    order[7] = [2, 1, 3]
    ends = {1: {}, 2: {}, 3: {}}
    for n in range(1, 8):
        lead = 100.0 * n
        ends[order[n][0]][n] = lead
        ends[order[n][1]][n] = lead + 0.6                          # nose to tail all race
        ends[order[n][2]][n] = lead + 5.0                          # 3 is never close
    battles = find_battles(order, ends, pit_laps={}, neutral=set())
    assert len(battles) == 1
    b = battles[0]
    assert (b["attacker"], b["defender"], b["from_lap"], b["to_lap"], b["laps"]) == (2, 1, 2, 7, 6)
    assert b["passed_on_lap"] == 6 and b["closest"] == 0.6 and b["changes"] == 1
    # back and forth: past on lap 3, re-passed on lap 5 -> the defender came out ahead
    swing = {n: [1, 2, 3] for n in range(1, 8)}
    swing[3] = swing[4] = [2, 1, 3]
    ends2 = {1: {}, 2: {}, 3: {}}
    for n in range(1, 8):
        ends2[swing[n][0]][n], ends2[swing[n][1]][n], ends2[swing[n][2]][n] = 100.0 * n, 100.0 * n + 0.5, 100.0 * n + 9
    b = find_battles(swing, ends2, pit_laps={}, neutral=set())[0]
    assert (b["attacker"], b["changes"], b["passed_on_lap"]) == (2, 2, None)
    # a Safety Car on lap 4 splits it in two; the first part (laps 2-3) is held, the second passes
    battles = find_battles(order, ends, pit_laps={}, neutral={4})
    assert sorted((b["from_lap"], b["to_lap"], b["passed_on_lap"]) for b in battles) == [(2, 3, None), (5, 7, 6)]


def test_places_gained_at_the_start():
    order = {1: [3, 1, 2, 4]}
    table = driver_table(order, [{"by": 3, "on": 1}], grid={1: 1, 2: 2, 3: 4, 4: 0})
    assert table[3] == {"made": 1, "lost": 0, "start_gain": 3}     # P4 on the grid, P1 after lap 1
    assert table[1]["start_gain"] == -1 and table[1]["lost"] == 1
    assert table[4]["start_gain"] == 0                              # pit-lane start counts as last


def test_overtakes_endpoint(api_client):
    body = api_client.get("/races/1/overtakes").json()
    assert body["total_overtakes"] == 1 and body["per_lap"] == [0, 1, 0]
    assert body["overtakes"] == [{"lap": 2, "by": 1, "on": 2, "for_position": 1}]
    drivers = {d["driver_name"]: d for d in body["drivers"]}
    assert drivers["Driver A"]["made"] == 1 and drivers["Driver B"]["lost"] == 1
    assert drivers["Driver A"]["start_gain"] == 0 and drivers["Driver C"]["classified"] is False
    assert body["battles"] == []                                    # no lap timing in the fixture
    assert api_client.get("/races/5/overtakes").json()["total_overtakes"] == 0
    assert api_client.get("/races/999/overtakes").status_code == 404
