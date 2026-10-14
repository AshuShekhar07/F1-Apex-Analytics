"""Car ratings 2018-2026, measured from the timing data. Read-only, and separate from every
prediction model.

For every team and season, against the rest of the field at each Grand Prix:

  qualifying pace  the team's fastest qualifying lap (either car, any part of qualifying) as %
                   behind the field's median team; the season value is the median over its races
  race pace        each car's median race lap (lap 1 left out, and laps over 107% of the race's
                   median lap: pit stops, Safety Cars, incidents), the team's better car, as %
                   behind the median team; cars that ran under 40% of the race are left out
  top speed        the team's highest speed-trap reading in qualifying, in km/h against the median
                   team (everyone can use DRS in qualifying, so the tow and DRS matter less)
  reliability      classified finishes per start, crashes and collisions recorded as such not
                   counted against the car (a plain "Retired" is)

Each becomes a 0-100 rating by ranking every team-season from 2018 to 2026 together: 100 is
the best of all of them, 0 the worst. So a car far ahead of its field (the 2020 Mercedes, the
2023 Red Bull) rates near 100, and the best car of a close season a little lower. Overall =
(2 x qualifying + 2 x race pace + reliability) / 5.

Character: within its own season, a car's top speed against its lap pace. Clearly quicker on
the straights than its lap pace suggests -> "low drag" (or a strong power unit); clearly slower
-> "high downforce" (it makes its time in the corners). Timing data cannot measure downforce
directly; this is the closest honest reading of it.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Any

from sqlalchemy import text

import cars_catalogue_v1 as catalogue
from race_status import CLASSIFIED_STATUSES, DISQUALIFIED_STATUSES, DNF_STATUSES, DNS_STATUSES, WITHDRAWN_STATUSES

INCIDENT_STATUSES = ("Accident", "Collision", "Collision damage", "Spun off", "Damage", "Debris")
UNKNOWN_STATUSES = ("Retired", "Illness")
MECHANICAL_STATUSES = tuple(s for s in DNF_STATUSES if s not in INCIDENT_STATUSES + UNKNOWN_STATUSES)
MIN_RACES = 3               # races with data before a pace or speed value is given
RACE_LAP_CUTOFF = 1.07      # laps slower than this x the race's median lap are left out
MIN_RACE_SHARE = 0.4        # cars that ran fewer laps than this share of the most are left out
CHARACTER_Z = 0.8           # top speed against lap pace (in standard deviations) for a label
WEIGHTS = {"quali": 2, "race": 2, "reliability": 1}

METHOD = {
    "quali": "Fastest qualifying lap of either car, % behind the median team, median over the season.",
    "race": "Median race lap of the team's better car (no lap 1, pit or Safety Car laps), % behind the median team.",
    "speed": "Highest qualifying speed-trap reading, km/h against the median team.",
    "reliability": "Classified finishes per start; recorded crashes and collisions are not held against the car.",
    "ratings": "0-100: every team-season of 2018-2026 ranked together, 100 the best of all. "
               "Overall = (2 x qualifying + 2 x race pace + reliability) / 5.",
    "character": "Top speed against lap pace within the season. Timing data cannot measure downforce directly.",
}


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _gaps_by_race(rows: list[tuple[int, int, int, float]], lower_is_better: bool = True):
    """(season, race, team, value) -> per team-season: the per-race gap to the median team
    (% for times, raw difference otherwise), plus per race who was best and by how much."""
    by_race: dict[int, list[tuple[int, int, float]]] = defaultdict(list)
    for season, race, team, value in rows:
        by_race[race].append((season, team, float(value)))
    gaps: dict[tuple[int, int], list[float]] = defaultdict(list)
    best: dict[tuple[int, int], int] = defaultdict(int)
    for race, items in by_race.items():
        if len(items) < 5:                       # too few teams with data to compare
            continue
        mid = statistics.median(v for _, _, v in items)
        for season, team, v in items:
            gaps[(season, team)].append((v / mid - 1) * 100 if lower_is_better else v - mid)
        season, team, _ = (min if lower_is_better else max)(items, key=lambda x: x[2])
        best[(season, team)] += 1
    return gaps, best


def _qualifying(db) -> list[tuple[int, int, int, float]]:
    return [tuple(r) for r in db.execute(text("""
        SELECT r.season_year, r.id, re.team_id,
               MIN(LEAST(COALESCE(q.q1_time, 1e9), COALESCE(q.q2_time, 1e9), COALESCE(q.q3_time, 1e9)))::float8
        FROM qualifying_results q
        JOIN sessions s ON s.id = q.session_id AND s.session_type = 'Q'
        JOIN races r ON r.id = s.race_id
        JOIN race_entries re ON re.id = q.race_entry_id
        WHERE re.team_id IS NOT NULL
        GROUP BY 1, 2, 3
        HAVING MIN(LEAST(COALESCE(q.q1_time, 1e9), COALESCE(q.q2_time, 1e9), COALESCE(q.q3_time, 1e9))) < 1e9""")).all()]


def _race_pace(db) -> list[tuple[int, int, int, float]]:
    return [tuple(r) for r in db.execute(text("""
        WITH rl AS (
            SELECT s.race_id, l.race_entry_id, l.lap_time::float8 AS t
            FROM laps l JOIN sessions s ON s.id = l.session_id AND s.session_type = 'R'
            WHERE l.lap_number > 1 AND l.lap_time > 0),
        med AS (SELECT race_id, percentile_cont(0.5) WITHIN GROUP (ORDER BY t) AS m FROM rl GROUP BY race_id),
        drv AS (
            SELECT rl.race_id, rl.race_entry_id, percentile_cont(0.5) WITHIN GROUP (ORDER BY rl.t) AS pace, COUNT(*) AS n
            FROM rl JOIN med USING (race_id) WHERE rl.t < med.m * :cut
            GROUP BY rl.race_id, rl.race_entry_id),
        drv2 AS (SELECT *, MAX(n) OVER (PARTITION BY race_id) AS nmax FROM drv)
        SELECT r.season_year, d.race_id, re.team_id, MIN(d.pace)
        FROM drv2 d JOIN race_entries re ON re.id = d.race_entry_id JOIN races r ON r.id = d.race_id
        WHERE d.n >= :share * d.nmax AND re.team_id IS NOT NULL
        GROUP BY 1, 2, 3"""), {"cut": RACE_LAP_CUTOFF, "share": MIN_RACE_SHARE}).all()]


def _top_speed(db) -> list[tuple[int, int, int, float]]:
    has = db.execute(text("""SELECT 1 FROM information_schema.columns
                             WHERE table_name = 'laps' AND column_name = 'speed_st_kmh'""")).first()
    if not has:
        return []
    return [tuple(r) for r in db.execute(text("""
        SELECT r.season_year, r.id, re.team_id, MAX(l.speed_st_kmh)::float8
        FROM laps l JOIN sessions s ON s.id = l.session_id AND s.session_type = 'Q'
        JOIN races r ON r.id = s.race_id JOIN race_entries re ON re.id = l.race_entry_id
        WHERE l.speed_st_kmh BETWEEN 150 AND 400 AND re.team_id IS NOT NULL
        GROUP BY 1, 2, 3""")).all()]


def _results(db) -> list[dict]:
    return [dict(r) for r in db.execute(text("""
        SELECT r.season_year AS season, r.id AS race_id, s.session_type AS kind, re.team_id, re.driver_id,
               rr.finishing_position AS pos, COALESCE(NULLIF(rr.points, 'NaN'), 0)::float8 AS pts, rr.status
        FROM race_results rr
        JOIN sessions s ON s.id = rr.session_id AND s.session_type IN ('R', 'S')
        JOIN races r ON r.id = s.race_id
        JOIN race_entries re ON re.id = rr.race_entry_id
        WHERE re.team_id IS NOT NULL""")).mappings()]


def _poles(db) -> dict[tuple[int, int], int]:
    return {(s, t): n for s, t, n in db.execute(text("""
        SELECT r.season_year, re.team_id, COUNT(*)
        FROM qualifying_results q
        JOIN sessions s ON s.id = q.session_id AND s.session_type = 'Q'
        JOIN races r ON r.id = s.race_id JOIN race_entries re ON re.id = q.race_entry_id
        WHERE q.final_position = 1 GROUP BY 1, 2"""))}


def _teams(db) -> dict[int, dict]:
    return {r["id"]: dict(r) for r in db.execute(text("SELECT id, name, color_hex FROM teams")).mappings()}


def _drivers(db) -> dict[int, str]:
    return dict(db.execute(text("SELECT id, name FROM drivers")).all())


def rank_ratings(values: dict[Any, float], higher_is_better: bool) -> dict[Any, int]:
    """0-100 by rank among all the values (ties share their average rank); 100 = best."""
    if not values:
        return {}
    ordered = sorted(values.items(), key=lambda kv: kv[1], reverse=not higher_is_better)   # worst first
    n = len(ordered)
    out: dict[Any, int] = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and ordered[j + 1][1] == ordered[i][1]:
            j += 1
        rank = (i + j) / 2
        for k in range(i, j + 1):
            out[ordered[k][0]] = 100 if n == 1 else round(100 * rank / (n - 1))
        i = j + 1
    return out


def overall(ratings: dict[str, int | None]) -> int | None:
    parts = [(WEIGHTS[k], v) for k, v in ratings.items() if k in WEIGHTS and v is not None]
    if not parts or ratings.get("quali") is None and ratings.get("race") is None:
        return None
    return round(sum(w * v for w, v in parts) / sum(w for w, _ in parts))


def character(speed_z: float | None, pace_z: float | None) -> dict | None:
    """pace_z: how quick over a lap (higher = quicker), speed_z: how quick on the straights,
    both in standard deviations within the season."""
    if speed_z is None or pace_z is None:
        return None
    d = speed_z - pace_z
    if d > CHARACTER_Z:
        return {"kind": "low-drag", "label": "Low drag",
                "text": "Quicker on the straights than its lap pace suggests: a low-drag car, or a strong power unit."}
    if d < -CHARACTER_Z:
        return {"kind": "high-downforce", "label": "High downforce",
                "text": "Slower on the straights than its lap pace suggests: it made its time in the corners."}
    return {"kind": "balanced", "label": "Balanced", "text": "Its top speed matches its lap pace."}


def _z(values: dict[Any, float]) -> dict[Any, float]:
    if len(values) < 4:
        return {}
    mu = statistics.mean(values.values())
    sd = statistics.pstdev(values.values())
    return {k: (v - mu) / sd for k, v in values.items()} if sd > 0 else {}


def compute(db) -> dict:
    teams, driver_names = _teams(db), _drivers(db)
    q_gaps, q_best = _gaps_by_race(_qualifying(db))
    r_gaps, r_best = _gaps_by_race(_race_pace(db))
    s_gaps, _ = _gaps_by_race(_top_speed(db), lower_is_better=False)
    poles = _poles(db)

    res: dict[tuple[int, int], dict] = defaultdict(lambda: {
        "gps": set(), "points": 0.0, "wins": 0, "podiums": 0, "one_twos": 0, "starts": 0, "finishes": 0,
        "incidents": 0, "mechanical": 0, "retired_unknown": 0, "drivers": defaultdict(int)})
    gp_count: dict[int, set] = defaultdict(set)
    top2: dict[tuple[int, int], list[int]] = defaultdict(list)    # (season, race) -> teams of P1 and P2
    for row in _results(db):
        key = (row["season"], row["team_id"])
        t = res[key]
        t["points"] += row["pts"]
        if row["kind"] != "R":
            continue
        gp_count[row["season"]].add(row["race_id"])
        t["gps"].add(row["race_id"])
        t["drivers"][row["driver_id"]] += 1
        st = row["status"]
        classified = st in CLASSIFIED_STATUSES or (st is None and row["pos"] is not None)
        if row["pos"] == 1 and classified:
            t["wins"] += 1
        if row["pos"] in (1, 2) and classified:
            top2[(row["season"], row["race_id"])].append(row["team_id"])
        if row["pos"] is not None and row["pos"] <= 3 and classified:
            t["podiums"] += 1
        if st in DNS_STATUSES or st in WITHDRAWN_STATUSES:
            continue
        t["starts"] += 1
        if classified or st in DISQUALIFIED_STATUSES:
            t["finishes"] += 1
        elif st in INCIDENT_STATUSES:
            t["incidents"] += 1
        elif st in MECHANICAL_STATUSES:
            t["mechanical"] += 1
        else:
            t["retired_unknown"] += 1
    for (season, _race), tms in top2.items():
        if len(tms) == 2 and tms[0] == tms[1]:
            res[(season, tms[0])]["one_twos"] += 1

    keys = [k for k, t in res.items() if t["gps"]]
    med = lambda d, k: _median(d.get(k, [])) if len(d.get(k, [])) >= MIN_RACES else None  # noqa: E731
    quali = {k: v for k in keys if (v := med(q_gaps, k)) is not None}
    race = {k: v for k in keys if (v := med(r_gaps, k)) is not None}
    speed = {k: v for k in keys if (v := med(s_gaps, k)) is not None}
    reliab = {k: res[k]["finishes"] / (res[k]["starts"] - res[k]["incidents"])
              for k in keys if res[k]["starts"] - res[k]["incidents"] >= MIN_RACES}
    ratings = {"quali": rank_ratings(quali, False), "race": rank_ratings(race, False),
               "speed": rank_ratings(speed, True), "reliability": rank_ratings(reliab, True)}

    # constructors' position, the season's quickest car and its margin, character
    by_season: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for k in keys:
        by_season[k[0]].append(k)
    position: dict[tuple[int, int], int] = {}
    margin: dict[tuple[int, int], dict] = {}
    char: dict[tuple[int, int], dict | None] = {}
    for season, ks in by_season.items():
        for i, k in enumerate(sorted(ks, key=lambda k: (-res[k]["points"], -res[k]["wins"])), 1):
            position[k] = i
        for name, d in (("quali", quali), ("race", race)):
            ranked = sorted((k for k in ks if k in d), key=lambda k: d[k])
            if len(ranked) >= 2:
                margin.setdefault(ranked[0], {})[name] = round(d[ranked[1]] - d[ranked[0]], 3)
        pace = {k: -(statistics.mean([v for v in (quali.get(k), race.get(k)) if v is not None]))
                for k in ks if k in quali or k in race}
        pz, sz = _z({k: v for k, v in pace.items() if k in speed}), _z({k: speed[k] for k in ks if k in speed and k in pace})
        for k in ks:
            char[k] = character(sz.get(k), pz.get(k))

    cars = []
    for k in sorted(keys, key=lambda k: (-k[0], position[k])):
        season, team_id = k
        t, team = res[k], teams.get(team_id, {})
        entry = catalogue.find(season, team.get("name"))
        r = {name: ratings[name].get(k) for name in ratings}
        r["overall"] = overall(r)
        cars.append({
            "key": entry.key if entry else f"{season}-{catalogue.normalise(team.get('name'))}",
            "season": season, "team_id": team_id, "team_name": team.get("name"), "color_hex": team.get("color_hex"),
            "car": entry.car if entry else None, "pu": entry.pu if entry else None,
            "livery": entry.livery if entry else None, "notes": list(entry.notes) if entry else [],
            "palette": list(entry.palette) if entry and entry.palette else None, "wiki": entry.wiki if entry else None,
            "drivers": [{"id": d, "name": driver_names.get(d), "races": n}
                        for d, n in sorted(t["drivers"].items(), key=lambda kv: -kv[1])],
            "results": {"position": position[k], "points": round(t["points"], 1), "races": len(t["gps"]),
                        "season_races": len(gp_count[season]), "wins": t["wins"], "poles": poles.get(k, 0),
                        "podiums": t["podiums"], "one_twos": t["one_twos"]},
            "metrics": {
                "quali_gap_pct": _round(quali.get(k), 3), "race_gap_pct": _round(race.get(k), 3),
                "top_speed_kmh": _round(speed.get(k), 1), "finish_rate": _round(reliab.get(k), 3),
                "starts": t["starts"], "finishes": t["finishes"], "incidents": t["incidents"],
                "mechanical": t["mechanical"], "retired_unknown": t["retired_unknown"],
                "quickest_in_qualifying": q_best.get(k, 0), "quickest_in_race": r_best.get(k, 0),
                "quali_races": len(q_gaps.get(k, [])), "race_races": len(r_gaps.get(k, [])),
                "speed_races": len(s_gaps.get(k, [])),
            },
            "ratings": r,
            "character": char.get(k),
            "season_best": margin.get(k, {}),
        })
    return {"seasons": sorted(by_season, reverse=True), "cars": cars, "count": len(cars), "method": METHOD}


def _round(v: float | None, digits: int) -> float | None:
    return None if v is None else round(v, digits)


_cache: dict[tuple, dict] = {}


def car_table(db) -> dict:
    """compute(), cached until race results, qualifying or laps change."""
    stamp = tuple(db.execute(text("""
        SELECT (SELECT COUNT(*) FROM race_results), (SELECT MAX(id) FROM race_results),
               (SELECT MAX(id) FROM qualifying_results), (SELECT MAX(id) FROM laps)""")).one())
    if stamp not in _cache:
        _cache.clear()
        _cache[stamp] = compute(db)
    return _cache[stamp]
