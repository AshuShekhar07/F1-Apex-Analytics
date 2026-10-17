"""Era-fair car measures for comparing cars of different seasons. Read-only: it reads the timing
data and the race model's split of results into car and driver (championship_model_v1), and
changes no prediction model.

Raw lap times cannot be compared across seasons (tyres, fuel, aero and power-unit rules all
changed), so every measure here is relative to the car's OWN season's field:

  dominance       per Grand Prix, how far the car stood out from that race's field in qualifying
                  and in race pace, in units of the field's spread (a robust z-score: lead over
                  the median team / 1.4826 x the median absolute deviation); the season value is
                  the mean over its races with a standard error, so a lead in a tightly packed
                  season counts as much as a bigger lead in a spread-out one
                  two versions: "car only" (the mean of both cars, so one star driver weighs
                  half) and "car + drivers" (the team's quicker car)
  margin          per Grand Prix, % of a lap ahead of the best other car (+) or behind the
                  quickest car (-); the season value is the median
  model           the race model's car strength for the season with driver skill separated
                  (fitted on this season and the one before, every race weighted the same),
                  turned into: with an average driver in every car, the share of races this car
                  would win against its own season's cars
  fingerprint     all within the season: dominance at slow / medium / fast circuits (average
                  qualifying speed, terciles over every circuit), straight-line speed (speed-trap
                  z), tyre wear (seconds per lap of tyre age against the median team, from clean
                  stint laps), race against qualifying (race dominance - qualifying dominance),
                  consistency (spread of its race-by-race dominance) and mechanical failures
  achievements    normalised for the points systems: share of the most points a team could
                  score that season (two cars, sprints, fastest-lap point), share of all points,
                  win / pole / podium / one-two / front-row-lockout rates, pace rank against the
                  constructors' position
  drivers         per driver: races, points, qualifying and race head-to-heads with the
                  teammate(s), and the model's skill rank among that season's drivers
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from typing import Any

from sqlalchemy import text

ERAS = {
    "era1_13inch": ("2017-2021 rules", "13-inch wheels, high-downforce wide cars, 1.6 V6 hybrid power units"),
    "era2_18inch_groundeffect": ("2022-2025 rules", "ground-effect floors, 18-inch wheels, simpler wings"),
    "era3_2026regs": ("2026 rules", "new power units with a bigger electric share, active aerodynamics, smaller cars"),
}
MIN_TEAMS = 5               # teams with data in a race before it is compared
MIN_SCALE = 0.05            # the smallest field spread used (% of a lap), so a dead heat is not infinite
CAR_ONLY_SPLIT = 1.5        # % a car may be off its teammate before it is treated as not showing the car's pace
RACE_LAP_CUTOFF = 1.07
MIN_RACE_SHARE = 0.4
MIN_STINT_LAPS = 6          # clean laps in a stint before its tyre wear is measured
MIN_CLASS_RACES = 2         # races at a circuit type before a value is given
MODEL_HALF_LIFE = 1e6       # every race of the fit weighted the same


def _median(v):
    return statistics.median(v) if v else None


def _mean(v):
    return statistics.fmean(v) if v else None


def _robust_scale(values: list[float]) -> float:
    med = statistics.median(values)
    mad = statistics.median(abs(v - med) for v in values)
    return max(1.4826 * mad, MIN_SCALE)


def team_value(times: list[float], car_only: bool) -> float | None:
    """One time per team for a session: its quicker car, or for "car only" the mean of its cars
    unless one is clearly off the other (crash, failure, aborted lap), then the quicker one."""
    times = sorted(t for t in times if t is not None and t > 0)
    if not times:
        return None
    if not car_only or len(times) == 1:
        return times[0]
    if (times[-1] / times[0] - 1) * 100 > CAR_ONLY_SPLIT:
        return times[0]
    return statistics.fmean(times)


def race_standing(values: dict[int, float], lower_is_better: bool = True) -> dict[int, dict]:
    """team -> {gap: % behind (+) / ahead (-) of the median team (raw difference when higher is
    better), z: how far it stood out (+ = better), margin: % ahead of the best other team (+) or
    behind the best (-)}. Needs MIN_TEAMS teams."""
    if len(values) < MIN_TEAMS:
        return {}
    mid = statistics.median(values.values())
    if lower_is_better:
        gaps = {t: (v / mid - 1) * 100 for t, v in values.items()}
    else:
        gaps = {t: v - mid for t, v in values.items()}
    scale = _robust_scale(list(gaps.values())) if lower_is_better else max(_robust_scale(list(gaps.values())), 0.5)
    sign = -1 if lower_is_better else 1
    out = {}
    for t, g in gaps.items():
        others = [o for u, o in gaps.items() if u != t]
        best_other = min(others) if lower_is_better else max(others)
        out[t] = {"gap": g, "z": sign * g / scale, "margin": (best_other - g) if lower_is_better else (g - best_other)}
    return out


def speed_classes(race_speeds: dict[int, float]) -> dict[int, str]:
    """race -> slow / medium / fast by the average speed of its fastest qualifying lap, in
    terciles over every race (so the classes do not depend on the season)."""
    if len(race_speeds) < 3:
        return {}
    ordered = sorted(race_speeds.values())
    lo, hi = ordered[len(ordered) // 3], ordered[(2 * len(ordered)) // 3]
    return {r: "slow" if v < lo else "fast" if v >= hi else "medium" for r, v in race_speeds.items()}


def stint_slope(laps: list[tuple[int, float]]) -> float | None:
    """Least-squares seconds per lap over a stint's clean laps [(lap number, time)]."""
    if len(laps) < MIN_STINT_LAPS:
        return None
    xs = [x for x, _ in laps]
    ys = [y for _, y in laps]
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    den = sum((x - mx) ** 2 for x in xs)
    return None if den == 0 else sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den


def softmax(values: dict[Any, float]) -> dict[Any, float]:
    if not values:
        return {}
    top = max(values.values())
    e = {k: math.exp(v - top) for k, v in values.items()}
    s = sum(e.values())
    return {k: v / s for k, v in e.items()}


def points_ceiling(season: int, races: int, sprints: int) -> float:
    """The most points one team (two cars) could score: first and second every race (+ the
    fastest-lap point 2019-2024) and every sprint."""
    from championship_model_v1 import RACE_POINTS, fastest_lap_point, sprint_points

    sp = sprint_points(season)
    per_race = RACE_POINTS[0] + RACE_POINTS[1] + (1 if fastest_lap_point(season) else 0)
    return races * per_race + sprints * (sum(sp[:2]) if sp else 0)


# --- data -------------------------------------------------------------------------------------

def _races(db) -> dict[int, dict]:
    return {r["id"]: dict(r) for r in db.execute(text("""
        SELECT r.id, r.season_year AS season, r.round_number AS round, r.race_date, r.regulation_era AS era,
               t.id AS track_id, t.name AS track, t.country, t.length_km::float8 AS length_km
        FROM races r LEFT JOIN tracks t ON t.id = r.track_id""")).mappings()}


def _quali_rows(db):
    return db.execute(text("""
        SELECT s.race_id, re.team_id, re.driver_id, q.final_position,
               NULLIF(LEAST(COALESCE(q.q1_time, 1e9), COALESCE(q.q2_time, 1e9), COALESCE(q.q3_time, 1e9)), 1e9)::float8 AS t
        FROM qualifying_results q
        JOIN sessions s ON s.id = q.session_id AND s.session_type = 'Q'
        JOIN race_entries re ON re.id = q.race_entry_id
        WHERE re.team_id IS NOT NULL""")).mappings().all()


def _race_pace_rows(db):
    return db.execute(text("""
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
        SELECT d.race_id, re.team_id, re.driver_id, d.pace::float8 AS t
        FROM drv2 d JOIN race_entries re ON re.id = d.race_entry_id
        WHERE d.n >= :share * d.nmax AND re.team_id IS NOT NULL"""),
        {"cut": RACE_LAP_CUTOFF, "share": MIN_RACE_SHARE}).mappings().all()


def _speed_rows(db):
    has = db.execute(text("""SELECT 1 FROM information_schema.columns
                             WHERE table_name = 'laps' AND column_name = 'speed_st_kmh'""")).first()
    if not has:
        return []
    return db.execute(text("""
        SELECT s.race_id, re.team_id, MAX(l.speed_st_kmh)::float8 AS v
        FROM laps l JOIN sessions s ON s.id = l.session_id AND s.session_type = 'Q'
        JOIN race_entries re ON re.id = l.race_entry_id
        WHERE l.speed_st_kmh BETWEEN 150 AND 400 AND re.team_id IS NOT NULL
        GROUP BY 1, 2""")).mappings().all()


def _stint_laps(db):
    """Race laps inside each tyre stint, out-lap and in-lap left out."""
    has = db.execute(text("SELECT to_regclass('race_stints') IS NOT NULL")).scalar()
    if not has:
        return []
    return db.execute(text("""
        SELECT st.race_id, re.team_id, st.race_entry_id, st.stint_number, l.lap_number, l.lap_time::float8 AS t
        FROM race_stints st
        JOIN race_entries re ON re.id = st.race_entry_id
        JOIN sessions s ON s.race_id = st.race_id AND s.session_type = 'R'
        JOIN laps l ON l.session_id = s.id AND l.race_entry_id = st.race_entry_id
             AND l.lap_number > st.start_lap AND l.lap_number < st.end_lap AND l.lap_number > 1
        WHERE l.lap_time > 0 AND re.team_id IS NOT NULL""")).mappings().all()


def _result_rows(db):
    return db.execute(text("""
        SELECT s.race_id, s.session_type AS kind, re.team_id, re.driver_id, rr.finishing_position AS pos,
               COALESCE(NULLIF(rr.points, 'NaN'), 0)::float8 AS pts, rr.status
        FROM race_results rr
        JOIN sessions s ON s.id = rr.session_id AND s.session_type IN ('R', 'S')
        JOIN race_entries re ON re.id = rr.race_entry_id
        WHERE re.team_id IS NOT NULL""")).mappings().all()


# --- the measures -------------------------------------------------------------------------------

def _per_race(rows, field: str = "t") -> dict[int, dict[int, list[float]]]:
    out: dict[int, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r[field] is not None:
            out[r["race_id"]][r["team_id"]].append(float(r[field]))
    return out


def _standings(per_race, races, car_only: bool, lower_is_better: bool = True):
    """(season, team) -> {race_id: standing} for every race with enough teams."""
    out: dict[tuple[int, int], dict[int, dict]] = defaultdict(dict)
    for race_id, teams in per_race.items():
        season = races.get(race_id, {}).get("season")
        if season is None:
            continue
        vals = {t: v for t, ts in teams.items() if (v := team_value(ts, car_only) if lower_is_better else max(ts)) is not None}
        for t, st in race_standing(vals, lower_is_better).items():
            out[(season, t)][race_id] = st
    return out


def _season_dominance(q: dict[int, dict], r: dict[int, dict]) -> tuple[dict | None, dict[int, float]]:
    """Mean of the per-race dominance (qualifying and race averaged where both exist), its
    standard error and the per-race values."""
    per_race = {}
    for race_id in set(q) | set(r):
        zs = [x[race_id]["z"] for x in (q, r) if race_id in x]
        per_race[race_id] = statistics.fmean(zs)
    vals = list(per_race.values())
    if len(vals) < 3:
        return None, per_race
    sd = statistics.stdev(vals)
    return {"z": round(statistics.fmean(vals), 3), "se": round(sd / math.sqrt(len(vals)), 3), "races": len(vals),
            "spread": round(sd, 3)}, per_race


def _tyre_wear(stint_rows, races) -> dict[tuple[int, int], float]:
    """(season, team) -> median over its races of (its stints' median slope - the median team's), s/lap."""
    stints: dict[tuple, list[tuple[int, float]]] = defaultdict(list)
    for r in stint_rows:
        stints[(r["race_id"], r["team_id"], r["race_entry_id"], r["stint_number"])].append((r["lap_number"], r["t"]))
    by_race: dict[int, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    for (race_id, team, _e, _n), laps in stints.items():
        med = statistics.median(t for _, t in laps)
        clean = [(x, t) for x, t in laps if t < med * RACE_LAP_CUTOFF]
        slope = stint_slope(clean)
        if slope is not None and abs(slope) < 1.0:
            by_race[race_id][team].append(slope)
    out: dict[tuple[int, int], list[float]] = defaultdict(list)
    for race_id, teams in by_race.items():
        vals = {t: statistics.median(s) for t, s in teams.items()}
        if len(vals) < MIN_TEAMS or race_id not in races:
            continue
        mid = statistics.median(vals.values())
        for t, v in vals.items():
            out[(races[race_id]["season"], t)].append(v - mid)
    return {k: statistics.median(v) for k, v in out.items() if len(v) >= 3}


def _model(db, seasons: list[int]) -> tuple[dict[tuple[int, int], dict], dict[tuple[int, int], float]]:
    """(season, team) -> {win_share, rank, teams}; (season, driver) -> skill. From the race
    model's strengths fitted on the season and the one before, every race weighted the same."""
    from championship_model_v1 import RACE_WINTER_SD, fit_strengths, load_weekends

    weekends = [w for w in load_weekends(db, start_year=min(seasons) - 1) if w.raced]
    cars: dict[tuple[int, int], dict] = {}
    skills: dict[tuple[int, int], float] = {}
    for season in seasons:
        mine = [w for w in weekends if w.season == season]
        if not mine:
            continue
        fit = fit_strengths([w for w in weekends if season - 1 <= w.season <= season], era=mine[-1].era,
                            winter_sd=RACE_WINTER_SD, season=season, half_life=MODEL_HALF_LIFE)
        teams = {r.team_id for w in mine for r in w.race}
        drivers = {r.driver_id for w in mine for r in w.race}
        share = softmax({t: fit.car[t] for t in teams if t in fit.car})
        for i, (t, p) in enumerate(sorted(share.items(), key=lambda kv: -kv[1]), 1):
            cars[(season, t)] = {"win_share": round(p, 4), "rank": i, "teams": len(share)}
        for d in drivers:
            if d in fit.skill:
                skills[(season, d)] = fit.skill[d]
    return cars, skills


def compute(db, base: dict) -> dict:
    """base: car_ratings_v1.car_table(db). Returns {key: fair measures} and {key: races list}."""
    races = _races(db)
    q_rows, r_rows = _quali_rows(db), _race_pace_rows(db)
    q_per, r_per = _per_race(q_rows), _per_race(r_rows)
    stand = {
        "car_only": (_standings(q_per, races, True), _standings(r_per, races, True)),
        "with_drivers": (_standings(q_per, races, False), _standings(r_per, races, False)),
    }
    speed = _standings(_per_race(_speed_rows(db), "v"), races, False, lower_is_better=False)
    wear = _tyre_wear(_stint_laps(db), races)

    # circuit type from the average speed of each race's fastest qualifying lap
    pole: dict[int, float] = {}
    for r in q_rows:
        if r["t"]:
            pole[r["race_id"]] = min(pole.get(r["race_id"], 1e9), float(r["t"]))
    kinds = speed_classes({rid: races[rid]["length_km"] * 3600 / t for rid, t in pole.items()
                           if rid in races and races[rid]["length_km"]})

    results = _result_rows(db)
    season_races: dict[int, set] = defaultdict(set)
    season_sprints: dict[int, set] = defaultdict(set)
    season_points: dict[int, float] = defaultdict(float)
    drv: dict[tuple[int, int], dict] = defaultdict(lambda: {"races": 0, "points": 0.0})
    finish: dict[int, dict[int, tuple[int, bool]]] = defaultdict(dict)       # race -> driver -> (pos, classified)
    from race_status import is_classified
    for r in results:
        season = races.get(r["race_id"], {}).get("season")
        if season is None:
            continue
        season_points[season] += r["pts"]
        key = (season, r["driver_id"])
        drv[key]["points"] += r["pts"]
        if r["kind"] == "S":
            season_sprints[season].add(r["race_id"])
            continue
        season_races[season].add(r["race_id"])
        drv[key]["races"] += 1
        if r["pos"]:
            finish[r["race_id"]][r["driver_id"]] = (r["pos"], is_classified(r["status"]))

    # head-to-heads between teammates and front-row lockouts
    team_of: dict[tuple[int, int], int] = {}
    quali_pos: dict[int, dict[int, int]] = defaultdict(dict)
    quali_team: dict[int, dict[int, list[int]]] = defaultdict(lambda: defaultdict(list))
    for r in q_rows:
        team_of[(r["race_id"], r["driver_id"])] = r["team_id"]
        if r["final_position"]:
            quali_pos[r["race_id"]][r["driver_id"]] = r["final_position"]
            quali_team[r["race_id"]][r["team_id"]].append(r["final_position"])
    season_qualis: dict[int, set] = defaultdict(set)
    for race_id in quali_pos:
        if race_id in races:
            season_qualis[races[race_id]["season"]].add(race_id)
    h2h: dict[tuple[int, int], dict] = defaultdict(lambda: {"quali": [0, 0], "race": [0, 0]})   # [won, lost]
    lockouts: dict[tuple[int, int], int] = defaultdict(int)
    for race_id, pos in quali_pos.items():
        season = races.get(race_id, {}).get("season")
        if season is None:
            continue
        for team, ps in quali_team[race_id].items():
            if sorted(ps)[:2] == [1, 2]:
                lockouts[(season, team)] += 1
        by_team: dict[int, list[int]] = defaultdict(list)
        for d in pos:
            by_team[team_of[(race_id, d)]].append(d)
        for team, ds in by_team.items():
            for a in ds:
                for b in ds:
                    if a == b:
                        continue
                    h2h[(season, a)]["quali"][0 if pos[a] < pos[b] else 1] += 1
                    fa, fb = finish[race_id].get(a), finish[race_id].get(b)
                    if fa and fb and fa[1] and fb[1]:
                        h2h[(season, a)]["race"][0 if fa[0] < fb[0] else 1] += 1

    seasons = sorted({c["season"] for c in base["cars"]})
    try:
        model_cars, skills = _model(db, seasons)
    except Exception:                                    # too little history: the measure is left out
        model_cars, skills = {}, {}
    skill_rank: dict[tuple[int, int], tuple[int, int]] = {}
    for season in seasons:
        ranked = sorted(((d, s) for (y, d), s in skills.items() if y == season), key=lambda kv: -kv[1])
        for i, (d, _) in enumerate(ranked, 1):
            skill_rank[(season, d)] = (i, len(ranked))

    fair: dict[str, dict] = {}
    race_lists: dict[str, list[dict]] = {}
    dominance_by_season: dict[int, dict[str, float]] = defaultdict(dict)
    for car in base["cars"]:
        k = (car["season"], car["team_id"])
        co_q, co_r = stand["car_only"][0].get(k, {}), stand["car_only"][1].get(k, {})
        wd_q, wd_r = stand["with_drivers"][0].get(k, {}), stand["with_drivers"][1].get(k, {})
        dom_co, per_race = _season_dominance(co_q, co_r)
        dom_wd, _ = _season_dominance(wd_q, wd_r)
        sp = speed.get(k, {})
        by_kind: dict[str, list[float]] = defaultdict(list)
        for race_id, z in per_race.items():
            if race_id in kinds:
                by_kind[kinds[race_id]].append(z)
        quali_dom = _mean([x["z"] for x in co_q.values()])
        race_dom = _mean([x["z"] for x in co_r.values()])
        if dom_co:
            dominance_by_season[car["season"]][car["key"]] = dom_co["z"]
        res, met = car["results"], car["metrics"]
        races_n, sprints_n = len(season_races[car["season"]]), len(season_sprints[car["season"]])
        ceiling = points_ceiling(car["season"], races_n, sprints_n)
        team_drivers = [d["id"] for d in car["drivers"]]
        fair[car["key"]] = {
            "era": races_era(races, car["season"]),
            "pace_rank": None,
            "dominance": {"car_only": dom_co, "with_drivers": dom_wd},
            "margin": {
                "quali_pct": _r(_median([x["margin"] for x in co_q.values()]), 3),
                "race_pct": _r(_median([x["margin"] for x in co_r.values()]), 3),
                "quali_pct_with_drivers": _r(_median([x["margin"] for x in wd_q.values()]), 3),
                "race_pct_with_drivers": _r(_median([x["margin"] for x in wd_r.values()]), 3),
            },
            "model": model_cars.get(k),
            "fingerprint": {
                "slow": _r(_mean(by_kind["slow"]) if len(by_kind["slow"]) >= MIN_CLASS_RACES else None, 2),
                "medium": _r(_mean(by_kind["medium"]) if len(by_kind["medium"]) >= MIN_CLASS_RACES else None, 2),
                "fast": _r(_mean(by_kind["fast"]) if len(by_kind["fast"]) >= MIN_CLASS_RACES else None, 2),
                "straights": _r(_mean([x["z"] for x in sp.values()]) if len(sp) >= 3 else None, 2),
                "tyre_wear_s_per_lap": _r(wear.get(k), 3),
                "race_vs_quali": _r(race_dom - quali_dom if race_dom is not None and quali_dom is not None
                                    and len(co_q) >= 3 and len(co_r) >= 3 else None, 2),
                "consistency_spread": dom_co["spread"] if dom_co else None,
                "mechanical_per_start": _r(met["mechanical"] / met["starts"] if met["starts"] else None, 3),
            },
            "achievements": {
                "points_share_of_max": _r(res["points"] / ceiling if ceiling else None, 3),
                "points_share_of_field": _r(res["points"] / season_points[car["season"]] if season_points[car["season"]] else None, 3),
                "win_rate": _r(res["wins"] / res["races"] if res["races"] else None, 3),
                "pole_rate": _r(res["poles"] / len(season_qualis[car["season"]]) if season_qualis[car["season"]] else None, 3),
                "podiums_per_race": _r(res["podiums"] / res["races"] if res["races"] else None, 3),
                "one_two_rate": _r(res["one_twos"] / res["races"] if res["races"] else None, 3),
                "front_row_lockouts": lockouts.get(k, 0),
                "points_ceiling": ceiling, "sprints": sprints_n,
            },
            "drivers": [
                {"id": d, "name": next((x["name"] for x in car["drivers"] if x["id"] == d), None),
                 "races": drv[(car["season"], d)]["races"], "points": round(drv[(car["season"], d)]["points"], 1),
                 "quali_h2h": h2h[(car["season"], d)]["quali"] if len(team_drivers) > 1 else None,
                 "race_h2h": h2h[(car["season"], d)]["race"] if len(team_drivers) > 1 else None,
                 "skill_rank": list(skill_rank[(car["season"], d)]) if (car["season"], d) in skill_rank else None}
                for d in team_drivers],
        }
        race_lists[car["key"]] = [
            {"race_id": rid, "round": races[rid]["round"], "track_id": races[rid]["track_id"],
             "track": races[rid]["track"], "country": races[rid]["country"], "kind": kinds.get(rid),
             "z": round(per_race[rid], 3),
             "quali_gap_pct": _r(co_q[rid]["gap"], 3) if rid in co_q else None,
             "race_gap_pct": _r(co_r[rid]["gap"], 3) if rid in co_r else None,
             "quali_margin_pct": _r(co_q[rid]["margin"], 3) if rid in co_q else None,
             "race_margin_pct": _r(co_r[rid]["margin"], 3) if rid in co_r else None}
            for rid in sorted(per_race, key=lambda rid: races[rid]["round"])]

    # pace rank within the season (car only), against the constructors' position
    for season, doms in dominance_by_season.items():
        for i, (key, _) in enumerate(sorted(doms.items(), key=lambda kv: -kv[1]), 1):
            fair[key]["pace_rank"] = [i, len(doms)]
    return {"fair": fair, "races": race_lists}


def races_era(races: dict[int, dict], season: int) -> dict | None:
    eras = [r["era"] for r in races.values() if r["season"] == season and r["era"]]
    if not eras:
        return None
    code = statistics.mode(eras)
    label, rules = ERAS.get(code, (code, ""))
    return {"code": code, "label": label, "rules": rules}


def _r(v, digits):
    return None if v is None else round(v, digits)


_cache: dict[tuple, dict] = {}


def fair_table(db) -> dict:
    """compute(), cached with car_ratings_v1's table (same data fingerprint)."""
    import car_ratings_v1

    stamp = car_ratings_v1.data_stamp(db)
    if stamp not in _cache:
        _cache.clear()
        _cache[stamp] = compute(db, car_ratings_v1.car_table(db))
    return _cache[stamp]
