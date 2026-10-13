"""Race trace: every car's gap to the leader on every lap, from the official timing.

Read-only, and separate from every prediction model: it only reads stored laps, stints,
results and Safety Car windows.

When a car finished each lap is the session time at the end of that lap, taken from the
stored FastF1 timing, best source first:
  1. sector_3_session_time_seconds     the timing line at the end of sector 3
  2. the car's start of the next lap   lap_start_time_seconds of lap n+1
  3. its start of the lap + lap time

Two views of the same times:
  gap    seconds behind the leader at the end of lap n (the first car to complete lap n);
         a lapped car is compared on its own lap count, as the timing screens do
  trace  seconds ahead (+) or behind (-) a car lapping at the winner's average pace from the
         end of lap 1 to the flag: the classic race trace, where a pit stop is a drop, a
         Safety Car a bunching, and a faster car's line climbs
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from sqlalchemy import text

LapRow = tuple[int, int, float | None, float | None, float | None]   # driver, lap, lap_time, start, s3 time


def lap_end_times(rows: Iterable[LapRow]) -> dict[int, dict[int, float]]:
    """driver -> lap -> session time (s) at the end of that lap."""
    starts: dict[int, dict[int, float]] = defaultdict(dict)
    laps: dict[int, dict[int, tuple[float | None, float | None, float | None]]] = defaultdict(dict)
    for driver, lap, lap_time, start, s3 in rows:
        d, n = int(driver), int(lap)
        laps[d][n] = (_f(lap_time), _f(start), _f(s3))
        if start is not None:
            starts[d][n] = float(start)
    ends: dict[int, dict[int, float]] = {}
    for d, by_lap in laps.items():
        out = {}
        for n, (lap_time, start, s3) in by_lap.items():
            if s3 is not None:
                out[n] = s3
            elif n + 1 in starts[d]:
                out[n] = starts[d][n + 1]
            elif start is not None and lap_time is not None:
                out[n] = start + lap_time
        ends[d] = out
    return ends


def _f(value) -> float | None:
    if value is None:
        return None
    v = float(value)
    return None if v != v else v                 # NaN -> missing


def build_trace(ends: dict[int, dict[int, float]]) -> tuple[dict[int, dict[str, list[float | None]]], dict]:
    """(driver -> {"gap": [...], "trace": [...]} indexed by lap - 1, reference facts)."""
    total = max((n for by_lap in ends.values() for n in by_lap), default=0)
    leader = {n: min(e[n] for e in ends.values() if n in e) for n in range(1, total + 1)
              if any(n in e for e in ends.values())}
    first = min(leader, default=None)
    if first is None or total not in leader or total - first < 1:
        return {}, {"total_laps": total, "reference_lap": None}
    pace = (leader[total] - leader[first]) / (total - first)   # the winner's average lap after lap 1
    out = {}
    for d, by_lap in ends.items():
        gap, trace = [], []
        for n in range(1, total + 1):
            t = by_lap.get(n)
            gap.append(round(t - leader[n], 3) if t is not None and n in leader else None)
            trace.append(round(leader[first] + (n - first) * pace - t, 3) if t is not None else None)
        while gap and gap[-1] is None:                      # a retired car's line just ends
            gap.pop()
            trace.pop()
        if any(g is not None for g in gap):
            out[d] = {"gap": gap, "trace": trace}
    return out, {"total_laps": total, "reference_lap": round(pace, 3)}


def pit_laps(stints: Iterable[tuple[int, int, int, str | None]]) -> dict[int, list[dict]]:
    """driver -> [{lap, compound}]: the lap each stint ended on (the in-lap) and the tyre fitted.
    stints: (driver, stint_number, end_lap, compound)."""
    by_driver: dict[int, list[tuple[int, int, str | None]]] = defaultdict(list)
    for driver, number, end_lap, compound in stints:
        by_driver[int(driver)].append((int(number), int(end_lap), compound))
    out = {}
    for d, rows in by_driver.items():
        rows.sort()
        out[d] = [{"lap": rows[i][1], "compound": rows[i + 1][2]} for i in range(len(rows) - 1)]
    return out


def load_race_trace(db: Any, race_id: int) -> dict | None:
    """The race trace of one race; None when the race does not exist."""
    from race_neutralisations_v1 import load_race_neutralisations
    from race_status import is_classified

    race = db.execute(text("""
        SELECT r.id, r.season_year, r.round_number, t.name AS track_name,
               (SELECT s.id FROM sessions s WHERE s.race_id = r.id AND s.session_type = 'R' LIMIT 1) AS session_id
        FROM races r JOIN tracks t ON t.id = r.track_id WHERE r.id = :r
    """), {"r": race_id}).mappings().first()
    if race is None:
        return None
    body = {"race_id": race_id, "season": race["season_year"], "round": race["round_number"],
            "track": race["track_name"], "total_laps": None, "reference_lap": None,
            "neutralised": [], "drivers": []}
    if race["session_id"] is None:
        return {**body, "note": "the race has not been run or loaded yet"}
    rows = db.execute(text("""
        SELECT re.driver_id, l.lap_number, l.lap_time, l.lap_start_time_seconds, l.sector_3_session_time_seconds
        FROM laps l JOIN race_entries re ON re.id = l.race_entry_id
        WHERE l.session_id = :s
    """), {"s": race["session_id"]}).all()
    series, ref = build_trace(lap_end_times(rows))
    if not series:
        return {**body, "note": "no lap-end timing stored for this race"}

    drivers = db.execute(text("""
        SELECT re.driver_id, d.name, tm.name AS team_name, tm.color_hex,
               rr.finishing_position, rr.starting_grid_position, rr.status
        FROM race_entries re
        JOIN drivers d ON d.id = re.driver_id
        LEFT JOIN teams tm ON tm.id = re.team_id
        LEFT JOIN race_results rr ON rr.race_entry_id = re.id AND rr.session_id = :s
        WHERE re.race_id = :r AND re.driver_id = ANY(:ids)
    """), {"r": race_id, "s": race["session_id"], "ids": list(series)}).mappings().all()
    pits = pit_laps(db.execute(text("""
        SELECT re.driver_id, rs.stint_number, rs.end_lap, rs.compound
        FROM race_stints rs JOIN race_entries re ON re.id = rs.race_entry_id
        WHERE rs.race_id = :r
    """), {"r": race_id}).all())
    out = []
    for d in drivers:
        did = int(d["driver_id"])
        out.append({"driver_id": did, "driver_name": d["name"], "team_name": d["team_name"], "color_hex": d["color_hex"],
                    "finish": d["finishing_position"], "grid": d["starting_grid_position"], "status": d["status"],
                    "classified": is_classified(d["status"]), "pits": pits.get(did, []), **series[did]})
    out.sort(key=lambda x: (x["finish"] is None, x["finish"] or 99, x["driver_name"] or ""))

    neutralised = load_race_neutralisations(db, race_id)
    events = [{"kind": e.event_type, "start_lap": e.start_lap, "end_lap": e.end_lap}
              for e in neutralised.events if e.start_lap is not None and e.end_lap is not None]
    return {**body, **ref, "neutralised": events, "neutralised_known": neutralised.coverage == "ok",
            "drivers": out,
            "note": "gap: seconds behind the first car to complete each lap; trace: seconds ahead (+) or behind (-) "
                    "a car lapping at the winner's average pace after lap 1. From F1 timing (FastF1)."}
