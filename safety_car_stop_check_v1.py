"""How much of the stop-count miss is safety-car luck? (read-only research report)

Production strategy (method M, practice tyre wear) names the right stop count for 47% of
cars (2022-25). Teams often take an extra, cheap stop under a Safety Car, Virtual Safety
Car or red flag, which nobody can know before the race. This report measures how much of
M's miss that explains.

For every dry car scored by the strategy backtest (walk-forward M, same rules):
  neutralised stop : a stop whose in-lap falls inside an SC / VSC / red-flag window
                     (race_neutralisations_v1, leader laps). A red-flag tyre change counts.
  green stops      : the car's stops minus its neutralised stops

Each car M got wrong is put in one group:
  explained   : the car's GREEN stop count equals M's prediction -- without the
                neutralised stop(s) M would have been right
  helped      : the car made neutralised stop(s), but its green count still differs from M
  untouched   : no neutralised stop -- the miss has nothing to do with a neutralisation

It also splits M and the hindsight ceiling (the race's own most common stop count) by
whether the race had any neutralisation, and reports races whose windows are unknown
separately (coverage != ok), never as clean.

    python safety_car_stop_check_v1.py --start-year 2022 --end-year 2025
"""

from __future__ import annotations

import argparse
import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from dotenv import load_dotenv
from sqlalchemy import create_engine

from race_strategy_timing_v1 import CarStrategy, is_legal_dry, load_car_strategies, predict_from_wear_timed

NEUTRALISATIONS = ("SC", "VSC", "RED_FLAG")


def stop_in_laps(car: CarStrategy) -> list[int]:
    """In-lap of each stop: the last lap of every stint but the final one."""
    return [s.end_lap for s in car.stints[:-1]]


def neutralised_stops(car: CarStrategy, neutralised_laps: frozenset[int]) -> int:
    return sum(1 for lap in stop_in_laps(car) if lap in neutralised_laps)


def classify(predicted: int | None, car: CarStrategy, neutralised_laps: frozenset[int]) -> str:
    """right | explained | helped | untouched | no prediction."""
    if predicted is None:
        return "no prediction"
    real = len(car.stints) - 1
    if real == predicted:
        return "right"
    under = neutralised_stops(car, neutralised_laps)
    if under == 0:
        return "untouched"
    return "explained" if real - under == predicted else "helped"


@dataclass(frozen=True)
class CarRow:
    race_id: int
    coverage: str             # race_neutralisations coverage: ok | ...
    race_neutralised: bool    # the race had at least one SC / VSC / red flag (coverage ok only)
    group: str                # classify() for M
    real: int
    predicted: int | None
    ceiling: int | None       # the race's own most common stop count
    under: int                # neutralised stops this car made


def race_mode_stops(race_cars: Sequence[CarStrategy]) -> int | None:
    legal = [len(c.stints) - 1 for c in race_cars if is_legal_dry(c.sequence)]
    return Counter(legal).most_common(1)[0][0] if legal else None


def run(db: Any, *, start_year: int, end_year: int) -> list[CarRow]:
    from race_neutralisations_v1 import load_race_neutralisations
    from race_practice_pace_v1 import load_weekend_wear

    cars = load_car_strategies(db, start_year=2018, end_year=end_year)
    targets = defaultdict(list)
    for c in cars:
        if start_year <= c.season_year <= end_year and not c.wet:
            targets[(c.race_date, c.race_id)].append(c)
    wear_by_race = {r: load_weekend_wear(db, r) for r in {c.race_id for c in cars}}
    rows: list[CarRow] = []
    for (race_date, race_id), race_cars in sorted(targets.items()):
        history = [c for c in cars if c.race_date < race_date]
        first = race_cars[0]
        m = predict_from_wear_timed(history, wear=wear_by_race.get(race_id), wear_by_race=wear_by_race,
                                    regulation_era=first.regulation_era, total_laps=first.race_laps).strategy
        predicted = len(m.sequence) - 1 if m else None
        windows = load_race_neutralisations(db, race_id)
        laps = windows.neutralised_laps(NEUTRALISATIONS) if windows.coverage == "ok" else frozenset()
        neutralised = windows.coverage == "ok" and any(e.event_type in NEUTRALISATIONS for e in windows.events)
        ceiling = race_mode_stops(race_cars)
        for car in race_cars:
            rows.append(CarRow(race_id, windows.coverage, neutralised, classify(predicted, car, laps),
                               len(car.stints) - 1, predicted, ceiling, neutralised_stops(car, laps)))
        print(f"race {race_id} ({race_date}): {len(race_cars)} cars, windows {windows.coverage}, "
              f"{'neutralised' if neutralised else 'clean' if windows.coverage == 'ok' else 'unknown'}", flush=True)
    return rows


def _share(n: int, d: int) -> str:
    return f"{n}/{d} ({n / d:.1%})" if d else "0/0"


def report(rows: Sequence[CarRow], label: str) -> None:
    print(f"\n=== SAFETY-CAR CHECK: stop count, {label} ({len(rows)} cars, "
          f"{len({r.race_id for r in rows})} races) ===")
    known = [r for r in rows if r.coverage == "ok"]
    unknown = [r for r in rows if r.coverage != "ok"]
    print(f"neutralisation windows known for {len({r.race_id for r in known})} races "
          f"({len(known)} cars); unknown for {len({r.race_id for r in unknown})} races ({len(unknown)} cars)")

    every = [r for r in rows if r.predicted is not None]
    print(f"M per car, every scored car (should match the strategy backtest): "
          f"right {_share(sum(r.real == r.predicted for r in every), len(every))}")
    scored = [r for r in known if r.predicted is not None]
    groups = Counter(r.group for r in scored)
    wrong = len(scored) - groups["right"]
    print(f"\nM per car (windows known): right {_share(groups['right'], len(scored))}")
    print(f"M wrong: {wrong} cars")
    for g, why in (("explained", "green-flag stop count = M's prediction"),
                   ("helped", "stopped under SC/VSC/red flag, green count still differs"),
                   ("untouched", "no stop under SC/VSC/red flag")):
        print(f"  {g:10} {_share(groups[g], wrong):>18}   {why}")
    direction = Counter("real > predicted" if r.real > r.predicted else "real < predicted"
                        for r in scored if r.group != "right")
    print(f"  direction of the misses: {dict(direction)}")
    if scored:
        best = groups["right"] + groups["explained"]
        print(f"M right if neutralised stops were not counted: {_share(best, len(scored))}")

    print("\nby race type (windows known)    races   cars   M right            ceiling right")
    for name, pick in (("clean (no SC/VSC/red flag)", lambda r: not r.race_neutralised),
                       ("neutralised", lambda r: r.race_neutralised)):
        part = [r for r in known if pick(r)]
        m_rows = [r for r in part if r.predicted is not None]
        c_rows = [r for r in part if r.ceiling is not None]
        print(f"  {name:28} {len({r.race_id for r in part}):5}  {len(part):5}   "
              f"{_share(sum(r.real == r.predicted for r in m_rows), len(m_rows)):18} "
              f"{_share(sum(r.real == r.ceiling for r in c_rows), len(c_rows))}")

    print("\ncars that stopped under a neutralisation: "
          f"{_share(sum(r.under > 0 for r in known), len(known))}")


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start-year", type=int, default=2022)
    parser.add_argument("--end-year", type=int, default=2025)
    args = parser.parse_args()
    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as db:
        rows = run(db, start_year=args.start_year, end_year=args.end_year)
    if not rows:
        print("No races scored.")
        return 1
    report(rows, f"dry races {args.start_year}-{args.end_year}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
