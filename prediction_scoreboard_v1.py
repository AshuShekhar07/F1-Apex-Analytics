"""Prediction scoreboard: every finishing-order model scored the same way.

One walk-forward harness for the race forecast, so a change is judged on the same
races and metrics as everything before it, against the same baselines. Nothing
here is tuned on the races it scores.

Models (per race, only data strictly before the race date):
  grid      finish in grid order; win/podium = shrunk grid-slot rates (baseline)
  blend     production (race_forecast_v1.production_transform): grid rank + w * (pace + team
            form rank - grid rank), w chosen on the previous season; pace = quali-implied pace
            with car pace v3 (a driver >1% off the teammate in their last common qualifying
            session, or with no time, gets the teammate's pace + 0.3%) and the practice
            correction (+ beta * long-run surprise, beta fitted on earlier seasons);
            win/podium = grid-slot rates
  candidates  experiments, each scored against the production blend:
    blend_practice_all  practice long-run gap from FP1 AND FP2 (each against its own session's
                        best, averaged), where production uses FP2 else FP1: more drivers covered.
                        OPEN (2026-09-30): every headline point estimate better (3.308 -> 3.289,
                        winner 57.2% -> 58.3%) but no CI excludes 0 yet; re-judged every report
    blend_track   production pace, own blend weight at hard-to-pass tracks (the third of
                  tracks where the grid order held best in earlier seasons; street circuits)
    blend_wetqw   production pace, own blend weight after wet qualifying
    (weights for these groups are chosen on the last 3 seasons' races of the group)
  diagnostic (never promotable): diag_wetrace, own weight for races that turned out wet --
            uses the actual race weather, so it is the most a perfect rain forecast could gain
  promoted: car pace v3 (2026-09-28): 10+ place misses 2.5% -> 2.1%, worse nowhere
            practice correction (2026-09-28): 10+ place misses 2.1% -> 1.8%, worse nowhere
            blend_combo (2026-09-30): team form against car pace + driver form vs teammate +
              sprint-race pace correction; average error 3.347 -> 3.314, finishers 2.749 ->
              2.716, worse nowhere (alone: sprint passed, car form and driver form did not)
            ablation (2026-09-30): the car-pace team form part of blend_combo was dropped --
              without it nothing was worse (top-10 overlap better); car pace, practice, sprint
              and driver form each made something clearly worse when removed; the recovery
              weight is kept on its targeted-group result
            recovery weight (2026-09-30): drivers starting >= 3 places behind their qualifying
              position get their own weight (last 3 seasons); those finishers 4.04 -> 3.54,
              -0.45 [-0.79, -0.12] over 94 races, headline worse nowhere
  rejected: blend_prob re-test (2026-09-30, current model): Brier win -0.0015 [-0.0037,
              +0.0006], podium -0.0022 [-0.0050, +0.0005]; better point estimates, no CI excludes 0
            blend_driver_all (2026-09-30): driver form carried across regulation eras;
              better on nothing (3.315 vs 3.308)
            blend_pass (2026-09-30): positions from overtaking clearly slower cars (pace
              magnitudes instead of ranks); 3.325 vs 3.308, winner 53.3% vs 57.2%, better on
              nothing; its fitted margin flipped between 0% and 0.5% season to season
            blend_wetq (2026-09-30): production, but the car-pace rule skipped in wet
              qualifying; better on nothing, worse on nothing
            blend_track, blend_wetqw (2026-09-30): no clear difference anywhere. Hard-to-pass
              tracks are NOT a weak spot (finishers' error 2.73 there vs 2.76 elsewhere); wet
              qualifying / wet races are (3.15 / 3.12 vs ~2.64), but even diag_wetrace, which
              knows the race was wet, gains nothing (+0.004): the extra wet-race error is
              chaos (crashes, Safety Cars, tyre gambles) that a blend weight cannot predict.
            These candidates run only with --experiments.
  rejected: blend_dnf (team reliability, --reliability): no better than a flat rate, MAE worse
            blend_prob (win/podium from predicted position, --prob): Brier no clear gain
  stored    with --stored: the forecasts stored in race_forecasts (recomputed whenever the
            model changes, incl. the new-era fallbacks the blend rows cannot rebuild)
  live      with --stored: race_forecast_live, each forecast exactly as it stood before its
            race (written only while the race had no results) -- the true live record

Metrics (race-weighted means, 90% CI resampling whole races):
  winner_hit          predicted P1 won
  podium_overlap      share of the real top 3 we named in our top 3
  top10_overlap       share of the real top 10 we named in our top 10
  rank_corr           Spearman correlation of predicted vs actual order
  mae / mae_finishers mean |predicted - actual| position (all / classified only)
  bad_miss_finishers  share of classified finishers off by >= 10 places
  brier_win/podium    mean squared error of the probabilities (0 = perfect)
Plus: breakdowns by era, wet/dry, grid group; probability calibration; worst misses.

    python prediction_scoreboard_v1.py                      # 2018-2026 (2018 from its 3rd race)
    python prediction_scoreboard_v1.py --stored --start-year 2025 --end-year 2026
    python prediction_scoreboard_v1.py --save scoreboard_baseline.json
    python prediction_scoreboard_v1.py --compare scoreboard_baseline.json   # regression gate
"""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Iterable, Sequence

import numpy as np
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from race_form_signal_v1 import (
    DriverForm,
    build_rows,
    choose_weight,
    predictions,
    form_signal,
    race_clustered,
    ranks,
)

HEADLINE = ("winner_hit", "podium_overlap", "top10_overlap", "rank_corr", "mae", "mae_finishers",
            "bad_miss_finishers", "brier_win", "brier_podium")
LOWER_IS_BETTER = {"mae", "mae_finishers", "bad_miss_finishers", "brier_win", "brier_podium"}
# regression gate: a change may not make a headline metric worse by more than this
TOLERANCE = {"winner_hit": 0.03, "podium_overlap": 0.02, "top10_overlap": 0.01, "rank_corr": 0.01,
             "mae": 0.03, "mae_finishers": 0.03, "bad_miss_finishers": 0.01,
             "brier_win": 0.002, "brier_podium": 0.003}
CALIBRATION_BINS = (0.0, 0.02, 0.05, 0.1, 0.2, 0.35, 0.5, 0.7, 1.0001)
BAD_MISS_PLACES = 10


@dataclass(frozen=True)
class Prediction:
    driver_id: int
    rank: int
    p_win: float
    p_podium: float


@dataclass(frozen=True)
class DriverScore:
    race_id: int
    model: str
    driver_id: int
    grid: int
    predicted: int
    finish: int
    classified: bool
    p_win: float
    p_podium: float

    @property
    def error(self) -> int:
        return abs(self.predicted - self.finish)


@dataclass(frozen=True)
class RaceMeta:
    race_id: int
    race_date: date
    season_year: int
    era: str
    wet: bool
    name: str


# --- per-race metrics ----------------------------------------------------------

def _spearman(a: Sequence[int], b: Sequence[int]) -> float:
    ra, rb = np.array(ranks(a), float), np.array(ranks(b), float)
    if ra.std() == 0 or rb.std() == 0:
        return 0.0
    return float(np.corrcoef(ra, rb)[0, 1])


def race_metrics(scores: Sequence[DriverScore]) -> dict[str, float | None]:
    """Headline metrics for one model on one race."""
    by_pred = sorted(scores, key=lambda s: s.predicted)
    actual_top = lambda n: {s.driver_id for s in scores if s.finish <= n}
    pred_top = lambda n: {s.driver_id for s in by_pred[:n]}
    finishers = [s for s in scores if s.classified]
    n10 = min(10, len(scores))
    return {
        "winner_hit": float(by_pred[0].finish == 1),
        "podium_overlap": len(pred_top(3) & actual_top(3)) / 3,
        "top10_overlap": len(pred_top(n10) & actual_top(n10)) / n10,
        "rank_corr": _spearman([s.predicted for s in scores], [s.finish for s in scores]),
        "mae": float(np.mean([s.error for s in scores])),
        "mae_finishers": float(np.mean([s.error for s in finishers])) if finishers else None,
        "bad_miss_finishers": float(np.mean([s.error >= BAD_MISS_PLACES for s in finishers])) if finishers else None,
        "brier_win": float(np.mean([(s.p_win - (s.finish == 1)) ** 2 for s in scores])),
        "brier_podium": float(np.mean([(s.p_podium - (s.finish <= 3)) ** 2 for s in scores])),
    }


class _RaceValue:
    def __init__(self, race_id: int, value: float):
        self.race_id, self.value = race_id, value


def summarise(scores: Sequence[DriverScore], model: str, races: Iterable[int] | None = None) -> dict[str, tuple]:
    """metric -> (mean over races, CI low, CI high) for one model."""
    by_race = defaultdict(list)
    for s in scores:
        if s.model == model and (races is None or s.race_id in races):
            by_race[s.race_id].append(s)
    per_metric = defaultdict(dict)
    for race_id, rows in by_race.items():
        for k, v in race_metrics(rows).items():
            if v is not None:
                per_metric[k][race_id] = [v]
    out = {k: race_clustered(per_metric[k]) for k in HEADLINE if per_metric[k]}
    out["races"] = (len(by_race),)
    out["drivers"] = (sum(len(v) for v in by_race.values()),)
    return out


DUMP_FIELDS = ("race_id", "model", "driver_id", "grid", "predicted", "finish", "classified", "p_win", "p_podium")


def dump_scores(scores: Sequence[DriverScore], path: str) -> None:
    import csv

    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(DUMP_FIELDS)
        for s in scores:
            w.writerow([getattr(s, k) for k in DUMP_FIELDS])


def load_dump(path: str) -> list[DriverScore]:
    import csv

    with open(path) as f:
        return [DriverScore(int(r["race_id"]), r["model"], int(r["driver_id"]), int(r["grid"]), int(r["predicted"]),
                            int(r["finish"]), r["classified"] == "True", float(r["p_win"]), float(r["p_podium"]))
                for r in csv.DictReader(f)]


def paired(scores: Sequence[DriverScore], a: str, b: str, metric: str) -> tuple[float, float, float]:
    """Mean over shared races of metric(a) - metric(b), with race-resampled CI."""
    per = {m: defaultdict(list) for m in (a, b)}
    for s in scores:
        if s.model in per:
            per[s.model][s.race_id].append(s)
    shared = set(per[a]) & set(per[b])
    diffs = {}
    for race_id in shared:
        x, y = race_metrics(per[a][race_id])[metric], race_metrics(per[b][race_id])[metric]
        if x is not None and y is not None:
            diffs[race_id] = [x - y]
    return race_clustered(diffs)


def calibration(scores: Sequence[DriverScore], model: str, field: str) -> list[tuple[str, int, float, float]]:
    """(bin, n, mean predicted, observed rate) for p_win or p_podium."""
    rows = [s for s in scores if s.model == model]
    hit = (lambda s: s.finish == 1) if field == "p_win" else (lambda s: s.finish <= 3)
    out = []
    for lo, hi in zip(CALIBRATION_BINS, CALIBRATION_BINS[1:]):
        b = [s for s in rows if lo <= getattr(s, field) < hi]
        if b:
            out.append((f"{lo:.0%}-{min(hi, 1):.0%}", len(b), float(np.mean([getattr(s, field) for s in b])),
                        float(np.mean([hit(s) for s in b]))))
    return out


def grid_group(grid: int) -> str:
    return "P1-3" if grid <= 3 else "P4-10" if grid <= 10 else "P11+"


# --- models --------------------------------------------------------------------

def slot_rate_function(finishes: Sequence[tuple[date, str, int, int]]) -> Callable[[date, str, int], dict]:
    """(race_date, era, grid, finish) history -> f(as_of, era, field) = {slot: (p_win, p_podium)},
    exactly the production rule (race_forecast_v1.shrunk_slot_rates + era fallback)."""
    from race_forecast_v1 import MIN_ERA_FINISHES, shrunk_slot_rates

    def rates(as_of: date, era: str, field: int) -> dict:
        before = [(g, f, e) for d, e, g, f in finishes if d < as_of]
        all_eras = [(g, f) for g, f, _ in before]
        in_era = [(g, f) for g, f, e in before if e == era]
        return shrunk_slot_rates(in_era if len(in_era) >= MIN_ERA_FINISHES else all_eras, all_eras,
                                 field_size=field)
    return rates


def score_race(race_id: int, model: str, rows: Sequence[DriverForm], preds: Sequence[Prediction]) -> list[DriverScore]:
    by_driver = {p.driver_id: p for p in preds}
    return [DriverScore(race_id, model, r.driver_id, r.grid, by_driver[r.driver_id].rank, r.finish, r.classified,
                        by_driver[r.driver_id].p_win, by_driver[r.driver_id].p_podium)
            for r in rows if r.driver_id in by_driver]


# candidate pace variants: name -> transform applied to a race's rows before the blend
Transform = Callable[[Sequence[DriverForm]], list[DriverForm]]
FITTED_BETAS: dict[int, float] = {}
LAST_SUMMARY: dict = {}          # the last run's headline summary (apex_report re-saves it)


# candidates: name -> factory(rows of seasons before `season`, season) -> pace transform
def _wet_quali_guard(history: Sequence[DriverForm], season: int) -> Transform:
    """Production, but the car-pace rule is skipped when qualifying was wet."""
    from race_forecast_v1 import PRACTICE_METHOD
    from race_form_signal_v1 import practice_beta, with_car_pace_dry_quali, with_practice_correction

    beta = practice_beta(with_car_pace_dry_quali(history)) if PRACTICE_METHOD == "correction" else 0.0

    def transform(rows: Sequence[DriverForm]) -> list[DriverForm]:
        paced = with_car_pace_dry_quali(rows)
        return with_practice_correction(paced, beta) if beta > 0 else paced
    return transform


SPRINT_BETAS: dict[int, float] = {}
RECOVERY_WEIGHTS: dict[int, tuple] = {}    # season -> (out-of-position weight or None, production weight)
PASS_PARAMS: dict[int, tuple] = {}         # season -> (strength, margin) for blend_pass

def _production_with(field_from: str, field_to: str) -> Callable[[Sequence[DriverForm], int], Transform]:
    """Candidate: production, with one input replaced by a research version (betas refitted)."""
    def factory(history: Sequence[DriverForm], season: int) -> Transform:
        from dataclasses import replace

        from race_forecast_v1 import production_transform

        def swap(rows):
            return [replace(r, **{field_to: getattr(r, field_from)}) for r in rows]
        production = production_transform(swap(history))
        return lambda rows: production(swap(rows))
    return factory


CANDIDATES: dict[str, Callable[[Sequence[DriverForm], int], Transform]] = {
    "blend_practice_all": _production_with("practice_gap_all", "practice_gap"),
    "blend_driver_all": _production_with("driver_form_all", "driver_form"),
    "blend_wetq": _wet_quali_guard,
}
# shown only with --experiments (kept so the verdicts can be re-checked on new data)
REJECTED = {"blend_wetq", "blend_track", "blend_wetqw", "diag_wetrace", "blend_pass", "blend_driver_all"}


# --- per-race blend weight candidates --------------------------------------------
# Production uses one blend weight per season. These candidates keep the production pace
# and change the weight only for one group of races, chosen on earlier seasons' races of
# the same group (the groups are small, so GROUP_SEASONS seasons rather than one).
HARD_TO_PASS_SHARE = 1 / 3   # the third of tracks where the starting order holds best
MIN_TRACK_RACES = 2          # a track needs this many earlier races to be rated
MIN_GROUP_RACES = 5          # fewer earlier races in the group -> production weight
GROUP_SEASONS = 3
GROUP_WEIGHTS: dict[str, dict[int, tuple]] = defaultdict(dict)   # name -> season -> (group weight, production weight)
HARD_TRACKS: dict[int, list[str]] = {}         # season -> hard-to-pass tracks rated before it
WET_QUALI_RACES: set[int] = set()


def track_hold(rows: Sequence[DriverForm], meta: dict[int, RaceMeta]) -> dict[str, float]:
    """track name -> mean Spearman(grid, finish) over classified finishers of the given
    races; only tracks with MIN_TRACK_RACES races. High = the starting order holds."""
    by_race = defaultdict(list)
    for r in rows:
        if r.classified:
            by_race[r.race_id].append(r)
    by_track = defaultdict(list)
    for race_id, rs in by_race.items():
        if len(rs) >= 5 and race_id in meta:
            by_track[meta[race_id].name].append(_spearman([r.grid for r in rs], [r.finish for r in rs]))
    return {t: float(np.mean(v)) for t, v in by_track.items() if len(v) >= MIN_TRACK_RACES}


def hard_to_pass_tracks(hold: dict[str, float]) -> set[str]:
    if len(hold) < 6:
        return set()
    cut = float(np.quantile(list(hold.values()), 1 - HARD_TO_PASS_SHARE))
    return {t for t, v in hold.items() if v >= cut}


GroupTest = Callable[[int, Sequence[DriverForm]], bool]
RaceWeight = Callable[[int, Sequence[DriverForm]], float]


def group_weight(history: Sequence[DriverForm], season: int, transform: Transform, in_group: GroupTest,
                 fallback: float) -> float:
    races = defaultdict(list)
    for r in history:
        if season - GROUP_SEASONS <= r.season_year < season:
            races[r.race_id].append(r)
    races = {k: v for k, v in races.items() if in_group(k, v)}
    if len(races) < MIN_GROUP_RACES:
        return fallback
    return choose_weight({k: transform(v) for k, v in races.items()})


def _grouped(name: str, in_group: GroupTest) -> Callable[..., RaceWeight]:
    def factory(history, season, meta, transform, weight) -> RaceWeight:
        w = group_weight(history, season, transform, in_group, weight)
        GROUP_WEIGHTS[name][season] = (w, weight)
        return lambda race_id, rows: w if in_group(race_id, rows) else weight
    return factory


def _track_weight(history, season, meta, transform, weight) -> RaceWeight:
    """Separate blend weight at hard-to-pass tracks (street circuits and the like), rated
    from earlier seasons' results only: where the grid order usually holds, pace should
    move the prediction less."""
    hard = hard_to_pass_tracks(track_hold(history, meta))
    HARD_TRACKS[season] = sorted(hard)
    return _grouped("blend_track", lambda race_id, rows: meta[race_id].name in hard)(
        history, season, meta, transform, weight)


def _wet_quali_weight(history, season, meta, transform, weight) -> RaceWeight:
    """Separate blend weight after wet qualifying (known before the race): the grid is
    shuffled by conditions, so pace and team form may deserve more weight."""
    return _grouped("blend_wetqw", lambda race_id, rows: any(r.quali_wet for r in rows))(
        history, season, meta, transform, weight)


def _wet_race_oracle(history, season, meta, transform, weight) -> RaceWeight:
    """DIAGNOSTIC ONLY, never promotable: separate weight for races that turned out wet.
    Uses the actual race weather, which is not known when the forecast is made; it shows
    the most a perfect rain forecast could gain."""
    return _grouped("diag_wetrace", lambda race_id, rows: meta[race_id].wet)(
        history, season, meta, transform, weight)


WEIGHT_CANDIDATES: dict[str, Callable[..., RaceWeight]] = {
    "blend_track": _track_weight,
    "blend_wetqw": _wet_quali_weight,
}
DIAGNOSTICS: dict[str, Callable[..., RaceWeight]] = {
    "diag_wetrace": _wet_race_oracle,
}


# post-blend candidates: name -> f(race_id, race rows, production blend ranks) -> ranks
Adjuster = Callable[[int, Sequence[DriverForm], list[int]], list[int]]


def model_scores(rows: Sequence[DriverForm], meta: dict[int, RaceMeta], rates, *, start_year: int,
                 adjusters: dict[str, Adjuster] | None = None, ablation: bool = False) -> list[DriverScore]:
    """grid, blend and candidate predictions for every race from start_year.

    Each blend variant chooses its own weight on the previous season (with the same
    variant applied), so a candidate never borrows the production weight."""
    by_season = defaultdict(lambda: defaultdict(list))
    for r in rows:
        by_season[r.season_year][r.race_id].append(r)
    from race_forecast_v1 import production_transform

    all_rows = list(rows)
    WET_QUALI_RACES.update(r.race_id for r in all_rows if r.quali_wet)
    out: list[DriverScore] = []
    for season in sorted(by_season):
        if season < start_year:
            continue
        previous = by_season.get(season - 1)
        history = [r for r in all_rows if r.season_year < season]
        production = production_transform(history)
        FITTED_BETAS[season] = production.beta
        SPRINT_BETAS[season] = production.sprint_beta
        variants = {"blend": production,
                    **{name: factory(history, season) for name, factory in CANDIDATES.items()}}
        weights = {name: choose_weight({k: transform(v) for k, v in previous.items()}) if previous else 0.5
                   for name, transform in variants.items()}
        race_weight = {name: (lambda race_id, rs, w=w: w) for name, w in weights.items()}
        for name, factory in {**WEIGHT_CANDIDATES, **DIAGNOSTICS}.items():
            variants[name] = production
            race_weight[name] = factory(history, season, meta, production, weights["blend"])
        from race_forecast_v1 import ABLATABLE, production_recovery_weight
        recovery = {"blend": production_recovery_weight(history, season, production, weights["blend"])}
        if ablation:     # production with one part switched off, weights refitted without it
            for part in ABLATABLE:
                name = f"without_{part}"
                variants[name] = production_transform(history, frozenset({part}))
                weights[name] = (choose_weight({k: variants[name](v) for k, v in previous.items()})
                                 if previous else 0.5)
                race_weight[name] = (lambda race_id, rs, w=weights[name]: w)
                recovery[name] = production_recovery_weight(history, season, variants[name], weights[name])
            variants["without_recovery"] = production
            race_weight["without_recovery"] = race_weight["blend"]
        RECOVERY_WEIGHTS[season] = (recovery["blend"], weights["blend"])
        from race_form_signal_v1 import choose_pass_params, pass_predictions
        pass_params = choose_pass_params({k: production(v) for k, v in previous.items()}) if previous else (0.5, 0.002)
        PASS_PARAMS[season] = pass_params
        for race_id, race_rows in by_season[season].items():
            m = meta[race_id]
            slot = rates(m.race_date, m.era, len(race_rows))
            ranked = {"grid": predictions(race_rows, 0.0)["grid"]}
            for name, transform in variants.items():
                ranked[name] = predictions(transform(race_rows), race_weight[name](race_id, race_rows),
                                           recovery.get(name))["blend"]
            ranked["blend_pass"] = pass_predictions(production(race_rows), *pass_params)
            for name, adjust in (adjusters or {}).items():
                ranked[name] = adjust(race_id, race_rows, ranked["blend"])
            for model, order in ranked.items():
                preds = [Prediction(r.driver_id, rank, *slot.get(r.grid, (0.0, 0.0)))
                         for r, rank in zip(race_rows, order)]
                out += score_race(race_id, model, race_rows, preds)
    return out


def stored_scores(db: Any, meta: dict[int, RaceMeta], truth: dict[int, list[DriverForm]]) -> list[DriverScore]:
    from race_forecast_v1 import MODEL_VERSION

    rows = db.execute(text("""
        SELECT f.race_id, fd.driver_id, fd.predicted_position, fd.p_win, fd.p_podium
        FROM race_forecasts f JOIN race_forecast_drivers fd ON fd.forecast_id = f.id
        WHERE f.model_version = :v
    """), {"v": MODEL_VERSION}).all()
    by_race = defaultdict(list)
    for race_id, driver_id, pos, p_win, p_pod in rows:
        by_race[race_id].append(Prediction(int(driver_id), int(pos), float(p_win), float(p_pod)))
    out = []
    for race_id, preds in by_race.items():
        if race_id in truth and race_id in meta:
            out += score_race(race_id, "stored", truth[race_id], preds)
    return out


def live_scores(db: Any, meta: dict[int, RaceMeta], truth: dict[int, list[DriverForm]]) -> list[DriverScore]:
    """Forecasts exactly as they stood before each race (race_forecast_live), never recomputed."""
    from race_forecast_v1 import MODEL_VERSION

    if not db.execute(text("SELECT to_regclass('race_forecast_live') IS NOT NULL")).scalar():
        return []
    by_race = defaultdict(list)
    for race_id, driver_id, pos, p_win, p_pod in db.execute(text("""
        SELECT race_id, driver_id, predicted_position, p_win, p_podium FROM race_forecast_live
        WHERE model_version = :v"""), {"v": MODEL_VERSION}):
        by_race[race_id].append(Prediction(int(driver_id), int(pos), float(p_win), float(p_pod)))
    out = []
    for race_id, preds in by_race.items():
        if race_id in truth and race_id in meta:
            out += score_race(race_id, "live", truth[race_id], preds)
    return out


# --- database ------------------------------------------------------------------

def load_meta(db: Any) -> dict[int, RaceMeta]:
    from race_pace_from_quali_v1 import SESSION_WET_SQL

    rows = db.execute(text(f"""
        SELECT r.id, r.race_date, r.season_year, r.regulation_era, {SESSION_WET_SQL}, t.name
        FROM races r JOIN tracks t ON t.id = r.track_id
        JOIN sessions s ON s.race_id = r.id AND s.session_type = 'R'
        LEFT JOIN session_weather sw ON sw.session_id = s.id
        WHERE r.regulation_era IS NOT NULL
    """)).all()
    return {int(i): RaceMeta(int(i), d, int(y), e, bool(w), n) for i, d, y, e, w, n in rows}


def load_truth(db: Any) -> dict[int, list[DriverForm]]:
    """Actual results for every race (for scoring stored forecasts, incl. races the blend skips)."""
    from race_status import is_classified

    rows = db.execute(text("""
        SELECT s.race_id, r.season_year, re.driver_id, rr.starting_grid_position, rr.finishing_position, rr.status
        FROM race_results rr
        JOIN sessions s ON s.id = rr.session_id AND s.session_type = 'R'
        JOIN races r ON r.id = s.race_id
        JOIN race_entries re ON re.id = rr.race_entry_id
        WHERE rr.finishing_position IS NOT NULL
    """)).all()
    out = defaultdict(list)
    for race_id, season, driver, grid, finish, status in rows:
        out[int(race_id)].append(DriverForm(int(race_id), int(season), int(driver), int(grid or 99), int(finish),
                                            is_classified(status), 0.0, 0.0, None))
    return out


def load_finishes(db: Any) -> list[tuple[date, str, int, int]]:
    rows = db.execute(text("""
        SELECT r.race_date, r.regulation_era, rr.starting_grid_position, rr.finishing_position
        FROM race_results rr
        JOIN sessions s ON s.id = rr.session_id AND s.session_type = 'R'
        JOIN races r ON r.id = s.race_id
        WHERE rr.starting_grid_position > 0 AND rr.finishing_position IS NOT NULL
    """)).all()
    return [(d, e, int(g), int(f)) for d, e, g, f in rows]


def driver_names(db: Any) -> dict[int, str]:
    return {int(i): n for i, n in db.execute(text("SELECT id, name FROM drivers")).all()}


# --- report --------------------------------------------------------------------

def _fmt(metric: str, v: float) -> str:
    return f"{v:.1%}" if metric in ("winner_hit", "podium_overlap", "top10_overlap", "bad_miss_finishers") else f"{v:.3f}"


def report(scores: Sequence[DriverScore], meta: dict[int, RaceMeta], names: dict[int, str], models: Sequence[str]) -> dict:
    summary = {m: summarise(scores, m) for m in models}
    print(f"\n=== HEADLINE (race-weighted, 90% CI over races) ===")
    print(f"{'metric':20}" + "".join(f"{m:>26}" for m in models) + "   better")
    for metric in HEADLINE:
        cells = []
        for m in models:
            v = summary[m].get(metric)
            cells.append(f"{_fmt(metric, v[0])} [{_fmt(metric, v[1])},{_fmt(metric, v[2])}]" if v else "-")
        print(f"{metric:20}" + "".join(f"{c:>26}" for c in cells) + ("   lower" if metric in LOWER_IS_BETTER else "   higher"))
    print("races / drivers     " + "".join(f"{summary[m]['races'][0]:>17} / {summary[m]['drivers'][0]:<6}" for m in models))

    if "grid" in models:
        print("\n=== vs GRID BASELINE (same races; CI excluding 0 = a real difference) ===")
        for m in models:
            if m == "grid":
                continue
            for metric in HEADLINE:
                d, lo, hi = paired(scores, m, "grid", metric)
                if d != d:
                    continue
                better = (hi < 0) if metric in LOWER_IS_BETTER else (lo > 0)
                worse = (lo > 0) if metric in LOWER_IS_BETTER else (hi < 0)
                print(f"{m:8} {metric:20} {d:+.4f} [{lo:+.4f}, {hi:+.4f}]  "
                      f"{'BETTER' if better else 'WORSE' if worse else 'no clear difference'}")

    candidates = [m for m in models if m not in ("grid", "blend", "stored", "live") and not m.startswith("diag_")]
    if candidates:
        print("\n=== CANDIDATES vs PRODUCTION BLEND (promote only if BETTER somewhere and WORSE nowhere) ===")
        for m in candidates:
            for metric in HEADLINE:
                d, lo, hi = paired(scores, m, "blend", metric)
                if d != d:
                    continue
                better = (hi < 0) if metric in LOWER_IS_BETTER else (lo > 0)
                worse = (lo > 0) if metric in LOWER_IS_BETTER else (hi < 0)
                print(f"{m:10} {metric:20} {d:+.4f} [{lo:+.4f}, {hi:+.4f}]  "
                      f"{'BETTER' if better else 'WORSE' if worse else 'no clear difference'}")
        for group in ("P1-3", "P4-10", "P11+"):
            cells = []
            for m in ["blend", *candidates]:
                rows = [s for s in scores if s.model == m and s.classified and grid_group(s.grid) == group]
                cells.append(f"{m} {np.mean([s.error for s in rows]):.2f}" if rows else f"{m} -")
            print(f"  finishers started {group:6} " + "   ".join(cells))

    main = "blend"
    print(f"\n=== BREAKDOWN ({main}): mae_finishers / podium_overlap / races ===")
    groups: dict[str, set[int]] = defaultdict(set)
    for s in scores:
        if s.model == main:
            m = meta[s.race_id]
            groups[f"era {m.era}"].add(s.race_id)
            groups["wet" if m.wet else "dry"].add(s.race_id)
            groups[f"season {m.season_year}"].add(s.race_id)
    for g in sorted(groups):
        sm = summarise(scores, main, groups[g])
        print(f"  {g:32} {sm['mae_finishers'][0]:.2f} places   {sm['podium_overlap'][0]:.0%}   {sm['races'][0]}")
    for group in ("P1-3", "P4-10", "P11+"):
        rows = [s for s in scores if s.model == main and s.classified and grid_group(s.grid) == group]
        if rows:
            print(f"  started {group:24} {np.mean([s.error for s in rows]):.2f} places   (finishers, n={len(rows)})")
    dnf = [s for s in scores if s.model == main and not s.classified]
    if dnf:
        print(f"  retirements                      {np.mean([s.error for s in dnf]):.2f} places   (n={len(dnf)}, "
              f"{len(dnf) / max(1, sum(s.model == main for s in scores)):.0%} of drivers)")

    for model in [main] + [m for m in models if m == "blend_prob"]:
        for field in ("p_win", "p_podium"):
            print(f"\n=== CALIBRATION {field} ({model}): when we said X, it happened Y ===")
            for label, n, predicted, observed in calibration(scores, model, field):
                flag = "  <- off" if n >= 30 and abs(predicted - observed) > max(0.05, 0.5 * predicted) else ""
                print(f"  {label:>10}  n={n:5}  said {predicted:6.1%}  happened {observed:6.1%}{flag}")

    print(f"\n=== WORST MISSES ({main}, classified finishers) ===")
    worst = sorted((s for s in scores if s.model == main and s.classified), key=lambda s: -s.error)[:12]
    for s in worst:
        m = meta[s.race_id]
        print(f"  {m.season_year} {m.name:22} {names.get(s.driver_id, s.driver_id):22} grid {s.grid:2}  "
              f"predicted P{s.predicted:<2} finished P{s.finish}")
    return {m: {k: list(v) for k, v in summary[m].items()} for m in models}


def groups_report(scores: Sequence[DriverScore], meta: dict[int, RaceMeta]) -> None:
    """Hard-to-pass tracks and wet races: where production is weakest, and whether the
    group-weight candidates help there (finishers' average error, same races)."""
    races = {s.race_id for s in scores if s.model == "blend"}
    last = max(HARD_TRACKS) if HARD_TRACKS else None
    hard = {r for r in races if meta[r].name in set(HARD_TRACKS.get(meta[r].season_year, []))}
    wet_quali = {r for r in races if r in WET_QUALI_RACES}
    wet = {r for r in races if meta[r].wet}
    print("\n=== HARD-TO-PASS TRACKS AND WET RACES (finishers' average error, places) ===")
    if last:
        print(f"  hard-to-pass tracks for {last} (grid order held best before it): "
              + (", ".join(HARD_TRACKS[last]) or "none rated yet"))
    for label, group, model in (("hard-to-pass tracks", hard, "blend_track"),
                                ("wet qualifying", wet_quali, "blend_wetqw"),
                                ("wet race (diagnostic)", wet, "diag_wetrace")):
        if not group:
            continue
        rest = races - group
        prod, other = summarise(scores, "blend", group), summarise(scores, "grid", group)
        d, lo, hi = paired([s for s in scores if s.race_id in group], model, "blend", "mae_finishers")
        verdict = "BETTER" if hi < 0 else "WORSE" if lo > 0 else "no clear difference"
        print(f"  {label:22} {len(group):3} races: production {prod['mae_finishers'][0]:.2f}, grid "
              f"{other['mae_finishers'][0]:.2f}, rest of races {summarise(scores, 'blend', rest)['mae_finishers'][0]:.2f}"
              f" | {model} {d:+.3f} [{lo:+.3f}, {hi:+.3f}] {verdict}")
    for name, by_season in GROUP_WEIGHTS.items():
        print(f"  {name} weight (group / production) per season: "
              + ", ".join(f"{y}: {g:.1f}/{p:.1f}" for y, (g, p) in sorted(by_season.items())))


def load_error_facts(db: Any) -> dict[tuple[int, int], tuple[int | None, int | None]]:
    """(race_id, driver_id) -> (qualifying position, race position after lap 1)."""
    rows = db.execute(text("""
        SELECT sq.race_id, re.driver_id, q.final_position,
               (SELECT l.position FROM laps l JOIN sessions sr ON sr.id = l.session_id AND sr.session_type = 'R'
                WHERE sr.race_id = sq.race_id AND l.race_entry_id = re.id AND l.lap_number = 1 LIMIT 1)
        FROM qualifying_results q
        JOIN sessions sq ON sq.id = q.session_id AND sq.session_type = 'Q'
        JOIN race_entries re ON re.id = q.race_entry_id
    """)).all()
    return {(int(r), int(d)): (int(qp) if qp else None, int(l1) if l1 else None) for r, d, qp, l1 in rows}


PENALTY_PLACES = 3        # starting this many places behind the qualifying position = grid penalty
LAP1_CHAOS_PLACES = 5     # gaining or losing this many places on lap 1 = start / first-lap incident


def error_categories(scores: Sequence[DriverScore], meta: dict[int, RaceMeta],
                     facts: dict[tuple[int, int], tuple]) -> list[tuple[str, int, float, float]]:
    """(category, drivers, share of all error, mean error) for the production blend; each
    driver counted once, in this order: retired, grid penalty / pit-lane start, big lap-1
    change, wet race, everything else."""
    rows = [s for s in scores if s.model == "blend"]
    total = sum(s.error for s in rows) or 1
    buckets = defaultdict(list)
    for s in rows:
        quali_pos, lap1 = facts.get((s.race_id, s.driver_id), (None, None))
        if not s.classified:
            key = "retired (not classified)"
        elif quali_pos is not None and s.grid - quali_pos >= PENALTY_PLACES:
            key = f"grid penalty / pit-lane start (>= {PENALTY_PLACES} places back)"
        elif lap1 is not None and abs(lap1 - s.grid) >= LAP1_CHAOS_PLACES:
            key = f"lap 1 changed position by >= {LAP1_CHAOS_PLACES}"
        elif meta[s.race_id].wet:
            key = "wet race"
        else:
            key = "everything else"
        buckets[key].append(s.error)
    return [(k, len(v), sum(v) / total, float(np.mean(v))) for k, v in buckets.items()]


def error_report(scores: Sequence[DriverScore], meta: dict[int, RaceMeta], facts: dict) -> None:
    print("\n=== WHERE THE ERROR COMES FROM (production blend; each driver in one group) ===")
    order = ["retired", "grid penalty", "lap 1", "wet race", "everything else"]
    for key, n, share, avg in sorted(error_categories(scores, meta, facts),
                                     key=lambda r: next(i for i, o in enumerate(order) if r[0].startswith(o))):
        print(f"  {key:52} {n:5} drivers  {share:6.1%} of all error  avg {avg:.2f} places")
    # hindsight: how good is the order after lap 1? (a ceiling for anything decided before the start)
    lap1 = [abs(facts[(s.race_id, s.driver_id)][1] - s.finish) for s in scores
            if s.model == "blend" and s.classified and facts.get((s.race_id, s.driver_id), (None, None))[1]]
    if lap1:
        print(f"  hindsight: finishers' error if we knew the order after lap 1 = {np.mean(lap1):.2f} places "
              f"(production before the start: {np.mean([s.error for s in scores if s.model == 'blend' and s.classified]):.2f})")


def signals_report(scored: Sequence[DriverForm]) -> None:
    """Does each research input carry information about the race-vs-qualifying residual?
    Correlation with a race-resampled CI; the candidates above say whether it also helps
    the finishing order."""
    from dataclasses import replace as _replace

    def line(label, rows):
        sig, lo, hi, n = form_signal(rows)
        verdict = "REAL signal" if lo > 0 else "no clear signal"
        print(f"  {label:44} r={sig:+.3f} [{lo:+.3f}, {hi:+.3f}] n={n:5}  {verdict}")

    print("\n=== NEW SIGNALS (correlation with the race-vs-qualifying residual, 90% CI over races) ===")
    line("team form (production)", scored)
    line("team form from car-pace residuals", [_replace(r, form=r.form_car) for r in scored if r.form_car is not None])
    line("driver form vs teammate", [_replace(r, form=r.driver_form) for r in scored if r.driver_form is not None])
    sprint = [_replace(r, form=r.sprint_gap - r.implied_gap) for r in scored if r.sprint_gap is not None]
    line("sprint-race pace vs qualifying pace", sprint)
    races = {r.race_id for r in scored if r.sprint_gap is not None}
    print(f"  sprint pace: {len(sprint)} drivers in {len(races)} sprint weekends; beta per season: "
          + (", ".join(f"{k}: {v:.2f}" for k, v in sorted(SPRINT_BETAS.items())) or "-"))


def confidence_report(scores: Sequence[DriverScore], meta: dict[int, RaceMeta]) -> dict:
    """Walk-forward check of the forecast's confidence label (race_confidence_v1) on the
    production blend: ranges fitted on earlier seasons, tested on the next one."""
    from race_confidence_v1 import COVERAGE, LABELS, ErrorSample, coverage_check

    samples = [ErrorSample(s.race_id, meta[s.race_id].season_year, s.predicted, s.finish,
                           s.race_id in WET_QUALI_RACES, meta[s.race_id].wet)
               for s in scores if s.model == "blend"]
    check = coverage_check(samples)
    print(f"\n=== CONFIDENCE LABEL (walk-forward; ranges should contain {COVERAGE:.0%} of drivers) ===")
    for name in ("all", "dry_quali", "wet_quali", "rain_forecast"):
        if name in check:
            n, inside, said, actual = check[name]
            ok = abs(inside - COVERAGE) <= 0.05 and abs(said - actual) <= 0.35
            print(f"  {LABELS.get(name, 'all races'):28} n={n:5}  inside range {inside:6.1%}  "
                  f"said {said:.2f} places  actual {actual:.2f}  {'ok' if ok else 'OFF'}")
    print("  (rain forecast rows use the actual race weather as a perfect forecast)")
    return check


def compare(current: dict, baseline: dict) -> list[str]:
    """Headline metrics that got worse than the saved baseline by more than TOLERANCE."""
    problems = []
    for model, metrics in current.items():
        old = baseline.get(model, {})
        for metric in HEADLINE:
            if metric not in metrics or metric not in old:
                continue
            delta = metrics[metric][0] - old[metric][0]
            worse = delta > TOLERANCE[metric] if metric in LOWER_IS_BETTER else -delta > TOLERANCE[metric]
            print(f"  {model:8} {metric:20} {_fmt(metric, old[metric][0]):>8} -> {_fmt(metric, metrics[metric][0]):>8}"
                  f"{'   WORSE' if worse else ''}")
            if worse:
                problems.append(f"{model}.{metric}")
    return problems


def reliability_adjuster(starts, meta: dict[int, RaceMeta]) -> tuple[Adjuster, dict]:
    """blend_dnf candidate, plus the per-driver p_dnf it used (for the signal check)."""
    from race_reliability_v1 import expected_position_ranks, race_probabilities

    used: dict[tuple[int, int], float] = {}

    def adjust(race_id: int, rows: Sequence[DriverForm], blend_ranks: list[int]) -> list[int]:
        probs = race_probabilities(starts, meta[race_id].race_date, [r.team_id for r in rows])
        p = [probs[r.team_id] for r in rows]
        for r, q in zip(rows, p):
            used[(race_id, r.driver_id)] = q
        return expected_position_ranks(blend_ranks, p)
    return adjust, used


def reliability_signal(used: dict, scores: Sequence[DriverScore]) -> tuple[float, float, float, float, int]:
    """Brier of team p_dnf vs a flat rate (the scored races' own mean) for 'did not finish'.
    Negative difference with CI below 0 = team reliability really predicts retirements."""
    rows = [(s.race_id, used[(s.race_id, s.driver_id)], not s.classified)
            for s in scores if s.model == "blend" and (s.race_id, s.driver_id) in used]
    if not rows:
        return (float("nan"),) * 4 + (0,)
    flat = float(np.mean([d for _, _, d in rows]))
    per_race = defaultdict(list)
    for race_id, p, dnf in rows:
        per_race[race_id].append((p - dnf) ** 2 - (flat - dnf) ** 2)
    d, lo, hi = race_clustered(per_race)
    return float(np.mean([(p - dnf) ** 2 for _, p, dnf in rows])), d, lo, hi, len(rows)


def rank_probability_scores(scores: Sequence[DriverScore], meta: dict[int, RaceMeta], rates,
                            *, k: float = 10.0, source: str = "blend", name: str = "blend_prob") -> list[DriverScore]:
    """Candidate probabilities: win/podium rate of each PREDICTED position, from earlier races
    only (walk-forward by date), shrunk toward the grid-slot rate of the same number."""
    from dataclasses import replace

    from race_forecast_v1 import non_increasing

    by_race = defaultdict(list)
    for s in scores:
        if s.model == source:
            by_race[s.race_id].append(s)
    history = defaultdict(lambda: [0, 0, 0])          # predicted position -> starts, wins, podiums
    out = []
    for race_id in sorted(by_race, key=lambda r: (meta[r].race_date, r)):
        rows = by_race[race_id]
        m = meta[race_id]
        prior = rates(m.race_date, m.era, len(rows))
        positions = range(1, len(rows) + 1)
        wins, pods, weights = [], [], []
        for p in positions:
            n, w, pod = history[p]
            prior_win, prior_pod = prior.get(p, (0.0, 0.0))
            wins.append((w + k * prior_win) / (n + k))
            pods.append((pod + k * prior_pod) / (n + k))
            weights.append(n + k)
        wins, pods = non_increasing(wins, weights), non_increasing(pods, weights)
        for s in rows:
            out.append(replace(s, model=name, p_win=wins[s.predicted - 1], p_podium=pods[s.predicted - 1]))
        for s in rows:                                 # update only after this race is predicted
            h = history[s.predicted]
            h[0] += 1
            h[1] += int(s.finish == 1)
            h[2] += int(s.finish <= 3)
    return out


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start-year", type=int, default=2018,
                        help="2018 is scored from the first race with enough earlier 2018 races to fit")
    parser.add_argument("--end-year", type=int, default=2026)
    parser.add_argument("--stored", action="store_true", help="also score the stored forecasts (live record)")
    parser.add_argument("--reliability", action="store_true", help="also run the rejected blend_dnf experiment")
    parser.add_argument("--prob", action="store_true", help="also run the rejected blend_prob experiment")
    parser.add_argument("--ablation", action="store_true",
                        help="also score production with each part switched off (does every part still help?)")
    parser.add_argument("--experiments", action="store_true",
                        help="also show the rejected pace/weight candidates (blend_wetq, blend_track, ...)")
    parser.add_argument("--save", help="write the headline summary to this JSON file")
    parser.add_argument("--compare", help="fail (exit 1) if worse than this saved summary")
    parser.add_argument("--dump", help="write every driver score of grid and blend to this CSV "
                                       "(scoreboard_compare_v1.py compares two runs race by race)")
    args = parser.parse_args()

    from race_pace_from_quali_v1 import load_observations

    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as db:
        meta = load_meta(db)
        rates = slot_rate_function(load_finishes(db))
        observations = load_observations(db, start_year=2018, end_year=args.end_year)
        rows = build_rows(db, observations, start_year=args.start_year - 1, end_year=args.end_year, include_wet=True,
                          with_practice=True, with_sprint=True)
        from race_reliability_v1 import load_starts

        adjust_dnf, used_p = reliability_adjuster(load_starts(db), meta)
        adjusters = {"blend_dnf": adjust_dnf} if args.reliability else {}
        scores = model_scores(rows, meta, rates, start_year=args.start_year, adjusters=adjusters,
                              ablation=args.ablation)
        prob_models = []
        if args.prob:
            scores += rank_probability_scores(scores, meta, rates)
            prob_models = ["blend_prob"]
        experiments = [m for m in (*CANDIDATES, *WEIGHT_CANDIDATES, *DIAGNOSTICS, "blend_pass")
                       if args.experiments or m not in REJECTED]
        if args.ablation:
            from race_forecast_v1 import ABLATABLE
            experiments += [f"without_{p}" for p in (*ABLATABLE, "recovery")]
        scores = [s for s in scores if s.model in ("grid", "blend", *experiments, *prob_models, *adjusters)]
        models = ["grid", "blend", *experiments, *prob_models, *adjusters]
        if args.stored:
            truth = load_truth(db)
            scores += [s for s in stored_scores(db, meta, truth)
                       if args.start_year <= meta[s.race_id].season_year <= args.end_year]
            models.append("stored")
            live = [s for s in live_scores(db, meta, truth)
                    if args.start_year <= meta[s.race_id].season_year <= args.end_year]
            if live:                 # forecasts as they stood before the race, never recomputed
                scores += live
                models.append("live")
        names = driver_names(db)
        facts = load_error_facts(db)
        practice_data = db.execute(text("""
            SELECT r.season_year, COUNT(DISTINCT r.id) AS races,
                   COUNT(DISTINCT CASE WHEN s.session_type IN ('FP1', 'FP2') THEN r.id END) AS with_fp_laps,
                   AVG(CASE WHEN s.session_type IN ('FP1', 'FP2')
                            THEN (l.tire_compound IS NOT NULL)::int END) AS compound_share
            FROM races r
            LEFT JOIN sessions s ON s.race_id = r.id AND s.session_type IN ('FP1', 'FP2')
            LEFT JOIN laps l ON l.session_id = s.id
            WHERE r.season_year BETWEEN :a AND :b
              AND EXISTS (SELECT 1 FROM sessions sr JOIN race_results rr ON rr.session_id = sr.id
                          WHERE sr.race_id = r.id AND sr.session_type = 'R')
              AND (l.id IS NOT NULL OR s.id IS NULL)
            GROUP BY r.season_year ORDER BY 1
        """), {"a": args.start_year, "b": args.end_year}).all()

    print(f"PREDICTION SCOREBOARD {args.start_year}-{args.end_year}")
    summary = report(scores, meta, names, models)
    scored = [r for r in rows if r.season_year >= args.start_year]
    fired = sum(r.car_gap is not None and r.car_gap < r.implied_gap - 1e-12 for r in scored)
    print(f"\n=== CAR PACE RULE: replaced the driver's own lap for {fired} of {len(scored)} drivers "
          f"({fired / max(1, len(scored)):.1%}); it should be rare ===")
    wet_q = [r for r in scored if r.quali_wet]
    fired_wet = sum(r.car_gap is not None and r.car_gap < r.implied_gap - 1e-12 for r in wet_q)
    print(f"  in wet qualifying: {fired_wet} of {len(wet_q)} drivers ({fired_wet / max(1, len(wet_q)):.1%}) "
          f"across {len({r.race_id for r in wet_q})} races")
    if args.experiments:
        groups_report(scores, meta)
    confidence_report(scores, meta)
    error_report(scores, meta, facts)
    if PASS_PARAMS and "blend_pass" in models:
        print("  blend_pass (strength, margin) per season: "
              + ", ".join(f"{y}: {s:.2f}/{m * 100:.1f}%" for y, (s, m) in sorted(PASS_PARAMS.items())))
    if RECOVERY_WEIGHTS:
        print("  recovery weight for out-of-position drivers (own / everyone else) per season: "
              + ", ".join(f"{y}: {'-' if w is None else f'{w:.1f}'}/{p:.1f}" for y, (w, p) in sorted(RECOVERY_WEIGHTS.items())))
        penalised = [s.error for s in scores if s.model == "blend" and s.classified
                     and facts.get((s.race_id, s.driver_id), (None,))[0] is not None
                     and s.grid - facts[(s.race_id, s.driver_id)][0] >= 3]
        if penalised:
            print(f"  out-of-position finishers: {np.mean(penalised):.2f} places (n={len(penalised)})")
    signals_report(scored)
    with_fp = [r for r in scored if r.practice_gap is not None]
    with_all = sum(r.practice_gap_all is not None for r in scored)
    print(f"\n=== PRACTICE PACE: long-run data for {len(with_fp)} of {len(scored)} drivers "
          f"({len(with_fp) / max(1, len(scored)):.0%}); FP1+FP2 combined: {with_all} "
          f"({with_all / max(1, len(scored)):.0%}) ===")
    from dataclasses import replace as _replace
    probe = [_replace(r, form=r.practice_gap - r.implied_gap) for r in with_fp if r.residual is not None]
    sig, lo, hi, n = form_signal(probe)
    print(f"  signal: corr(long-run pace vs quali-implied pace, race pace vs quali-implied pace) = {sig:+.3f} "
          f"[{lo:+.3f}, {hi:+.3f}] n={n}  "
          f"{'REAL extra information' if lo > 0 else 'no clear extra information'}")
    print("  practice laps in the database (races with FP1/FP2 laps / raced, share of laps with a compound): "
          + ", ".join(f"{y}: {fp}/{n} {share or 0:.0%}" for y, n, fp, share in practice_data))
    if FITTED_BETAS:
        print("  production practice beta per season (fitted on earlier seasons): "
              + ", ".join(f"{k}: {v:.2f}" for k, v in sorted(FITTED_BETAS.items())))
    print("  coverage by season: " + ", ".join(
        f"{y}: {sum(r.practice_gap is not None for r in scored if r.season_year == y) / max(1, sum(r.season_year == y for r in scored)):.0%}"
        for y in sorted({r.season_year for r in scored})))
    if args.reliability:
        brier, d, lo, hi, n = reliability_signal(used_p, scores)
        print(f"\n=== RELIABILITY SIGNAL (does team non-finish rate predict retirements?) ===")
        print(f"  Brier team p_dnf {brier:.4f}; minus flat rate {d:+.5f} [{lo:+.5f}, {hi:+.5f}]  n={n}  "
              f"{'REAL signal' if hi < 0 else 'WORSE than a flat rate' if lo > 0 else 'no clear signal'}")
    LAST_SUMMARY.clear()
    LAST_SUMMARY.update({"range": [args.start_year, args.end_year], "models": summary})
    if args.dump:
        dump_scores([s for s in scores if s.model in ("grid", "blend")], args.dump)
        print(f"\nwrote driver scores to {args.dump}")
    if args.save:
        with open(args.save, "w") as f:
            json.dump({"range": [args.start_year, args.end_year], "models": summary}, f, indent=2)
        print(f"\nsaved headline summary to {args.save}")
    if args.compare:
        with open(args.compare) as f:
            baseline = json.load(f)
        print(f"\n=== REGRESSION GATE vs {args.compare} ===")
        if baseline.get("range") != [args.start_year, args.end_year]:
            print(f"  warning: baseline covers {baseline.get('range')}, this run {[args.start_year, args.end_year]}")
        problems = compare(summary, baseline["models"])
        print("  PASS" if not problems else f"  FAIL: {', '.join(problems)}")
        return 1 if problems else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
