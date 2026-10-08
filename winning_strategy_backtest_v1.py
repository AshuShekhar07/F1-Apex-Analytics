"""Walk-forward backtest of the predicted winning strategy (race_winning_strategy_v1).

Scored on the FRONT-GRID cars (grid 1-5) of every dry race, each predicted only from
earlier races:
  W  winning strategy : this track's front-grid plans (SC/VSC stops left out), era fallback
  E  era front grid   : the same rule with no track history (shows what the track adds)
  M  production       : today's one-plan-for-every-car method (practice wear)
  H  hybrid           : M's stop count (practice wear); tyre order and pit windows from W's
                        front-grid cars with that stop count; windows capped at 10 laps
  PS1/PS2 pre-start   : H once each car's starting tyre is declared: history cut to front-grid
                        cars that started on it; PS1 keeps H's stop count, PS2 takes the stop
                        count of those cars (race_winning_strategy_v1.predict_pre_start).
                        after_start_hit scores the plan after the first tyre, which pre-start
                        methods are given.

Metrics (per front-grid car, then averaged per race; 90% intervals resample races):
  stop_count_hit / sequence_hit          : every front-grid car
  *_green                                : cars with no stop under SC/VSC/red flag (windows known)
  first_window_hit / second_window_hit   : the car's green first / second stop inside the window
  first_stop_within_3                    : typical (W, E) or predicted (M) first stop within 3 laps
  first_window_width                     : laps in the first window (narrower = more useful)
  winner_sequence_hit                    : the race winner ran the predicted tyre order

    python winning_strategy_backtest_v1.py --start-year 2022 --end-year 2025

RESULT 2026-10-06 (2022-25, 70 races, 328 front-grid cars). W NOT PROMOTED over M:
  stop count W 0.410 vs M 0.478 (n.s.), green 0.462 vs 0.573 (n.s.); tyre order 0.245 vs
  0.324 (n.s.), green 0.295 vs 0.402 (WORSE); first stop within 3 laps 0.411 vs 0.468 (n.s.);
  winner's tyre order 0.258 vs 0.303 (n.s.). Practice wear (M) beats track history even for
  the front grid. Track history does beat the era alone (green stop count +0.131, BETTER;
  first window 5.7 vs 6.5 laps wide, BETTER). First window hit 0.333 at 5.7 laps wide.
  Confidence levels are ordered: stop count right medium 0.552 (119 cars), low 0.328 (104);
  main or alternative right medium 0.902, low 0.797; high had 1 race (too few to judge).
  2026 (13 races): no clear difference vs M anywhere.
HYBRID H 2026-10-06 (same cars). 2022-25 (70 races, 328 front-grid cars), H vs M: stop count
  0.478 = 0.478 (same by construction), green 0.573 = 0.573; tyre order 0.308 vs 0.324
  (-0.016 [-0.047, +0.013], n.s.), green 0.382 vs 0.402 (n.s.); first stop within 3 laps 0.463
  vs 0.468 (n.s.); winner's tyre order 0.288 vs 0.303 (n.s.). Worse nowhere, better nowhere:
  a TIE with production on the plan, plus pit windows production does not give (first window
  hit 0.386 at 6.1 laps wide, second 0.504). H vs W: higher on every metric, none clear.
  2026 (13 races): H >= M on every metric, none clear. Confidence levels (H, 2022-25): stop
  count right medium 0.636 (73 cars), low 0.532 (150); main or alternative 0.906 / 0.800.
WET-RULE FIX 2026-10-06 (same cars, 80 races): W with rain-flagged slick races in its history vs
  W0 without -- no clear difference anywhere (green stop count +0.032 [+0.000, +0.063]); not
  adopted. The track record (facts) uses the tyre rule, so it lists those races.
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
    format_review,
    format_strategy,
    is_front,
    review_race,
    track_record,
    load_neutralised_laps,
    predict_hybrid,
    predict_pre_start,
    predict_winning_strategy,
    stop_in_laps,
)

W, E, M = "W winning strategy (track)", "E era front grid", "M production (all cars)"
H = "H hybrid (wear stops, track plan)"
PS1 = "PS1 pre-start: hybrid stops"
PS2 = "PS2 pre-start: start-tyre stops"
METRICS = (("stop_count_hit", False), ("stop_count_hit_green", False), ("sequence_hit", False),
           ("sequence_hit_green", False), ("after_start_hit", False), ("first_window_hit", False), ("second_window_hit", False),
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
        # the plan after the starting tyre: a pre-start method knows the first tyre, so its
        # tyre-order gain is partly given; this part it has to predict
        "after_start_hit": int(sequence is not None and tuple(sequence[1:]) == tuple(car.sequence[1:])),
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
LEVELS: list[tuple[str, int, str, int, int, int]] = []   # (method, race_id, level, stop hit, main-or-alt hit, sequence hit)


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
    LEVELS.clear()
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
        h = predict_hybrid(history, [c for c in cars if c.race_date < race_date], wear=wear_by_race.get(race_id),
                           wear_by_race=wear_by_race, track_id=first.track_id, **kwargs)
        all_before = [c for c in cars if c.race_date < race_date]
        pre_start: dict = {}

        def plan_for_start(rule: str, compound: str):
            if (rule, compound) not in pre_start:
                pre_start[(rule, compound)] = predict_pre_start(
                    history, all_before, start_compound=compound, wear=wear_by_race.get(race_id),
                    wear_by_race=wear_by_race, track_id=first.track_id, stop_rule=rule, **kwargs)
            return pre_start[(rule, compound)]

        for car in front:
            plans = [(W, _plan(w)), (E, _plan(e)), (M, m_plan), (H, _plan(h))]
            if car.sequence:          # the starting tyre is declared before the start
                plans += [(PS1, _plan(plan_for_start("hybrid", car.sequence[0]))),
                          (PS2, _plan(plan_for_start("start", car.sequence[0])))]
            else:
                plans += [(PS1, _plan(h)), (PS2, _plan(h))]
            for name, (sequence, windows, first_stop) in plans:
                rows.append(score_car(race_id, name, car, sequence=sequence, windows=windows,
                                      first_stop=first_stop, neutralised=neutralised.get(race_id)))
        laps = neutralised.get(race_id)
        for name, p in ((W, w), (H, h)):
            if p is None:
                continue
            for car in front:
                if laps is None or any(lap in laps for lap in stop_in_laps(car)):
                    continue                       # level record on green-flag front-grid cars only
                real = len(car.stints) - 1
                alt = p.alternative.stops if p.alternative else None
                LEVELS.append((name, race_id, p.confidence, int(real == p.stops), int(real in (p.stops, alt)),
                               int(car.sequence == p.sequence)))
        if h is not None:
            record = track_record([c for c in cars if c.race_date < race_date], neutralised, track_id=first.track_id)
            EXAMPLES.append(f"race {race_id} ({race_date}):\n{format_strategy(h, record)}\n"
                            f"{format_review(h, review_race(h, race_cars, laps))}")
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


def level_report(method: str) -> None:
    """How often each confidence level was right, walk-forward (green-flag front-grid cars)."""
    print(f"\n--- confidence levels, {method} (green-flag front-grid cars; 90% CI resamples races) ---")
    print("level    races   cars   stop count right          main or alternative right   tyre order right")

    class _R:
        def __init__(self, race_id, value):
            self.race_id, self.value = race_id, value
    for level in ("high", "medium", "low"):
        rows = [r[1:] for r in LEVELS if r[0] == method and r[2] == level]
        if not rows:
            print(f"{level:7}      0      0")
            continue
        cells = []
        for k in (2, 3, 4):
            v, lo, hi = race_clustered_ci([_R(r[0], r[k]) for r in rows], lambda r: r.value)
            cells.append(f"{v:.3f} [{lo:.3f}, {hi:.3f}]")
        print(f"{level:7} {len({r[0] for r in rows}):6} {len(rows):6}   {cells[0]:25} {cells[1]:27} {cells[2]}")


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
    print_table([r for r in rows if r.method in (W, E)], E)
    print("\n--- pre-start (starting tyre known) vs H, same cars ---")
    print_table([r for r in rows if r.method in (H, PS1, PS2)], H)
    print("\n--- H vs W, the track-only front-grid plan ---")
    print_table([r for r in rows if r.method in (W, H)], W)
    level_report(H)
    level_report(W)
    print(f"\n--- last {args.examples} predictions ---")
    for example in EXAMPLES[-args.examples:]:
        print(example)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
