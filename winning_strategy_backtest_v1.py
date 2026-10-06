"""Walk-forward backtest of the predicted winning strategy (race_winning_strategy_v1).

Scored on the FRONT-GRID cars (grid 1-5) of every dry race, each predicted only from
earlier races:
  W  winning strategy : this track's front-grid plans (SC/VSC stops left out), era fallback
  E  era front grid   : the same rule with no track history (shows what the track adds)
  M  production       : today's one-plan-for-every-car method (practice wear)

Metrics (per front-grid car, then averaged per race; 90% intervals resample races):
  stop_count_hit / sequence_hit          : every front-grid car
  *_green                                : cars with no stop under SC/VSC/red flag (windows known)
  first_window_hit / second_window_hit   : the car's green first / second stop inside the window
  first_stop_within_3                    : typical (W, E) or predicted (M) first stop within 3 laps
  first_window_width                     : laps in the first window (narrower = more useful)
  winner_sequence_hit                    : the race winner ran the predicted tyre order

    python winning_strategy_backtest_v1.py --start-year 2022 --end-year 2025
"""

from __future__ import annotations

import argparse
import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Sequence

from dotenv import load_dotenv
from sqlalchemy import create_engine

from race_strategy_backtest_v2 import race_clustered_ci
from race_strategy_timing_v1 import CarStrategy, load_car_strategies, predict_from_wear_timed
from race_winning_strategy_v1 import (
    FRONT_GRID,
    WinningStrategy,
    clean_front_cars,
    format_strategy,
    is_front,
    load_neutralised_laps,
    predict_winning_strategy,
    stop_in_laps,
)

W, E, M = "W winning strategy (track)", "E era front grid", "M production (all cars)"
METRICS = (("stop_count_hit", False), ("stop_count_hit_green", False), ("sequence_hit", False),
           ("sequence_hit_green", False), ("first_window_hit", False), ("second_window_hit", False),
           ("first_stop_within_3", False), ("first_window_width", True), ("winner_sequence_hit", False))


@dataclass(frozen=True)
class Row:
    race_id: int
    method: str
    values: dict = field(default_factory=dict)


def score_car(race_id: int, method: str, car: CarStrategy, *, sequence: tuple[str, ...] | None,
              windows: Sequence[tuple[int, int]] | None, first_stop: int | None,
              neutralised: frozenset[int] | None) -> Row:
    """windows: (first lap, last lap) per stop, None for a method without windows (M).
    neutralised: the race's SC/VSC/red-flag laps; None when unknown."""
    real_stops = len(car.stints) - 1
    stops = stop_in_laps(car)
    known = neutralised is not None
    green = [known and lap not in neutralised for lap in stops]
    clean_car = known and all(green)
    hit_stops = int(sequence is not None and len(sequence) - 1 == real_stops)
    hit_sequence = int(sequence is not None and sequence == car.sequence)
    v: dict[str, Any] = {
        "stop_count_hit": hit_stops,
        "sequence_hit": hit_sequence,
        "stop_count_hit_green": hit_stops if clean_car else None,
        "sequence_hit_green": hit_sequence if clean_car else None,
        "first_window_hit": None, "second_window_hit": None, "first_window_width": None,
        "first_stop_within_3": None,
        "winner_sequence_hit": hit_sequence if car.finish == 1 else None,
    }
    if windows:
        v["first_window_width"] = windows[0][1] - windows[0][0] + 1
        for i, key in ((0, "first_window_hit"), (1, "second_window_hit")):
            if i < len(windows) and i < len(stops) and green[i]:
                v[key] = int(windows[i][0] <= stops[i] <= windows[i][1])
    if first_stop is not None and stops and green[0]:
        v["first_stop_within_3"] = int(abs(first_stop - stops[0]) <= 3)
    return Row(race_id, method, v)


def _plan(p: WinningStrategy | None):
    if p is None:
        return None, None, None
    windows = [(w.first_lap, w.last_lap) for w in p.windows]
    return p.sequence, windows, (p.windows[0].typical_lap if p.windows else None)


EXAMPLES: list[str] = []


def run(db: Any, *, start_year: int, end_year: int) -> list[Row]:
    from race_practice_pace_v1 import load_weekend_wear

    cars = load_car_strategies(db, start_year=2018, end_year=end_year)
    race_ids = {c.race_id for c in cars}
    neutralised = load_neutralised_laps(db, race_ids)
    wear_by_race = {r: load_weekend_wear(db, r) for r in race_ids}
    clean = clean_front_cars(cars, neutralised)
    targets = defaultdict(list)
    for c in cars:
        if start_year <= c.season_year <= end_year and not c.wet:
            targets[(c.race_date, c.race_id)].append(c)
    rows: list[Row] = []
    EXAMPLES.clear()
    for (race_date, race_id), race_cars in sorted(targets.items()):
        front = [c for c in race_cars if is_front(c)]
        if not front:
            continue
        first = race_cars[0]
        history = [c for c in clean if c.race_date < race_date]
        kwargs = dict(regulation_era=first.regulation_era, total_laps=first.race_laps)
        w = predict_winning_strategy(history, track_id=first.track_id, **kwargs)
        e = predict_winning_strategy(history, track_id=-1, **kwargs)
        m = predict_from_wear_timed([c for c in cars if c.race_date < race_date], wear=wear_by_race.get(race_id),
                                    wear_by_race=wear_by_race, **kwargs).strategy
        m_plan = (m.sequence, None, m.stop_laps[0] if m.stop_laps else None) if m else (None, None, None)
        for car in front:
            for name, (sequence, windows, first_stop) in ((W, _plan(w)), (E, _plan(e)), (M, m_plan)):
                rows.append(score_car(race_id, name, car, sequence=sequence, windows=windows,
                                      first_stop=first_stop, neutralised=neutralised.get(race_id)))
        if w is not None and len(EXAMPLES) < 400:
            real = "; ".join(f"P{c.grid} {'-'.join(x[0] for x in c.sequence)} stops {stop_in_laps(c)}"
                             f"{' (won)' if c.finish == 1 else ''}" for c in sorted(front, key=lambda c: c.grid))
            EXAMPLES.append(f"race {race_id} ({race_date}):\n{format_strategy(w)}\n  Real front grid: {real}")
        print(f"race {race_id} ({race_date}): {len(front)} front-grid cars scored", flush=True)
    return rows


def summarise(rows: Sequence[Row], baseline: str) -> list[tuple]:
    methods = sorted({r.method for r in rows})
    out = []
    for metric, lower_is_better in METRICS:
        for m in methods:
            mine = [r for r in rows if r.method == m]
            value, lo, hi = race_clustered_ci(mine, lambda r, k=metric: r.values.get(k))
            n = sum(r.values.get(metric) is not None for r in mine)
            if m == baseline or n == 0:
                out.append((metric, m, value, n, None))
                continue
            base = [r for r in rows if r.method == baseline]

            class _D:
                def __init__(self, race_id, value):
                    self.race_id, self.value = race_id, value
            diffs = [_D(a.race_id, a.values[metric] - b.values[metric]) for a, b in zip(mine, base)
                     if a.values.get(metric) is not None and b.values.get(metric) is not None]
            if not diffs:
                out.append((metric, m, value, n, None))
                continue
            d, dlo, dhi = race_clustered_ci(diffs, lambda r: r.value)
            better = (dhi < 0) if lower_is_better else (dlo > 0)
            worse = (dlo > 0) if lower_is_better else (dhi < 0)
            out.append((metric, m, value, n, (d, dlo, dhi, "BETTER" if better else "WORSE" if worse
                                               else "no clear difference")))
    return out


def print_table(rows: Sequence[Row], baseline: str) -> None:
    print(f"metric                 method                        value      n   vs {baseline} [90% CI]")
    for metric, method, value, n, cmp in summarise(rows, baseline):
        tail = "" if cmp is None else f"{cmp[0]:+.3f} [{cmp[1]:+.3f}, {cmp[2]:+.3f}]  {cmp[3]}"
        print(f"{metric:22} {method:28} {value:7.3f} {n:6}   {tail}")


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start-year", type=int, default=2022)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument("--examples", type=int, default=6, help="example predictions to print")
    args = parser.parse_args()
    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as db:
        rows = run(db, start_year=args.start_year, end_year=args.end_year)
    if not rows:
        print("No races scored.")
        return 1
    print(f"\n=== WINNING STRATEGY BACKTEST (dry races {args.start_year}-{args.end_year}, "
          f"front grid = P1-P{FRONT_GRID}, {len({r.race_id for r in rows})} races) ===")
    print("\n--- vs M, production ---")
    print_table(rows, M)
    print("\n--- vs E, the era's front-grid plan (what the track history adds) ---")
    print_table([r for r in rows if r.method != M], E)
    print(f"\n--- last {args.examples} predictions ---")
    for example in EXAMPLES[-args.examples:]:
        print(example)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
