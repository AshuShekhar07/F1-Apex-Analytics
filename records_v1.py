"""Records and streaks over the Grands Prix in the database (2018 onwards).

Read-only, and separate from every prediction model.

  pole          fastest in qualifying (qualifying P1; grid penalties do not move it)
  fastest lap   the quickest race lap in the timing
  hat-trick     pole, win and fastest lap in the same Grand Prix
  Grand Chelem  a hat-trick while leading every lap (official position 1 at the end of every
                lap, else first across the line on every lap); a race without that timing
                cannot be checked and is listed as a hat-trick only
  streaks       consecutive Grands Prix a driver took part in (sprints not counted): wins,
                podiums, points finishes, classified finishes, poles. A streak is active when it
                runs to the latest Grand Prix and the driver was in it.
  pole to win   per circuit: how often qualifying P1 won the race; and the lowest grid slots
                a race was won from (a pit-lane start counts as the back of the grid)
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable, Sequence

from sqlalchemy import text

STREAKS: dict[str, Callable[[dict], bool]] = {
    "wins": lambda r: r["finish"] == 1,
    "podiums": lambda r: r["finish"] is not None and r["finish"] <= 3 and r["classified"],
    "points": lambda r: (r["points"] or 0) > 0,
    "finishes": lambda r: r["classified"],
    "poles": lambda r: r["quali"] == 1,
}
TOP = 10


def streak_runs(flags: Sequence[bool]) -> list[tuple[int, int]]:
    """(start index, end index) of every run of True, in order."""
    runs, start = [], None
    for i, f in enumerate(flags):
        if f and start is None:
            start = i
        if not f and start is not None:
            runs.append((start, i - 1))
            start = None
    if start is not None:
        runs.append((start, len(flags) - 1))
    return runs


def streak_tables(by_driver: dict[int, list[dict]], latest_race: int | None) -> dict[str, dict[str, list[dict]]]:
    """kind -> {"longest": [...], "active": [...]}: each driver's best run of each kind, and the
    runs still going (ending at the latest Grand Prix, which the driver took part in)."""
    out = {}
    for kind, test in STREAKS.items():
        longest, active = [], []
        for d, races in by_driver.items():
            runs = streak_runs([test(r) for r in races])
            if not runs:
                continue
            best = max(runs, key=lambda se: (se[1] - se[0], se[1]))     # the longest; on ties the latest
            longest.append(_streak(d, races, best))
            last = runs[-1]
            if latest_race is not None and races[last[1]]["race_id"] == latest_race and last[1] == len(races) - 1:
                active.append(_streak(d, races, last))
        longest.sort(key=lambda s: (-s["length"], s["to"]["race_date"]))
        active.sort(key=lambda s: -s["length"])
        out[kind] = {"longest": longest[:TOP], "active": active[:TOP]}
    return out


def _streak(driver: int, races: list[dict], run: tuple[int, int]) -> dict:
    first, last = races[run[0]], races[run[1]]
    keep = ("race_id", "season", "round", "track", "race_date")
    return {"driver_id": driver, "driver_name": last["driver_name"], "team_name": last["team_name"],
            "color_hex": last["color_hex"], "length": run[1] - run[0] + 1,
            "from": {k: first[k] for k in keep}, "to": {k: last[k] for k in keep}}


def led_every_lap(order: dict[int, list[int]], driver: int) -> bool | None:
    """True/False from the running order; None when it cannot be checked (no order, or a lap
    the driver has no position or crossing time for)."""
    if not order or any(driver not in cars for cars in order.values()):
        return None
    return all(cars[0] == driver for cars in order.values())


_CACHE: dict[tuple, dict] = {}


def load_records(db: Any) -> dict:
    fingerprint = tuple(db.execute(text("""
        SELECT (SELECT COUNT(*) FROM race_results), (SELECT COUNT(*) FROM qualifying_results),
               (SELECT COUNT(*) FROM laps)""")).one())
    if fingerprint not in _CACHE:
        _CACHE.clear()
        _CACHE[fingerprint] = _compute(db)
    return _CACHE[fingerprint]


def _compute(db: Any) -> dict:
    from race_status import is_classified

    rows = [dict(r) for r in db.execute(text("""
        SELECT r.id AS race_id, r.season_year AS season, r.round_number AS round, r.race_date, t.name AS track,
               t.id AS track_id, re.driver_id, d.name AS driver_name, tm.name AS team_name, tm.color_hex,
               rr.finishing_position AS finish, rr.status, NULLIF(rr.points, 'NaN') AS points,
               rr.starting_grid_position AS grid, q.final_position AS quali
        FROM race_results rr
        JOIN sessions s ON s.id = rr.session_id AND s.session_type = 'R'
        JOIN races r ON r.id = s.race_id
        JOIN tracks t ON t.id = r.track_id
        JOIN race_entries re ON re.id = rr.race_entry_id
        JOIN drivers d ON d.id = re.driver_id
        LEFT JOIN teams tm ON tm.id = re.team_id
        LEFT JOIN sessions sq ON sq.race_id = r.id AND sq.session_type = 'Q'
        LEFT JOIN qualifying_results q ON q.session_id = sq.id AND q.race_entry_id = re.id
        ORDER BY r.race_date, r.id
    """)).mappings()]
    for r in rows:
        r["race_date"] = str(r["race_date"])
        r["classified"] = is_classified(r["status"])
        r["points"] = float(r["points"]) if r["points"] is not None else None
    if not rows:
        return {"races": 0, "first_season": None, "last_season": None, "streaks": {}, "hat_tricks": [],
                "leaders": {}, "pole_to_win": [], "furthest_back_wins": []}
    races = {r["race_id"]: r for r in rows}                       # one row per race for race facts
    latest_race = rows[-1]["race_id"]
    fastest = {int(race): int(driver) for race, driver in db.execute(text("""
        SELECT DISTINCT ON (s.race_id) s.race_id, re.driver_id
        FROM laps l JOIN sessions s ON s.id = l.session_id AND s.session_type = 'R'
        JOIN race_entries re ON re.id = l.race_entry_id
        WHERE l.lap_time IS NOT NULL AND l.lap_time > 0
        ORDER BY s.race_id, l.lap_time
    """))}

    by_driver: dict[int, list[dict]] = defaultdict(list)
    winners, poles = {}, {}
    for r in rows:
        by_driver[r["driver_id"]].append(r)
        if r["finish"] == 1:
            winners[r["race_id"]] = r
        if r["quali"] == 1:
            poles[r["race_id"]] = r["driver_id"]

    hat_tricks = []
    for race_id, w in winners.items():
        d = w["driver_id"]
        if poles.get(race_id) == d and fastest.get(race_id) == d:
            hat_tricks.append({**{k: w[k] for k in ("race_id", "season", "round", "track", "race_date", "driver_id",
                                                     "driver_name", "team_name", "color_hex")},
                               "grand_chelem": _led_every_lap(db, race_id, d)})
    hat_tricks.sort(key=lambda h: h["race_date"], reverse=True)

    def leaders(count: Callable[[dict], bool], extra: dict[int, int] | None = None) -> list[dict]:
        totals: dict[int, int] = defaultdict(int)
        for r in rows:
            totals[r["driver_id"]] += int(count(r))
        for d, n in (extra or {}).items():
            totals[d] += n
        last = {r["driver_id"]: r for r in rows}
        top = sorted((d for d in totals if totals[d] > 0), key=lambda d: -totals[d])[:5]
        return [{"driver_id": d, "driver_name": last[d]["driver_name"], "team_name": last[d]["team_name"],
                 "color_hex": last[d]["color_hex"], "count": totals[d]} for d in top]

    fastest_counts: dict[int, int] = defaultdict(int)
    for d in fastest.values():
        fastest_counts[d] += 1
    leader_tables = {
        "wins": leaders(STREAKS["wins"]),
        "poles": leaders(STREAKS["poles"]),
        "podiums": leaders(STREAKS["podiums"]),
        "fastest_laps": leaders(lambda r: False, fastest_counts),
        "hat_tricks": leaders(lambda r: False, _count(h["driver_id"] for h in hat_tricks)),
    }

    by_track: dict[int, dict] = {}
    for race_id, w in winners.items():
        if race_id not in poles:
            continue
        t = by_track.setdefault(w["track_id"], {"track_id": w["track_id"], "track": w["track"], "races": 0, "pole_won": 0})
        t["races"] += 1
        t["pole_won"] += int(poles[race_id] == w["driver_id"])
    pole_to_win = sorted(by_track.values(), key=lambda t: (-t["pole_won"] / t["races"], -t["races"], t["track"]))
    field = defaultdict(int)
    for r in rows:
        field[r["race_id"]] += 1
    back = []
    for race_id, w in winners.items():
        grid = w["grid"] if w["grid"] else field[race_id]        # pit-lane start: the back of the grid
        back.append({k: w[k] for k in ("race_id", "season", "round", "track", "driver_id", "driver_name",
                                       "team_name", "color_hex")} | {"grid": grid, "pit_lane": not w["grid"]})
    back.sort(key=lambda b: (-b["grid"], b["season"]))

    seasons = sorted({r["season"] for r in rows})
    return {
        "races": len(races), "first_season": seasons[0], "last_season": seasons[-1],
        "latest_race": {k: races[latest_race][k] for k in ("race_id", "season", "round", "track", "race_date")},
        "streaks": streak_tables(by_driver, latest_race),
        "hat_tricks": hat_tricks,
        "leaders": leader_tables,
        "pole_to_win": pole_to_win,
        "pole_to_win_overall": {"races": sum(t["races"] for t in pole_to_win),
                                "pole_won": sum(t["pole_won"] for t in pole_to_win)},
        "furthest_back_wins": back[:8],
        "note": f"Grands Prix only (sprints not counted), {seasons[0]}-{seasons[-1]}: the seasons in the database. "
                "Pole = fastest in qualifying; fastest lap = quickest race lap in the timing.",
    }


def _count(items) -> dict[int, int]:
    out: dict[int, int] = defaultdict(int)
    for i in items:
        out[i] += 1
    return out


def _led_every_lap(db: Any, race_id: int, driver: int) -> bool | None:
    from overtakes_v1 import running_order
    from race_trace_v1 import lap_end_times

    session_id = db.execute(text("SELECT id FROM sessions WHERE race_id = :r AND session_type = 'R'"), {"r": race_id}).scalar()
    rows = db.execute(text("""
        SELECT re.driver_id, l.lap_number, l.position, l.lap_time, l.lap_start_time_seconds, l.sector_3_session_time_seconds
        FROM laps l JOIN race_entries re ON re.id = l.race_entry_id WHERE l.session_id = :s
    """), {"s": session_id}).all()
    positions: dict[int, dict[int, int]] = defaultdict(dict)
    for d, n, p, *_ in rows:
        if p is not None:
            positions[int(d)][int(n)] = int(p)
    ends = lap_end_times((d, n, lt, st, s3) for d, n, _, lt, st, s3 in rows)
    return led_every_lap(running_order(positions, ends), driver)
