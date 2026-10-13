"""Overtakes and battles of a race, from the lap-by-lap running order and timing.

Read-only, and separate from every prediction model.

Overtake: car A behind car B at the end of lap n-1 and ahead of it at the end of lap n. Not
counted, because they are not passes on track:
  - lap 1 (the start: counted separately as places gained on lap 1, from the grid)
  - a lap under Safety Car, Virtual Safety Car or red flag
  - either car on an in-lap or out-lap (a pit stop swaps places, not a pass)
  - passing a car that retires on that lap or the next (a car in trouble)
The running order is the official position at the end of each lap; where a lap has none, the
order the cars crossed the line.

Battle: two cars next to each other in the order, the one behind within 1.0 s (DRS range),
lap after lap, outside neutralised laps and pit laps. Its outcome is who came out ahead (the
lap the car behind at the start got past for good, if it did) and how often the order changed.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from sqlalchemy import text

DRS_GAP = 1.0
TOP_BATTLES = 8


def running_order(positions: dict[int, dict[int, int]], ends: dict[int, dict[int, float]]) -> dict[int, list[int]]:
    """lap -> drivers front to back: by official position when every car on the lap has one,
    else by when they crossed the line."""
    laps = sorted({n for by in (positions, ends) for d in by for n in by[d]})
    out = {}
    for n in laps:
        cars = sorted({d for by in (positions, ends) for d in by if n in by[d]})
        if all(n in positions.get(d, {}) for d in cars):
            out[n] = sorted(cars, key=lambda d: positions[d][n])
        elif all(n in ends.get(d, {}) for d in cars):
            out[n] = sorted(cars, key=lambda d: ends[d][n])
        else:
            out[n] = sorted(cars, key=lambda d: (positions.get(d, {}).get(n, 99), ends.get(d, {}).get(n, float("inf"))))
    return out


def find_overtakes(order: dict[int, list[int]], pit_laps: dict[int, set[int]], neutral: set[int],
                   last_lap: dict[int, int], classified: set[int]) -> list[dict]:
    """[{lap, by, on, for_position}] for every pass on track (see the module rules)."""
    out = []
    for n in sorted(order):
        if n < 2 or n in neutral or n - 1 not in order:
            continue
        before = {d: i for i, d in enumerate(order[n - 1])}
        after = {d: i for i, d in enumerate(order[n])}
        both = [d for d in order[n] if d in before]

        def pitting(d):
            return n in pit_laps.get(d, ()) or (n - 1) in pit_laps.get(d, ())

        for a in both:
            for b in both:
                if before[a] > before[b] and after[a] < after[b]:
                    if pitting(a) or pitting(b):
                        continue
                    if b not in classified and last_lap.get(b, n) <= n + 1:
                        continue                          # a car about to retire
                    out.append({"lap": n, "by": a, "on": b, "for_position": after[a] + 1})
    out.sort(key=lambda o: (o["lap"], o["for_position"]))
    return out


def find_battles(order: dict[int, list[int]], ends: dict[int, dict[int, float]], pit_laps: dict[int, set[int]],
                 neutral: set[int]) -> list[dict]:
    """Runs of consecutive laps where two cars ran nose to tail within DRS_GAP."""
    close: dict[frozenset, list[tuple[int, int, int, float]]] = defaultdict(list)   # pair -> (lap, ahead, behind, gap)
    for n, cars in order.items():
        if n < 2 or n in neutral:
            continue
        for ahead, behind in zip(cars, cars[1:]):
            ea, eb = ends.get(ahead, {}).get(n), ends.get(behind, {}).get(n)
            if ea is None or eb is None:
                continue
            if any(n in pit_laps.get(d, ()) or (n - 1) in pit_laps.get(d, ()) for d in (ahead, behind)):
                continue
            gap = eb - ea
            if 0 <= gap <= DRS_GAP:
                close[frozenset((ahead, behind))].append((n, ahead, behind, gap))
    battles = []
    for pair, laps in close.items():
        laps.sort()
        run = [laps[0]]
        for item in laps[1:] + [None]:
            if item is not None and item[0] == run[-1][0] + 1:
                run.append(item)
                continue
            if len(run) >= 2:
                attacker, defender = run[0][2], run[0][1]
                # who was ahead on each lap of the fight, and on the lap after it if they were still both running
                ahead = [a for _, a, _, _ in run]
                end = run[-1][0]
                nxt = order.get(end + 1, [])
                if attacker in nxt and defender in nxt:
                    ahead.append(attacker if nxt.index(attacker) < nxt.index(defender) else defender)
                laps_seen = [r[0] for r in run] + ([end + 1] if len(ahead) > len(run) else [])
                changes = [laps_seen[i] for i in range(1, len(ahead)) if ahead[i] != ahead[i - 1]]
                battles.append({"attacker": attacker, "defender": defender, "from_lap": run[0][0], "to_lap": end,
                                "laps": len(run), "closest": round(min(r[3] for r in run), 3),
                                "changes": len(changes),
                                "passed_on_lap": changes[-1] if ahead[-1] == attacker else None})
            if item is not None:
                run = [item]
    battles.sort(key=lambda b: (-b["laps"], b["closest"]))
    return battles[:TOP_BATTLES]


def driver_table(order: dict[int, list[int]], overtakes: Iterable[dict], grid: dict[int, int | None]) -> dict[int, dict]:
    made, lost = defaultdict(int), defaultdict(int)
    for o in overtakes:
        made[o["by"]] += 1
        lost[o["on"]] += 1
    lap1 = {d: i + 1 for i, d in enumerate(order.get(1, []))}
    out = {}
    for d in {d for cars in order.values() for d in cars}:
        g = grid.get(d)
        start = (g if g else len(lap1)) - lap1[d] if d in lap1 else None   # pit-lane starters (grid 0) start last
        out[d] = {"made": made[d], "lost": lost[d], "start_gain": start}
    return out


def load_overtakes(db: Any, race_id: int) -> dict | None:
    from race_neutralisations_v1 import load_race_neutralisations
    from race_status import is_classified
    from race_trace_v1 import lap_end_times, pit_laps

    race = db.execute(text("""
        SELECT r.id, (SELECT s.id FROM sessions s WHERE s.race_id = r.id AND s.session_type = 'R' LIMIT 1) AS session_id
        FROM races r WHERE r.id = :r
    """), {"r": race_id}).mappings().first()
    if race is None:
        return None
    body = {"race_id": race_id, "total_overtakes": 0, "per_lap": [], "overtakes": [], "battles": [], "drivers": []}
    if race["session_id"] is None:
        return {**body, "note": "the race has not been run or loaded yet"}
    rows = db.execute(text("""
        SELECT re.driver_id, l.lap_number, l.position, l.lap_time, l.lap_start_time_seconds, l.sector_3_session_time_seconds
        FROM laps l JOIN race_entries re ON re.id = l.race_entry_id WHERE l.session_id = :s
    """), {"s": race["session_id"]}).all()
    positions: dict[int, dict[int, int]] = defaultdict(dict)
    for d, n, p, *_ in rows:
        if p is not None:
            positions[int(d)][int(n)] = int(p)
    ends = lap_end_times((d, n, lt, st, s3) for d, n, _, lt, st, s3 in rows)
    order = running_order(positions, ends)
    if not order:
        return {**body, "note": "no lap-by-lap running order stored for this race"}
    stints = pit_laps(db.execute(text("""
        SELECT re.driver_id, rs.stint_number, rs.end_lap, rs.compound
        FROM race_stints rs JOIN race_entries re ON re.id = rs.race_entry_id WHERE rs.race_id = :r
    """), {"r": race_id}).all())
    pits = {d: {p["lap"] for p in v} for d, v in stints.items()}
    neutralisations = load_race_neutralisations(db, race_id)
    neutral = set(neutralisations.neutralised_laps(("SC", "VSC", "RED_FLAG")))
    results = {int(r["driver_id"]): r for r in db.execute(text("""
        SELECT re.driver_id, d.name, tm.name AS team_name, tm.color_hex, rr.starting_grid_position AS grid,
               rr.finishing_position AS finish, rr.status
        FROM race_entries re JOIN drivers d ON d.id = re.driver_id LEFT JOIN teams tm ON tm.id = re.team_id
        LEFT JOIN race_results rr ON rr.race_entry_id = re.id AND rr.session_id = :s
        WHERE re.race_id = :r
    """), {"r": race_id, "s": race["session_id"]}).mappings()}
    classified = {d for d, r in results.items() if is_classified(r["status"])}
    last_lap = {d: max(n for n, cars in order.items() if d in cars) for d in {d for c in order.values() for d in c}}
    overtakes = find_overtakes(order, pits, neutral, last_lap, classified)
    battles = find_battles(order, ends, pits, neutral)
    table = driver_table(order, overtakes, {d: r["grid"] for d, r in results.items()})
    total = max(order)
    per_lap = [0] * total
    for o in overtakes:
        per_lap[o["lap"] - 1] += 1
    drivers = []
    for d, stats in table.items():
        r = results.get(d, {})
        drivers.append({"driver_id": d, "driver_name": r.get("name"), "team_name": r.get("team_name"),
                        "color_hex": r.get("color_hex"), "grid": r.get("grid"), "finish": r.get("finish"),
                        "classified": d in classified, **stats})
    drivers.sort(key=lambda x: (x["finish"] is None, x["finish"] or 99))
    return {**body, "total_laps": total, "total_overtakes": len(overtakes), "per_lap": per_lap,
            "overtakes": overtakes, "battles": battles, "drivers": drivers,
            "neutralised": [{"kind": e.event_type, "start_lap": e.start_lap, "end_lap": e.end_lap}
                            for e in neutralisations.events if e.start_lap is not None and e.end_lap is not None],
            "neutralised_known": neutralisations.coverage == "ok",
            "note": "Passes on track between the end of one lap and the next: pit-stop swaps, Safety Car / VSC / "
                    "red-flag laps, cars about to retire and the start are not counted."}
