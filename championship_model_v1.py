"""Race-win and championship probabilities from one strength model.

Every driver has a strength  s = car[team] + skill[driver], fitted on race results with the
Plackett-Luce (PL) ranking model: the chance a driver finishes ahead of the rest of the
remaining field is exp(s) / sum(exp(s) of everyone still to be placed). Finishing ahead of
others race after race raises a strength; it is the standard model for ranked outcomes.

  * only CLASSIFIED finishers are ranked -- a retirement is a separate, equal per-driver
    chance (team reliability did not predict retirements: race_reliability_v1), so one DNF
    costs the points of that race, not the driver's strength
  * recent races weigh more (half-life HALF_LIFE races), sprints half a race, races of another
    regulation era OTHER_ERA_WEIGHT; nothing after the as-of date is used
  * car and skill are shrunk toward 0 (normal priors), cars more freely than drivers, so a
    driver changing teams keeps their skill and takes the new car
  * season cars (winter_sd set): one car strength per team PER SEASON, each tied to the same
    team's previous-season car by a normal step of size winter_sd -- cars are rebuilt every
    winter, drivers are not. Without it one car strength spans seasons and a dominant car
    is carried into the next year (2024 Red Bull, 2021 Mercedes)

Race probabilities: s + grid effect (fitted on earlier races), DNFs sampled, the finishing
order sampled from PL (Gumbel trick: sort s + Gumbel noise) -> P(win), P(podium), P(points).

Championships: current points (race + sprint, from the database) plus Monte Carlo of every
remaining weekend with the season's points system -> P(drivers' title), P(constructors'
title), expected final points. All constants are set a priori; championship_backtest_v1.py
judges them on 2018-2025.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Sequence

import numpy as np
from scipy.optimize import minimize
from sqlalchemy import text

HALF_LIFE = 8.0             # races until an observation counts half
SPRINT_WEIGHT = 0.5
OTHER_ERA_WEIGHT = 0.3
MAX_HISTORY = 48            # weekends looked back (weights beyond are negligible)
CAR_PRIOR_SD = 1.0
SKILL_PRIOR_SD = 0.4
SIMS = 20000

RACE_POINTS = (25, 18, 15, 12, 10, 8, 6, 4, 2, 1)


def sprint_points(season: int) -> tuple[int, ...]:
    return (3, 2, 1) if season == 2021 else (8, 7, 6, 5, 4, 3, 2, 1) if season >= 2022 else ()


def fastest_lap_point(season: int) -> bool:
    return 2019 <= season <= 2024


# --- data ------------------------------------------------------------------------

@dataclass(frozen=True)
class Result:
    driver_id: int
    team_id: int
    grid: int | None
    finish: int | None
    classified: bool
    points: float


@dataclass
class Weekend:
    race_id: int
    season: int
    round: int
    race_date: date
    era: str | None
    sprint_weekend: bool
    race: list[Result] = field(default_factory=list)
    sprint: list[Result] = field(default_factory=list)

    @property
    def raced(self) -> bool:
        return bool(self.race)


def load_weekends(db: Any, *, start_year: int = 2018, end_year: int = 2100) -> list[Weekend]:
    """Every scheduled weekend (raced or not) with its race and sprint classification."""
    from race_status import is_classified

    weekends = {}
    for rid, season, rnd, when, era, fmt in db.execute(text("""
        SELECT id, season_year, round_number, race_date, regulation_era, COALESCE(weekend_format, 'conventional')
        FROM races WHERE season_year BETWEEN :a AND :b AND race_date IS NOT NULL
        ORDER BY race_date, round_number"""), {"a": start_year, "b": end_year}):
        weekends[int(rid)] = Weekend(int(rid), int(season), int(rnd), when, era, fmt != "conventional")
    for rid, kind, driver, team, grid, finish, status, points in db.execute(text("""
        SELECT s.race_id, s.session_type, re.driver_id, re.team_id, rr.starting_grid_position,
               rr.finishing_position, rr.status, COALESCE(rr.points, 0)
        FROM race_results rr
        JOIN sessions s ON s.id = rr.session_id AND s.session_type IN ('R', 'S')
        JOIN race_entries re ON re.id = rr.race_entry_id
        JOIN races r ON r.id = s.race_id
        WHERE r.season_year BETWEEN :a AND :b"""), {"a": start_year, "b": end_year}):
        w = weekends.get(int(rid))
        if w is None:
            continue
        result = Result(int(driver), int(team), int(grid) if grid and grid > 0 else None,
                        int(finish) if finish else None, is_classified(status), float(points))
        (w.race if kind == "R" else w.sprint).append(result)
    return sorted(weekends.values(), key=lambda w: (w.race_date, w.round))


# --- Plackett-Luce strengths ------------------------------------------------------

@dataclass(frozen=True)
class Strengths:
    car: dict[int, float]
    skill: dict[int, float]

    def of(self, driver_id: int, team_id: int) -> float:
        return self.car.get(team_id, 0.0) + self.skill.get(driver_id, 0.0)


def _orders(weekends: Sequence[Weekend], era: str | None) -> list[tuple[float, int, list[tuple[int, int]]]]:
    """(weight, season, [(driver, team) in finishing order]) for every race/sprint, newest first."""
    out = []
    for age, w in enumerate(reversed(weekends[-MAX_HISTORY:])):
        base = 0.5 ** (age / HALF_LIFE) * (1.0 if (era is None or w.era == era) else OTHER_ERA_WEIGHT)
        for results, factor in ((w.race, 1.0), (w.sprint, SPRINT_WEIGHT)):
            finishers = sorted((r for r in results if r.classified and r.finish), key=lambda r: r.finish)
            if len(finishers) >= 2:
                out.append((base * factor, w.season, [(r.driver_id, r.team_id) for r in finishers]))
    return out


def pl_loglik_grad(s: np.ndarray) -> tuple[float, np.ndarray]:
    """Plackett-Luce log-likelihood of the order s[0] > s[1] > ... and its gradient."""
    lse = np.logaddexp.accumulate(s[::-1])[::-1]          # log sum_{j >= k} exp(s_j)
    loglik = float(np.sum(s - lse))
    grad = 1.0 - np.exp(s) * np.cumsum(np.exp(-lse))
    return loglik, grad


def fit_strengths(weekends: Sequence[Weekend], *, era: str | None = None, winter_sd: float | None = None,
                  season: int | None = None) -> Strengths:
    """MAP car and skill strengths from the given (earlier) weekends.

    winter_sd None: one car strength per team across seasons. Otherwise one per team and
    season (see module doc); the returned car is the one of `season` (the season being
    predicted), or, before that team has raced in it, its latest earlier car."""
    orders = _orders(weekends, era)
    if not orders:
        return Strengths({}, {})
    season_cars = winter_sd is not None
    car_key = (lambda t, y: (t, y)) if season_cars else (lambda t, y: (t, 0))
    cars = sorted({car_key(t, y) for _, y, o in orders for _, t in o})
    drivers = sorted({d for _, _, o in orders for d, _ in o})
    ci = {c: i for i, c in enumerate(cars)}
    di = {d: len(cars) + i for i, d in enumerate(drivers)}
    index = [(w, np.array([ci[car_key(t, y)] for _, t in o]), np.array([di[d] for d, _ in o])) for w, y, o in orders]
    n = len(cars) + len(drivers)
    prior = np.concatenate([np.full(len(cars), 1 / CAR_PRIOR_SD ** 2), np.full(len(drivers), 1 / SKILL_PRIOR_SD ** 2)])
    # season cars: each team's car linked to its previous one (sorted by season) instead of 0
    links = [(ci[a], ci[b]) for a, b in zip(cars, cars[1:]) if season_cars and a[0] == b[0]]
    prev_i = np.array([a for a, _ in links], dtype=int)
    next_i = np.array([b for _, b in links], dtype=int)
    prior[next_i] = 0.0
    link_w = 1 / winter_sd ** 2 if season_cars else 0.0

    def objective(theta):
        value = 0.5 * float(np.sum(prior * theta ** 2))
        grad = prior * theta
        if len(links):
            step = theta[next_i] - theta[prev_i]
            value += 0.5 * link_w * float(np.sum(step ** 2))
            np.add.at(grad, next_i, link_w * step)
            np.add.at(grad, prev_i, -link_w * step)
        for w, t_idx, d_idx in index:
            ll, g = pl_loglik_grad(theta[t_idx] + theta[d_idx])
            value -= w * ll
            np.add.at(grad, t_idx, -w * g)
            np.add.at(grad, d_idx, -w * g)
        return value, grad

    theta = minimize(objective, np.zeros(n), jac=True, method="L-BFGS-B").x
    car: dict[int, float] = {}
    for (t, y) in cars:                       # sorted by team then season: the last kept wins
        if not season_cars or season is None or y <= season:
            car[t] = float(theta[ci[(t, y)]])
    return Strengths(car, {d: float(theta[di[d]]) for d in drivers})


def dnf_rate(weekends: Sequence[Weekend], era: str | None) -> float:
    """Share of race starters not classified, this era's earlier races (all eras if few)."""
    def rate(ws):
        rows = [r for w in ws for r in w.race]
        return (sum(not r.classified for r in rows) + 1) / (len(rows) + 2), len(rows)
    in_era, n = rate([w for w in weekends if w.era == era])
    return in_era if n >= 200 else rate(weekends)[0]


# --- simulation --------------------------------------------------------------------

def simulate_order(strength: np.ndarray, dnf: float, rng: np.random.Generator, sims: int) -> np.ndarray:
    """(sims, n) finishing positions 1..n; retirements placed behind every finisher.
    strength: (n,) or per simulation (sims, n)."""
    n = strength.shape[-1]
    score = (strength if strength.ndim == 2 else strength[None, :]) + rng.gumbel(size=(sims, n))
    retired = rng.random((sims, n)) < dnf
    score = np.where(retired, -1e9 + score, score)
    order = np.argsort(-score, axis=1)
    positions = np.empty_like(order)
    np.put_along_axis(positions, order, np.arange(1, n + 1)[None, :], axis=1)
    return np.where(retired, 0, positions)                 # 0 = did not finish (no points)


def points_for(positions: np.ndarray, table: Sequence[int]) -> np.ndarray:
    lookup = np.zeros(max(len(table), positions.max(initial=0)) + 2)
    lookup[1:len(table) + 1] = table
    return lookup[positions]


@dataclass(frozen=True)
class RaceProbabilities:
    p_win: dict[int, float]
    p_podium: dict[int, float]
    p_points: dict[int, float]

    @property
    def favourite(self) -> int:
        return max(self.p_win, key=self.p_win.get)


def race_probabilities(strengths: Strengths, entrants: Sequence[tuple[int, int, int | None]], *, grid_coef: float,
                       dnf: float, sims: int = SIMS, seed: int = 7) -> RaceProbabilities:
    """entrants: (driver, team, grid slot or None). Strength + grid_coef * -log(grid)."""
    field_size = len(entrants)
    s = np.array([strengths.of(d, t) - grid_coef * np.log(g or field_size) for d, t, g in entrants])
    pos = simulate_order(s, dnf, np.random.default_rng(seed), sims)
    drivers = [d for d, _, _ in entrants]
    finished = pos > 0
    return RaceProbabilities(
        {d: float(np.mean(pos[:, i] == 1)) for i, d in enumerate(drivers)},
        {d: float(np.mean(finished[:, i] & (pos[:, i] <= 3))) for i, d in enumerate(drivers)},
        {d: float(np.mean(finished[:, i] & (pos[:, i] <= 10))) for i, d in enumerate(drivers)},
    )


def fit_grid_coef(weekends: Sequence[Weekend], strengths_for, *, max_races: int = 60) -> float:
    """Grid effect maximising the PL likelihood of the actual orders of the last max_races
    raced weekends, each with strengths fitted only on the weekends before it.
    strengths_for(before, weekend) -> Strengths for that weekend."""
    from scipy.optimize import minimize_scalar

    cases = []
    raced = [w for w in weekends if w.raced]
    for idx in range(max(0, len(raced) - max_races), len(raced)):
        w, before = raced[idx], raced[:idx]
        if len(before) < 5:
            continue
        st = strengths_for(before, w)
        finishers = sorted((r for r in w.race if r.classified and r.finish), key=lambda r: r.finish)
        if len(finishers) < 5:
            continue
        n = len(w.race)
        cases.append((np.array([st.of(r.driver_id, r.team_id) for r in finishers]),
                      np.array([-np.log(r.grid or n) for r in finishers])))
    if not cases:
        return 1.0

    def neg(coef):
        return -sum(pl_loglik_grad(base + coef * g)[0] for base, g in cases)
    return float(minimize_scalar(neg, bounds=(0.0, 5.0), method="bounded").x)


@dataclass(frozen=True)
class ChampionshipProjection:
    p_driver_title: dict[int, float]
    p_team_title: dict[int, float]
    expected_driver_points: dict[int, float]
    expected_team_points: dict[int, float]
    remaining_weekends: int


def standings(weekends: Iterable[Weekend], season: int) -> tuple[dict[int, float], dict[int, float]]:
    """Points so far (race + sprint, as stored) per driver and per team."""
    drivers, teams = defaultdict(float), defaultdict(float)
    for w in weekends:
        if w.season != season:
            continue
        for r in (*w.race, *w.sprint):
            drivers[r.driver_id] += r.points
            teams[r.team_id] += r.points
    return dict(drivers), dict(teams)


def project_championship(strengths: Strengths, lineup: dict[int, int], remaining: Sequence[Weekend], *,
                         driver_points: dict[int, float], team_points: dict[int, float], dnf: float,
                         sims: int = SIMS, seed: int = 11, drift: float = 0.0) -> ChampionshipProjection:
    """Monte Carlo of every remaining weekend for the current lineup (driver -> team).

    drift: car development. Each simulated season every team's car strength takes a random
    walk with this standard deviation per weekend, so a team that is fastest now is not
    assumed fastest until the end."""
    rng = np.random.default_rng(seed)
    # everyone with points stays in the table (a dropped driver keeps theirs); only the
    # current lineup races the remaining weekends
    drivers = sorted(set(lineup) | set(driver_points))
    racing = np.array([i for i, d in enumerate(drivers) if d in lineup])
    teams = sorted(set(lineup.values()) | set(team_points))
    s = np.array([strengths.of(drivers[i], lineup[drivers[i]]) for i in racing])
    car_teams = sorted({lineup[drivers[i]] for i in racing})
    car_of = np.array([car_teams.index(lineup[drivers[i]]) for i in racing], dtype=int)
    development = np.zeros((sims, len(car_teams)))
    pts = np.tile(np.array([driver_points.get(d, 0.0) for d in drivers]), (sims, 1))
    wins = np.zeros_like(pts)
    for w in remaining if len(racing) else ():
        if drift > 0:
            development += rng.normal(0.0, drift, size=development.shape)
        s_now = s[None, :] + development[:, car_of] if drift > 0 else s
        if w.sprint_weekend and sprint_points(w.season):
            pos = simulate_order(s_now, dnf * 0.5, rng, sims)
            pts[:, racing] += points_for(pos, sprint_points(w.season))
        pos = simulate_order(s_now, dnf, rng, sims)
        pts[:, racing] += points_for(pos, RACE_POINTS)
        wins[:, racing] += pos == 1
        if fastest_lap_point(w.season):
            # +1 to one top-10 finisher, drawn by strength (the fastest cars set it most)
            fl = np.where((pos >= 1) & (pos <= 10), np.broadcast_to(s_now, pos.shape) + rng.gumbel(size=pos.shape),
                          -np.inf)
            has = np.isfinite(fl).any(axis=1)
            pts[np.arange(sims)[has], racing[np.argmax(fl[has], axis=1)]] += 1
    # ties: more wins first, then a coin flip (count-back rules beyond wins are rare)
    tiebreak = pts + 1e-3 * wins + 1e-6 * rng.random(pts.shape)
    champion = np.argmax(tiebreak, axis=1)
    team_index = {t: i for i, t in enumerate(teams)}
    team_pts = np.tile(np.array([team_points.get(t, 0.0) for t in teams]), (sims, 1))
    for i in racing:
        d = drivers[i]
        team_pts[:, team_index[lineup[d]]] += pts[:, i] - driver_points.get(d, 0.0)
    team_champion = np.argmax(team_pts + 1e-6 * rng.random(team_pts.shape), axis=1)
    return ChampionshipProjection(
        {d: float(np.mean(champion == i)) for i, d in enumerate(drivers)},
        {t: float(np.mean(team_champion == i)) for i, t in enumerate(teams)},
        {d: float(pts[:, i].mean()) for i, d in enumerate(drivers)},
        {t: float(team_pts[:, i].mean()) for i, t in enumerate(teams)},
        len(remaining),
    )


def current_lineup(weekends: Sequence[Weekend]) -> dict[int, int]:
    """driver -> team from the latest raced weekend (race drivers who started)."""
    last = next((w for w in reversed(weekends) if w.race), None)
    return {r.driver_id: r.team_id for r in last.race} if last else {}


# --- production ------------------------------------------------------------------
# Settings chosen by championship_backtest_v1 (walk-forward, 2018-2025):
#   race winner  season cars, winter 0.3: win log loss 1.322 vs 1.330 shared cars (CI excludes
#                0), nothing worse; vs the grid-slot rates Brier 0.0305 vs 0.0335, log loss
#                1.32 vs 1.53, favourite won 57% vs 52% (152 races)
#   titles       shared cars + drift 0.15 (chosen walk-forward in 6 of 8 seasons). Season cars
#                were worse for constructors (0.851 vs 0.837), so they stay off here.
MODEL_VERSION = "championship_v1"
RACE_WINTER_SD = 0.3
TITLE_WINTER_SD: float | None = None
# Drift test (championship_backtest_v1 --titles-only): choosing 0-0.4 walk-forward was WORSE
# than 0.15 for both titles (log loss +0.028 / +0.027, CIs resampling seasons exclude 0). A
# larger drift only pays in hindsight of 2024; chosen before it, it hurts the other seasons.
TITLE_DRIFT = 0.15
MIN_HISTORY = 10            # raced weekends needed before any strength is fitted
VALIDATION = {
    "source": "championship_backtest_v1: every race and every round of 2018-2025, each predicted "
              "only from the races before it",
    "race_winner": {"races": 152, "favourite_won": 0.572, "brier_win": 0.0305, "brier_podium": 0.0681,
                    "log_loss_win": 1.322, "grid_slot_rates": {"favourite_won": 0.520, "brier_win": 0.0335,
                                                               "brier_podium": 0.0691, "log_loss_win": 1.531}},
    "drivers_title": {"log_loss": 0.428, "points_leader_wins_log_loss": 1.660,
                      "favourite_right_by_quarter": [0.596, 0.976, 0.795, 0.951],
                      "when_we_said_90_plus": {"rounds": 80, "said": 0.982, "happened": 1.0},
                      "champions_chance_moves_per_round": 0.066},
    "constructors_title": {"log_loss": 0.837, "points_leader_wins_log_loss": 1.388,
                           "favourite_right_by_quarter": [0.702, 0.707, 0.864, 1.0],
                           "when_we_said_90_plus": {"rounds": 114, "said": 0.976, "happened": 0.86},
                           "champions_chance_moves_per_round": 0.055},
    "caveat": "Very confident constructors' calls (90%+) came true about 86% of the time: the misses "
              "are 2021 and 2024, when a team's car or second driver changed course mid-season. "
              "Crashes, rain and reliability are chance events; the model gives probabilities, not results.",
}


def _cached_strengths(winter_sd: float | None):
    cache: dict = {}

    def strengths_for(before: Sequence[Weekend], w: Weekend) -> Strengths:
        key = (len(before), w.era, w.season)
        if key not in cache:
            cache[key] = fit_strengths(before, era=w.era, winter_sd=winter_sd, season=w.season)
        return cache[key]
    return strengths_for


def race_win_chances(weekends: Sequence[Weekend], *, race_date: date, era: str | None, season: int,
                     entrants: Sequence[tuple[int, int, int | None]], sims: int = SIMS
                     ) -> tuple[RaceProbabilities, float] | None:
    """Production P(win/podium/points) for one race from the weekends raced before its date,
    or None with too little history. The grid effect is fitted on the races before the
    season started (as in the backtest). Returns (probabilities, grid effect)."""
    before = [w for w in weekends if w.raced and w.race_date < race_date]
    if len(before) < MIN_HISTORY:
        return None
    pre_season = [w for w in before if w.season < season] or before
    grid_coef = fit_grid_coef(pre_season, _cached_strengths(RACE_WINTER_SD))
    st = fit_strengths(before, era=era, winter_sd=RACE_WINTER_SD, season=season)
    return race_probabilities(st, entrants, grid_coef=grid_coef, dnf=dnf_rate(before, era), sims=sims), grid_coef


def season_lineup(done: Sequence[Weekend], cars_per_team: int = 2) -> dict[int, int]:
    """driver -> team for the rest of the season: the latest race's starters, topped up to two
    cars per team with the team's most recent other driver this season. A result missing from
    the data would otherwise leave a team one car short for every remaining race (which made
    2025 Mercedes 0.1% after round 2)."""
    lineup = {r.driver_id: r.team_id for r in done[-1].race}
    count: dict[int, int] = defaultdict(int)
    for team in lineup.values():
        count[team] += 1
    for w in reversed(done[:-1]):
        for r in w.race:
            if r.driver_id not in lineup and count[r.team_id] < cars_per_team:
                lineup[r.driver_id] = r.team_id
                count[r.team_id] += 1
    return lineup


@dataclass(frozen=True)
class SeasonProjection:
    season: int
    after_round: int
    projection: ChampionshipProjection
    driver_points: dict[int, float]
    team_points: dict[int, float]
    lineup: dict[int, int]


def project_season(weekends: Sequence[Weekend], season: int, *, after_round: int | None = None,
                   sims: int | None = None, lineup: dict[int, int] | None = None) -> SeasonProjection:
    """Title chances after `after_round` (default: the latest raced round; 0 = before the
    season) from the races up to that point only, so a past round's projection is what we
    would have said then. Before the season, the cars and drivers come from earlier seasons
    (fit_strengths weighs races under other regulations less), the line-up is `lineup` (the
    first race's entry list when loaded) or last season's final line-up."""
    season_w = [w for w in weekends if w.season == season]
    if not season_w:
        raise ValueError(f"no {season} calendar loaded")
    raced_rounds = [w.round for w in season_w if w.raced]
    if after_round is None:
        after_round = max(raced_rounds) if raced_rounds else 0
    done = [w for w in season_w if w.round <= after_round]
    if after_round > 0 and (not done or not all(w.raced for w in done)):
        raise ValueError(f"{season} round {after_round} has not been raced")
    remaining = [w for w in season_w if w.round > after_round]
    if done:
        history = [w for w in weekends if w.raced and w.race_date <= done[-1].race_date]
    else:
        history = [w for w in weekends if w.raced and w.race_date < season_w[0].race_date]
    if not history or (not done and len(history) < MIN_HISTORY):
        raise ValueError(f"too few races before {season} round {after_round + 1} to project from")
    era = (remaining[0] if remaining else done[-1]).era
    st = fit_strengths(history, era=era, winter_sd=TITLE_WINTER_SD, season=season)
    if lineup is None:
        lineup = season_lineup(done) if done else season_lineup([w for w in history if w.season == history[-1].season])
    d_pts, t_pts = standings(done, season)
    proj = project_championship(st, lineup, remaining, driver_points=d_pts, team_points=t_pts,
                                dnf=dnf_rate(history, era), sims=sims or SIMS, drift=TITLE_DRIFT)
    return SeasonProjection(season, after_round, proj, d_pts, t_pts, lineup)


# --- title confidence: how often the favourite became champion at this stage of a season --

TITLE_CONFIDENCE_FILE = "title_confidence_v1.json"
TITLE_STAGES = (("pre_season", "before the first race"), ("early", "first quarter of the season"),
                ("mid", "second and third quarters"), ("late", "last quarter"))
CONFIDENCE_HIGH, CONFIDENCE_MEDIUM = 0.55, 0.35
# Titles are usually a fight between two or three, not twenty: a favourite right 60% of the
# time is ordinary there. high: the favourite became champion >= 80% of the time, medium >= 55%.
TITLE_HIGH, TITLE_MEDIUM = 0.80, 0.55
TITLE_CHANCE_BINS = ((0.0, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 0.95), (0.95, 1.01))
MIN_TITLE_BIN = 10          # projections needed in a chance bin to use it
LEVELS = ("low", "medium", "high")


def title_stage(after_round: int, season_rounds: int) -> str:
    if after_round == 0:
        return "pre_season"
    share = after_round / max(season_rounds, 1)
    return "early" if share <= 0.25 else "mid" if share <= 0.75 else "late"


def confidence_level(rate: float) -> str:
    return "high" if rate >= CONFIDENCE_HIGH else "medium" if rate >= CONFIDENCE_MEDIUM else "low"


def title_level(rate: float) -> str:
    return "high" if rate >= TITLE_HIGH else "medium" if rate >= TITLE_MEDIUM else "low"


def title_backtest(weekends: Sequence[Weekend], start: int, end: int, *, sims: int = 2000) -> dict:
    """Every completed season start..end projected before every round (production settings,
    races up to that point only); per stage and title: how often the favourite became champion."""
    records: dict[tuple[str, str], list[tuple[float, bool]]] = defaultdict(list)
    for season in range(start, end + 1):
        season_w = [w for w in weekends if w.season == season]
        if not season_w or not all(w.raced for w in season_w):
            continue
        final_d, final_t = standings(season_w, season)
        champs = {"drivers": max(final_d, key=final_d.get), "constructors": max(final_t, key=final_t.get)}
        for k in range(len(season_w)):
            try:
                sp = project_season(weekends, season, after_round=k, sims=sims,
                                    lineup={r.driver_id: r.team_id for r in season_w[k].race} if k == 0 else None)
            except ValueError:
                continue
            stage = title_stage(k, len(season_w))
            for kind, probs in (("drivers", sp.projection.p_driver_title), ("constructors", sp.projection.p_team_title)):
                fav = max(probs, key=probs.get)
                records[(kind, stage)].append((probs[fav], fav == champs[kind]))
    out: dict = {"drivers": {}, "constructors": {}}
    for (kind, stage), rows in records.items():
        out[kind][stage] = {"rounds": len(rows), "favourite_won": round(sum(r[1] for r in rows) / len(rows), 3),
                            "mean_favourite_p": round(sum(r[0] for r in rows) / len(rows), 3)}
    for kind in out:
        rows = [r for (k, _), rs in records.items() if k == kind for r in rs]
        out[kind]["by_chance"] = [
            {"lo": lo, "hi": hi, "rounds": len(sel), "mean_p": round(sum(r[0] for r in sel) / len(sel), 3),
             "favourite_won": round(sum(r[1] for r in sel) / len(sel), 3)}
            for lo, hi in TITLE_CHANCE_BINS for sel in [[r for r in rows if lo <= r[0] < hi]] if sel]
    return out


def monotone_bins(bins: Sequence[dict]) -> list[dict]:
    """A bigger title chance never gets a worse record: adjacent bins that break the order are
    pooled (weighted by projections). With seven seasons one surprise season can otherwise make
    80-95% favourites look less reliable than 60-80% ones."""
    blocks: list[list] = []                  # [rate, weight, member bins]
    for b in sorted(bins, key=lambda b: b["lo"]):
        blocks.append([b["favourite_won"], b["rounds"], [b]])
        while len(blocks) > 1 and blocks[-2][0] > blocks[-1][0]:
            (r2, w2, m2), (r1, w1, m1) = blocks.pop(), blocks.pop()
            blocks.append([(r1 * w1 + r2 * w2) / (w1 + w2), w1 + w2, m1 + m2])
    return [{**b, "favourite_won": round(rate, 3), "favourite_won_raw": b["favourite_won"], "rounds": weight,
             "range_lo": members[0]["lo"], "range_hi": members[-1]["hi"]}
            for rate, weight, members in blocks for b in members]


def title_confidence(kind: str, after_round: int, season_rounds: int, table: dict | None = None,
                     favourite_p: float | None = None) -> dict:
    """Measured record of title favourites in earlier seasons: at this stage, and with a
    similar title chance; the level is the lower of the two, so a close fight is never 'high'
    just because it is mid-season."""
    import json
    from pathlib import Path

    stage = title_stage(after_round, season_rounds)
    if table is None:
        try:
            table = json.loads((Path(__file__).resolve().parent / TITLE_CONFIDENCE_FILE).read_text())["titles"]
        except (OSError, ValueError, KeyError):
            table = {}
    rec = table.get(kind, {}).get(stage)
    label = dict(TITLE_STAGES)[stage]
    if not rec:
        return {"stage": stage, "level": "low" if stage == "pre_season" else "medium", "measured": False,
                "basis": f"{label}: not measured yet (run championship_projection_v1.py --backtest)"}
    out = {"stage": stage, "level": title_level(rec["favourite_won"]), "measured": True,
           "favourite_won_historically": rec["favourite_won"], "rounds": rec["rounds"],
           "basis": f"{label}: in {rec['rounds']} earlier projections at this stage, the favourite became "
                    f"champion {rec['favourite_won']:.0%} of the time"}
    match = next((b for b in monotone_bins(table.get(kind, {}).get("by_chance", []))
                  if favourite_p is not None and b["lo"] <= favourite_p < b["hi"]), None)
    if match and match["rounds"] >= MIN_TITLE_BIN:
        out["chance_basis"] = (f"favourites with a {match['range_lo']:.0%}-{min(match['range_hi'], 1):.0%} title chance "
                               f"became champion {match['favourite_won']:.0%} of the time ({match['rounds']} projections)")
        out["chance_record"] = match["favourite_won"]
        if LEVELS.index(title_level(match["favourite_won"])) < LEVELS.index(out["level"]):
            out["level"] = title_level(match["favourite_won"])
    return out


def store_projection(db: Any, sp: SeasonProjection) -> int:
    """Replace the stored projection for (season, round); returns rows written."""
    from pathlib import Path

    db.execute(text((Path(__file__).resolve().parent / "migration_championship_projections_v1.sql").read_text()))
    db.execute(text("""DELETE FROM championship_projections
                       WHERE season_year = :s AND after_round = :r AND model_version = :v"""),
               {"s": sp.season, "r": sp.after_round, "v": MODEL_VERSION})
    p = sp.projection
    rows = [("drivers", k, prob, sp.driver_points.get(k, 0.0), p.expected_driver_points.get(k, 0.0))
            for k, prob in p.p_driver_title.items()]
    rows += [("constructors", k, prob, sp.team_points.get(k, 0.0), p.expected_team_points.get(k, 0.0))
             for k, prob in p.p_team_title.items()]
    for kind, entity, prob, now, expected in rows:
        db.execute(text("""
            INSERT INTO championship_projections (season_year, after_round, model_version, kind, entity_id,
                                                  p_title, points_now, expected_points, weekends_left)
            VALUES (:s, :r, :v, :k, :e, :p, :now, :exp, :left)"""),
            {"s": sp.season, "r": sp.after_round, "v": MODEL_VERSION, "k": kind, "e": entity,
             "p": round(prob, 4), "now": now, "exp": round(expected, 1), "left": p.remaining_weekends})
    return len(rows)
