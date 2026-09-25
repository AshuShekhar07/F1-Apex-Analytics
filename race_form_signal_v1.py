"""Does recent team race-form add information beyond the grid?

The all-driver backtest showed the simulator losing to "finish = grid": with
race pace taken from qualifying alone it knows nothing the grid does not, and it
treated the 0.8%-of-lap-time race-vs-quali residual as pure noise. Part of that
residual may be persistent (cars that are better in race trim). This test asks,
WITHOUT the simulator, whether that is real predictive signal.

For every driver in every dry target race (walk-forward, race-date cutoff):
  residual = actual race-pace gap - race gap implied by the qualifying gap
  form     = shrunk mean of the TEAM's residuals over its last FORM_RACES races
             before the target date (same era, any season): sum / (n + SHRINK)

Part 1 -- signal: correlation between pre-race form and the target race's
          residual (the thing form claims to predict). ~0 means no signal.
Part 2 -- finishing order, three predictions ranked within each race:
          grid       : starting grid
          pace+form  : quali-implied race gap + form
          blend      : grid rank + w * (pace+form rank - grid rank), w chosen
                       on the PREVIOUS season only (grid of 0..1)

Confidence intervals resample whole races. FORM_RACES and SHRINK are fixed in
advance, not tuned on the target races.

    python race_form_signal_v1.py --start-year 2024 --end-year 2025
"""

from __future__ import annotations

import argparse
import os
import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from statistics import mean
from typing import Any, Iterable, Sequence

import numpy as np
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from race_pace_from_quali_v1 import PaceObservation, best_quali_times, fit_model, load_observations, quali_gaps
from race_status import is_classified

FORM_RACES = 5
SHRINK = 2.0
BLEND_WEIGHTS = tuple(round(w, 1) for w in np.arange(0.0, 1.01, 0.1))


@dataclass(frozen=True)
class DriverForm:
    race_id: int
    season_year: int
    driver_id: int
    grid: int
    finish: int
    classified: bool
    implied_gap: float   # race gap implied by qualifying
    form: float          # team's shrunk recent residual (pre-race)
    residual: float | None  # this race's realised residual (post-race), None if no clean pace


# --- pure helpers --------------------------------------------------------------

def team_form(history: Sequence[tuple[date, int, float]], team_id: int, before: date,
              races: int = FORM_RACES, shrink: float = SHRINK) -> float:
    """Shrunk mean residual of a team's last `races` races strictly before `before`.

    history: (race_date, race_id, residual) for that team's drivers.
    """
    by_race = defaultdict(list)
    for race_date, race_id, residual in history:
        if race_date < before:
            by_race[(race_date, race_id)].append(residual)
    recent = sorted(by_race)[-races:]
    values = [mean(by_race[k]) for k in recent]
    return sum(values) / (len(values) + shrink) if values else 0.0


def ranks(scores: Sequence[float]) -> list[int]:
    """1-based ranks, lower score = better; ties broken by input order."""
    order = sorted(range(len(scores)), key=lambda i: (scores[i], i))
    out = [0] * len(scores)
    for position, i in enumerate(order, start=1):
        out[i] = position
    return out


def predictions(rows: Sequence[DriverForm], weight: float) -> dict[str, list[int]]:
    grid = ranks([r.grid for r in rows])
    pace = ranks([r.implied_gap + r.form for r in rows])
    blend = ranks([g + weight * (p - g) for g, p in zip(grid, pace)])
    return {"grid": grid, "pace_form": pace, "blend": blend}


def race_clustered(values_by_race: dict[int, list[float]], *, seed: int = 11, draws: int = 4000):
    means = [mean(v) for v in values_by_race.values() if v]
    if not means:
        return float("nan"), float("nan"), float("nan")
    rng = random.Random(seed)
    boots = sorted(mean(rng.choices(means, k=len(means))) for _ in range(draws))
    return mean(means), boots[int(0.05 * draws)], boots[int(0.95 * draws) - 1]


def form_signal(rows: Iterable[DriverForm], *, seed: int = 11, draws: int = 2000) -> tuple[float, float, float, int]:
    """Pearson r between pre-race form and realised residual, bootstrap CI over races."""
    by_race = defaultdict(list)
    for r in rows:
        if r.residual is not None:
            by_race[r.race_id].append((r.form, r.residual))
    pairs = [p for v in by_race.values() for p in v]
    if len(pairs) < 10:
        return float("nan"), float("nan"), float("nan"), len(pairs)

    def corr(ps):
        x, y = np.array(ps).T
        return float(np.corrcoef(x, y)[0, 1]) if x.std() > 0 and y.std() > 0 else 0.0

    rng = random.Random(seed)
    keys = list(by_race)
    boots = sorted(corr([p for k in rng.choices(keys, k=len(keys)) for p in by_race[k]]) for _ in range(draws))
    return corr(pairs), boots[int(0.05 * draws)], boots[int(0.95 * draws) - 1], len(pairs)


def choose_weight(races: dict[int, list[DriverForm]]) -> float:
    """Blend weight minimising finishing MAE on the given (earlier) races."""
    def mae(weight):
        errors = []
        for rows in races.values():
            predicted = predictions(rows, weight)["blend"]
            errors += [abs(p - r.finish) for p, r in zip(predicted, rows)]
        return mean(errors) if errors else float("inf")
    return min(BLEND_WEIGHTS, key=lambda w: (mae(w), w))


# --- database assembly ---------------------------------------------------------

def build_rows(db: Any, observations: Sequence[PaceObservation], *, start_year: int, end_year: int) -> list[DriverForm]:
    by_race_driver = {(o.race_id, o.driver_id): o for o in observations}
    team_history = defaultdict(list)   # (era, team) -> [(date, race_id, residual)]
    races = db.execute(text("""
        SELECT r.id, r.season_year, r.regulation_era, r.race_date
        FROM races r
        JOIN sessions s ON s.race_id = r.id AND s.session_type = 'R'
        JOIN session_weather sw ON sw.session_id = s.id AND sw.rainfall = FALSE
        WHERE r.season_year BETWEEN 2018 AND :b AND r.regulation_era IS NOT NULL
        ORDER BY r.race_date, r.id
    """), {"b": end_year}).mappings().all()

    rows: list[DriverForm] = []
    for race in races:
        era, when = race["regulation_era"], race["race_date"]
        history = [o for o in observations if o.regulation_era == era and o.race_date is not None and o.race_date < when]
        try:
            model = fit_model(history)
        except ValueError:
            model = None
        entries = db.execute(text("""
            SELECT re.driver_id, re.team_id, rr.starting_grid_position AS grid,
                   rr.finishing_position AS finish, rr.status
            FROM race_results rr
            JOIN sessions s ON s.id = rr.session_id AND s.session_type = 'R'
            JOIN race_entries re ON re.id = rr.race_entry_id
            WHERE s.race_id = :r AND rr.finishing_position IS NOT NULL
        """), {"r": race["id"]}).mappings().all()
        gaps = quali_gaps(best_quali_times(db, race["id"]))
        back_grid = len(entries) + 1
        back_gap = max(gaps.values()) if gaps else 0.03

        if model is not None and race["season_year"] >= start_year and len(entries) >= 10:
            for e in entries:
                obs = by_race_driver.get((race["id"], int(e["driver_id"])))
                gap = gaps.get(int(e["driver_id"]), back_gap)
                implied = model.race_gap(gap)
                rows.append(DriverForm(
                    race["id"], race["season_year"], int(e["driver_id"]),
                    int(e["grid"]) if e["grid"] and e["grid"] > 0 else back_grid,
                    int(e["finish"]), is_classified(e["status"]), implied,
                    team_form(team_history[(era, int(e["team_id"]))], int(e["team_id"]), when),
                    (obs.race_gap - implied) if obs else None,
                ))
        # update team history with THIS race only after its rows are built (no leakage)
        if model is not None:
            for e in entries:
                obs = by_race_driver.get((race["id"], int(e["driver_id"])))
                if obs is not None:
                    team_history[(era, int(e["team_id"]))].append((when, race["id"], obs.race_gap - model.race_gap(obs.quali_gap)))
    return rows


def evaluate(rows: Sequence[DriverForm], *, score_from: int) -> dict[str, Any]:
    """Score seasons >= score_from; each season's blend weight comes from the season before."""
    by_season_race: dict[int, dict[int, list[DriverForm]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        by_season_race[r.season_year][r.race_id].append(r)
    errors = {k: defaultdict(list) for k in ("grid", "pace_form", "blend")}
    finisher_errors = {k: defaultdict(list) for k in ("grid", "pace_form", "blend")}
    weights = {}
    for season in sorted(by_season_race):
        weight = choose_weight(by_season_race[season - 1]) if season - 1 in by_season_race else 0.0
        weights[season] = float(weight)
        if season < score_from:
            continue
        for race_id, race_rows in by_season_race[season].items():
            for name, predicted in predictions(race_rows, weight).items():
                for p, r in zip(predicted, race_rows):
                    errors[name][race_id].append(abs(p - r.finish))
                    if r.classified:
                        finisher_errors[name][race_id].append(abs(p - r.finish))

    def diff(table, a, b):
        return {race: [x - y for x, y in zip(table[a][race], table[b][race])] for race in table[a]}

    scored = [r for r in rows if r.season_year >= score_from]
    return {
        "signal": form_signal(scored),
        "weights": weights,
        "mae": {k: race_clustered(v)[0] for k, v in errors.items()},
        "mae_finishers": {k: race_clustered(v)[0] for k, v in finisher_errors.items()},
        "pace_form_minus_grid": race_clustered(diff(errors, "pace_form", "grid")),
        "blend_minus_grid": race_clustered(diff(errors, "blend", "grid")),
        "races": len({r.race_id for r in scored}),
        "drivers": len(scored),
    }


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start-year", type=int, default=2024)
    parser.add_argument("--end-year", type=int, default=2025)
    args = parser.parse_args()

    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as db:
        observations = load_observations(db, start_year=2018, end_year=args.end_year)
        # previous season is scored too, so the blend weight can be chosen on it
        rows = build_rows(db, observations, start_year=args.start_year - 1, end_year=args.end_year)
    result = evaluate(rows, score_from=args.start_year)
    r, lo, hi, n = result["signal"]

    print(f"=== RACE-FORM SIGNAL {args.start_year}-{args.end_year}: {result['drivers']} drivers, "
          f"{result['races']} races (form: last {FORM_RACES} team races, shrink {SHRINK}) ===")
    print(f"Part 1  corr(pre-race team form, realised race-vs-quali residual) = {r:+.3f} "
          f"[90% CI {lo:+.3f}, {hi:+.3f}]  n={n}")
    print("        signal is real only if the CI lies entirely above zero")
    print("Part 2  finishing-position MAE (all drivers / classified finishers only)")
    for name in ("grid", "pace_form", "blend"):
        print(f"        {name:10} {result['mae'][name]:.3f} / {result['mae_finishers'][name]:.3f}")
    print(f"        blend weight per season (chosen on the previous season): {result['weights']}")
    for label in ("pace_form_minus_grid", "blend_minus_grid"):
        d, a, b = result[label]
        verdict = "BEATS grid" if b < 0 else ("worse than grid" if a > 0 else "no clear difference")
        print(f"        {label:22} {d:+.3f} [90% CI {a:+.3f}, {b:+.3f}]  {verdict}")
    print("Read-only.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
