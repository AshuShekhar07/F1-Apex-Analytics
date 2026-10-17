"""Ideal lap: each driver's best sector 1 + best sector 2 + best sector 3 of a session, from any
of their laps, against their real best lap. The difference is the time left on the table.

Read-only, and separate from every prediction model: it only reads stored laps and results.

Rules, so the ideal lap is a lap the driver showed, not a data error:
  - a lap marked deleted (track limits) is left out, when deletions are stored for the session
  - a lap whose three sectors do not add up to its lap time (more than 0.5 s apart) is left
    out: one of its sector times is wrong
  - a sector time more than 7% quicker than the session's typical best for that sector is a
    timing glitch and is left out
  - qualifying's real best lap is the official one (fastest of Q1/Q2/Q3, deleted laps already
    excluded); other sessions use the fastest lap in the timing
  - an ideal lap a few thousandths slower than the real best lap is sector rounding (it equals
    the best lap); more than 0.05 s slower means the best lap's sector times are missing, so no
    ideal lap is given
The ultimate lap is the session's fastest sector 1 + sector 2 + sector 3, from anyone.
"""

from __future__ import annotations

from collections import defaultdict
from statistics import median
from typing import Any, Iterable

from sqlalchemy import text

SESSIONS = ("FP1", "FP2", "FP3", "SQ", "Q")
SUM_TOLERANCE = 0.5        # s: sectors vs lap time
GLITCH_SHARE = 0.93        # a sector under 93% of the session's typical best is a glitch

LapRow = tuple[int, int, float | None, float | None, float | None, float | None, bool | None]
# driver, lap number, lap time, sector 1, sector 2, sector 3, deleted


def _f(value) -> float | None:
    if value is None:
        return None
    v = float(value)
    return None if v != v or v <= 0 else v


def best_sectors(rows: Iterable[LapRow]) -> dict[int, list[tuple[float, int] | None]]:
    """driver -> [(time, lap) of the best sector 1, 2, 3] over the valid laps."""
    candidates: dict[int, list[list[tuple[float, int]]]] = defaultdict(lambda: [[], [], []])
    for driver, lap, lap_time, s1, s2, s3, deleted in rows:
        if deleted:
            continue
        sectors = [_f(s1), _f(s2), _f(s3)]
        lt = _f(lap_time)
        if lt is not None and all(s is not None for s in sectors) and abs(sum(sectors) - lt) > SUM_TOLERANCE:
            continue
        for k, s in enumerate(sectors):
            if s is not None:
                candidates[int(driver)][k].append((s, int(lap)))
    # typical best per sector: the median of the drivers' bests; anything far below is a glitch
    typical = []
    for k in range(3):
        bests = [min(c[k])[0] for c in candidates.values() if c[k]]
        typical.append(median(bests) if bests else None)
    out = {}
    for d, per_sector in candidates.items():
        best = []
        for k in range(3):
            ok = [c for c in per_sector[k] if typical[k] is None or c[0] >= GLITCH_SHARE * typical[k]]
            best.append(min(ok) if ok else None)
        out[d] = best
    return out


def best_laps(rows: Iterable[LapRow]) -> dict[int, float]:
    """driver -> fastest lap time among laps not marked deleted."""
    out: dict[int, float] = {}
    for driver, _, lap_time, *_rest, deleted in rows:
        lt = _f(lap_time)
        if lt is None or deleted:
            continue
        out[int(driver)] = min(lt, out.get(int(driver), lt))
    return out


def ideal_table(sectors: dict[int, list[tuple[float, int] | None]], best: dict[int, float],
                positions: dict[int, int] | None = None) -> tuple[list[dict], dict]:
    """(one row per driver, session facts: ultimate lap and who set each sector)."""
    session_best: list[tuple[float, int] | None] = []
    for k in range(3):
        times = [(s[k][0], d) for d, s in sectors.items() if s[k] is not None]
        session_best.append(min(times) if times else None)
    rows = []
    for d in set(sectors) | set(best):
        secs = sectors.get(d, [None, None, None])
        ideal = round(sum(s[0] for s in secs), 3) if all(s is not None for s in secs) else None
        lap = best.get(d)
        if ideal is not None and lap is not None and ideal > lap:
            # a few thousandths over is sector rounding; more means the best lap's sectors are missing
            ideal = round(lap, 3) if ideal <= lap + 0.05 else None
        rows.append({
            "driver_id": d, "best_lap": round(lap, 3) if lap is not None else None, "ideal_lap": ideal,
            "left_on_table": round(max(0.0, lap - ideal), 3) if lap is not None and ideal is not None else None,
            "sectors": [None if s is None else {"time": round(s[0], 3), "lap": s[1],
                                                "session_best": session_best[k] is not None and session_best[k][1] == d
                                                and abs(session_best[k][0] - s[0]) < 1e-9}
                        for k, s in enumerate(secs)],
        })
    ranked = sorted((r for r in rows if r["ideal_lap"] is not None), key=lambda r: r["ideal_lap"])
    for i, r in enumerate(ranked, 1):
        r["ideal_rank"] = i
    by_best = sorted((r for r in rows if r["best_lap"] is not None), key=lambda r: r["best_lap"])
    for i, r in enumerate(by_best, 1):
        r["best_rank"] = i
    for r in rows:
        r.setdefault("ideal_rank", None)
        r.setdefault("best_rank", None)
        r["position"] = (positions or {}).get(r["driver_id"]) or r["best_rank"]
    rows.sort(key=lambda r: (r["position"] is None, r["position"] or 99))
    ultimate = round(sum(s[0] for s in session_best), 3) if all(s is not None for s in session_best) else None
    fastest = min(best.values(), default=None)
    if ultimate is not None and fastest is not None and fastest < ultimate <= fastest + 0.05:
        ultimate = round(fastest, 3)                       # sector rounding, as for a driver's ideal lap
    return rows, {"ultimate_lap": ultimate,
                  "ultimate_sectors": [None if s is None else {"time": round(s[0], 3), "driver_id": s[1]} for s in session_best]}


def load_ideal_lap(db: Any, race_id: int, session_type: str | None = None) -> dict | None:
    """The ideal lap table of one session of a race weekend; None when the race does not exist.
    session_type None: qualifying if it has timing, else the latest session that has."""
    race = db.execute(text("SELECT id FROM races WHERE id = :r"), {"r": race_id}).scalar()
    if race is None:
        return None
    available = [t for (t,) in db.execute(text("""
        SELECT s.session_type FROM sessions s
        WHERE s.race_id = :r AND s.session_type = ANY(:types)
          AND EXISTS (SELECT 1 FROM laps l WHERE l.session_id = s.id AND l.sector_1_time IS NOT NULL)
    """), {"r": race_id, "types": list(SESSIONS)})]
    available.sort(key=SESSIONS.index)
    chosen = session_type or ("Q" if "Q" in available else (available[-1] if available else None))
    body = {"race_id": race_id, "session": chosen, "sessions_available": available, "drivers": []}
    if chosen not in available:
        return {**body, "note": "no sector timing stored for this session"}
    session_id = db.execute(text("SELECT id FROM sessions WHERE race_id = :r AND session_type = :t"),
                            {"r": race_id, "t": chosen}).scalar()
    rows = db.execute(text("""
        SELECT re.driver_id, l.lap_number, l.lap_time, l.sector_1_time, l.sector_2_time, l.sector_3_time, l.is_deleted
        FROM laps l JOIN race_entries re ON re.id = l.race_entry_id
        WHERE l.session_id = :s
    """), {"s": session_id}).all()
    deleted_known = any(r[6] is not None for r in rows)
    best = best_laps(rows)
    positions = None
    if chosen == "Q":
        official = db.execute(text("""
            SELECT re.driver_id, q.final_position, LEAST(q.q1_time, q.q2_time, q.q3_time) AS best
            FROM qualifying_results q JOIN race_entries re ON re.id = q.race_entry_id
            WHERE q.session_id = :s
        """), {"s": session_id}).all()
        positions = {int(d): int(p) for d, p, _ in official if p is not None}
        best.update({int(d): float(b) for d, _, b in official if b is not None})
    table, facts = ideal_table(best_sectors(rows), best, positions)
    info = {int(r["driver_id"]): r for r in db.execute(text("""
        SELECT re.driver_id, d.name, tm.name AS team_name, tm.color_hex
        FROM race_entries re JOIN drivers d ON d.id = re.driver_id LEFT JOIN teams tm ON tm.id = re.team_id
        WHERE re.race_id = :r
    """), {"r": race_id}).mappings()}
    for row in table:
        i = info.get(row["driver_id"], {})
        row.update({"driver_name": i.get("name"), "team_name": i.get("team_name"), "color_hex": i.get("color_hex")})
    for s in facts["ultimate_sectors"]:
        if s is not None:
            s["driver_name"] = info.get(s["driver_id"], {}).get("name")
    pole = min((r["best_lap"] for r in table if r["best_lap"] is not None), default=None)
    return {**body, **facts, "pole_lap": pole, "deleted_known": deleted_known, "drivers": table,
            "note": "Ideal lap: a driver's best sector 1 + 2 + 3 from any of their laps in the session. "
                    "Sectors can come from different runs, and the track gets quicker through a session."}
