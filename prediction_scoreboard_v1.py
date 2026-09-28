"""Prediction scoreboard: every finishing-order model scored the same way.

One walk-forward harness for the race forecast, so a change is judged on the same
races and metrics as everything before it, against the same baselines. Nothing
here is tuned on the races it scores.

Models (per race, only data strictly before the race date):
  grid      finish in grid order; win/podium = shrunk grid-slot rates (baseline)
  blend     production (race_forecast_v1.production_pace): grid rank + w * (pace + team form
            rank - grid rank), w chosen on the previous season; pace = car pace (a driver whose
            qualifying lap was missing or >0.3% slower than the teammate's gets the teammate's
            pace + 0.3%); win/podium = grid-slot rates
  CANDIDATES  experimental pace variants, each scored against the production blend
  stored    with --stored: the forecasts actually stored in race_forecasts (the live
            record, incl. the new-era fallbacks the blend rows cannot rebuild)

Metrics (race-weighted means, 90% CI resampling whole races):
  winner_hit          predicted P1 won
  podium_overlap      share of the real top 3 we named in our top 3
  top10_overlap       share of the real top 10 we named in our top 10
  rank_corr           Spearman correlation of predicted vs actual order
  mae / mae_finishers mean |predicted - actual| position (all / classified only)
  bad_miss_finishers  share of classified finishers off by >= 10 places
  brier_win/podium    mean squared error of the probabilities (0 = perfect)
Plus: breakdowns by era, wet/dry, grid group; probability calibration; worst misses.

    python prediction_scoreboard_v1.py --start-year 2019 --end-year 2026
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
# (blend_car was promoted to production on 2026-09-28: bad misses 2.6% -> 1.8%, worse nowhere)
CANDIDATES: dict[str, Callable[[Sequence[DriverForm]], list[DriverForm]]] = {}


def model_scores(rows: Sequence[DriverForm], meta: dict[int, RaceMeta], rates, *, start_year: int) -> list[DriverScore]:
    """grid, blend and candidate predictions for every race from start_year.

    Each blend variant chooses its own weight on the previous season (with the same
    variant applied), so a candidate never borrows the production weight."""
    by_season = defaultdict(lambda: defaultdict(list))
    for r in rows:
        by_season[r.season_year][r.race_id].append(r)
    from race_forecast_v1 import production_pace

    variants = {"blend": production_pace, **CANDIDATES}
    out: list[DriverScore] = []
    for season in sorted(by_season):
        if season < start_year:
            continue
        previous = by_season.get(season - 1)
        weights = {name: choose_weight({k: transform(v) for k, v in previous.items()}) if previous else 0.5
                   for name, transform in variants.items()}
        for race_id, race_rows in by_season[season].items():
            m = meta[race_id]
            slot = rates(m.race_date, m.era, len(race_rows))
            ranked = {"grid": predictions(race_rows, 0.0)["grid"]}
            for name, transform in variants.items():
                ranked[name] = predictions(transform(race_rows), weights[name])["blend"]
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


# --- database ------------------------------------------------------------------

def load_meta(db: Any) -> dict[int, RaceMeta]:
    rows = db.execute(text("""
        SELECT r.id, r.race_date, r.season_year, r.regulation_era, COALESCE(sw.rainfall, FALSE), t.name
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

    candidates = [m for m in models if m in CANDIDATES]
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

    for field in ("p_win", "p_podium"):
        print(f"\n=== CALIBRATION {field} ({main}): when we said X, it happened Y ===")
        for label, n, predicted, observed in calibration(scores, main, field):
            flag = "  <- off" if n >= 30 and abs(predicted - observed) > max(0.05, 0.5 * predicted) else ""
            print(f"  {label:>10}  n={n:5}  said {predicted:6.1%}  happened {observed:6.1%}{flag}")

    print(f"\n=== WORST MISSES ({main}, classified finishers) ===")
    worst = sorted((s for s in scores if s.model == main and s.classified), key=lambda s: -s.error)[:12]
    for s in worst:
        m = meta[s.race_id]
        print(f"  {m.season_year} {m.name:22} {names.get(s.driver_id, s.driver_id):22} grid {s.grid:2}  "
              f"predicted P{s.predicted:<2} finished P{s.finish}")
    return {m: {k: list(v) for k, v in summary[m].items()} for m in models}


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


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start-year", type=int, default=2019)
    parser.add_argument("--end-year", type=int, default=2026)
    parser.add_argument("--stored", action="store_true", help="also score the stored forecasts (live record)")
    parser.add_argument("--save", help="write the headline summary to this JSON file")
    parser.add_argument("--compare", help="fail (exit 1) if worse than this saved summary")
    args = parser.parse_args()

    from race_pace_from_quali_v1 import load_observations

    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as db:
        meta = load_meta(db)
        rates = slot_rate_function(load_finishes(db))
        observations = load_observations(db, start_year=2018, end_year=args.end_year)
        rows = build_rows(db, observations, start_year=args.start_year - 1, end_year=args.end_year, include_wet=True)
        scores = model_scores(rows, meta, rates, start_year=args.start_year)
        models = ["grid", "blend", *CANDIDATES]
        if args.stored:
            scores += [s for s in stored_scores(db, meta, load_truth(db))
                       if args.start_year <= meta[s.race_id].season_year <= args.end_year]
            models.append("stored")
        names = driver_names(db)

    print(f"PREDICTION SCOREBOARD {args.start_year}-{args.end_year}")
    summary = report(scores, meta, names, models)
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
