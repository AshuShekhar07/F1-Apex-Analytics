"""Undercut checker: pit-stop duels between cars running close together, and who came out ahead.

Read-only, and separate from every prediction model (the strategy model is not used).

A duel: car F pits (in-lap u) while car S is within REACH seconds of it at the end of lap u-1,
and S makes its own stop within WINDOW laps after F (in-lap s, u < s <= u + WINDOW). Both stops
under green: a stop on a Safety Car / VSC / red-flag lap is much cheaper, so those duels are
skipped (and counted). Order and gap are compared at the end of lap u-1 and at the end of
lap s+1, S's out-lap, when both stops are done.
  - F was behind: an undercut attempt (pit first, use the fresh tyres); it worked if F came out ahead
  - F was ahead: S tried the overcut (stay out); it worked if S came out ahead
gain: seconds F gained on S over the cycle (+ = F gained).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

REACH = 3.5
WINDOW = 6


def find_duels(ends: dict[int, dict[int, float]], stops: dict[int, list[dict]], neutral: set[int],
               order: dict[int, list[int]] | None = None) -> tuple[list[dict], int]:
    """(duels, how many were skipped for a stop under a Safety Car / VSC / red flag).
    stops: driver -> [{lap (in-lap), compound (fitted)}] in lap order."""
    duels, skipped = [], 0
    for f, f_stops in stops.items():
        for fs in f_stops:
            u = fs["lap"]
            before = u - 1
            tf = ends.get(f, {}).get(before)
            if tf is None:
                continue
            for s, s_stops in stops.items():
                if s == f:
                    continue
                ts = ends.get(s, {}).get(before)
                if ts is None or abs(tf - ts) > REACH:
                    continue
                nxt = next((x for x in s_stops if u <= x["lap"] <= u + WINDOW), None)
                if nxt is None or nxt["lap"] == u:          # no stop in the window, or pitted together
                    continue
                if any(u - WINDOW <= x["lap"] < u for x in s_stops):
                    continue                              # S had just stopped: not the same pit cycle
                if {u, nxt["lap"]} & neutral:
                    skipped += 1
                    continue
                after = nxt["lap"] + 1
                af, as_ = ends.get(f, {}).get(after), ends.get(s, {}).get(after)
                if af is None or as_ is None:
                    continue                              # one of them did not finish the cycle
                gap_before, gap_after = tf - ts, af - as_  # + = F behind S
                behind = gap_before > 0
                cars = (order or {}).get(before, [])
                position = min(cars.index(f), cars.index(s)) + 1 if f in cars and s in cars else None
                duels.append({
                    "first": f, "second": s, "first_lap": u, "second_lap": nxt["lap"],
                    "first_compound": fs.get("compound"), "second_compound": nxt.get("compound"),
                    "gap_before": round(gap_before, 3), "gap_after": round(gap_after, 3),
                    "gain": round(gap_before - gap_after, 3),
                    "kind": "undercut" if behind else "overcut",
                    "worked": (gap_after < 0) if behind else (gap_after > 0),
                    "for_position": position,
                })
    duels.sort(key=lambda d: (d["first_lap"], d["for_position"] or 99))
    return duels, skipped


def load_undercuts(db: Any, race_id: int) -> dict | None:
    from overtakes_v1 import running_order
    from race_neutralisations_v1 import load_race_neutralisations
    from race_trace_v1 import lap_end_times, pit_laps

    race = db.execute(text("""
        SELECT r.id, (SELECT s.id FROM sessions s WHERE s.race_id = r.id AND s.session_type = 'R' LIMIT 1) AS session_id
        FROM races r WHERE r.id = :r
    """), {"r": race_id}).mappings().first()
    if race is None:
        return None
    body = {"race_id": race_id, "reach": REACH, "window": WINDOW, "duels": [], "skipped_neutralised": 0,
            "summary": {"undercut": {"tried": 0, "worked": 0}, "overcut": {"tried": 0, "worked": 0}}, "drivers": []}
    if race["session_id"] is None:
        return {**body, "note": "the race has not been run or loaded yet"}
    rows = db.execute(text("""
        SELECT re.driver_id, l.lap_number, l.position, l.lap_time, l.lap_start_time_seconds, l.sector_3_session_time_seconds
        FROM laps l JOIN race_entries re ON re.id = l.race_entry_id WHERE l.session_id = :s
    """), {"s": race["session_id"]}).all()
    ends = lap_end_times((d, n, lt, st, s3) for d, n, _, lt, st, s3 in rows)
    positions: dict[int, dict[int, int]] = {}
    for d, n, p, *_ in rows:
        if p is not None:
            positions.setdefault(int(d), {})[int(n)] = int(p)
    stops = pit_laps(db.execute(text("""
        SELECT re.driver_id, rs.stint_number, rs.end_lap, rs.compound
        FROM race_stints rs JOIN race_entries re ON re.id = rs.race_entry_id WHERE rs.race_id = :r
    """), {"r": race_id}).all())
    neutralisations = load_race_neutralisations(db, race_id)
    neutral = set(neutralisations.neutralised_laps(("SC", "VSC", "RED_FLAG")))
    duels, skipped = find_duels(ends, stops, neutral, running_order(positions, ends))
    summary = {k: {"tried": sum(d["kind"] == k for d in duels), "worked": sum(d["kind"] == k and d["worked"] for d in duels)}
               for k in ("undercut", "overcut")}
    ids = sorted({d["first"] for d in duels} | {d["second"] for d in duels})
    drivers = [dict(r) for r in db.execute(text("""
        SELECT re.driver_id, d.name AS driver_name, tm.name AS team_name, tm.color_hex
        FROM race_entries re JOIN drivers d ON d.id = re.driver_id LEFT JOIN teams tm ON tm.id = re.team_id
        WHERE re.race_id = :r AND re.driver_id = ANY(:ids)
    """), {"r": race_id, "ids": ids}).mappings()] if ids else []
    return {**body, "duels": duels, "skipped_neutralised": skipped, "summary": summary, "drivers": drivers,
            "neutralised_known": neutralisations.coverage == "ok",
            "note": f"Cars within {REACH} s when the first of them pitted, the other pitting within {WINDOW} laps; "
                    "order and gap compared before the first stop and after the second car's out-lap. "
                    "Stops under a Safety Car, VSC or red flag are left out."}
