"""Walk-forward backtest of strategy PREDICTION (which strategy a car will run).

Every car that covered >= 90% of each target race is scored against its REAL
stints (rebuilt from pit/tyre-life lap data, so same-compound stops count).

Methods (history strictly before the race date):
  A  production today : era's most common strategy from race_stints (compound-change stints)
  B  era mode, real   : era's most common strategy from real stints
  C  hierarchy        : track + grid band -> track -> era + band -> era (race_strategy_timing_v1)
  D  pre-start        : C, filtered to strategies that started on the car's declared compound

Dry races by default; --wet scores wet races and adds W (wet-race precedent) vs the dry methods.

Metrics per car: sequence match, stop-count match, first-stop error (laps, when
both predicted and real strategies stop), first stop within 3 laps. Confidence
intervals resample whole races.

    python race_strategy_prediction_backtest_v1.py --start-year 2024 --end-year 2025
    python race_strategy_prediction_backtest_v1.py --start-year 2022 --end-year 2025 --wet
"""

from __future__ import annotations

import argparse
import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from statistics import mean, median
from typing import Any, Sequence

from dotenv import load_dotenv
from sqlalchemy import create_engine

from race_strategy_backtest_v2 import race_clustered_ci
from race_strategy_precedent_v1 import build_strategy, historical_strategy_options, load_precedents
from race_strategy_simulator_v1 import Strategy
from race_strategy_timing_v1 import CarStrategy, is_legal_dry, load_car_strategies, predict_strategy


@dataclass(frozen=True)
class Score:
    race_id: int
    method: str
    sequence_hit: int
    stop_count_hit: int
    first_stop_error: float | None
    first_stop_within_3: int | None


def score(race_id: int, method: str, predicted: Strategy | None, actual: CarStrategy) -> Score:
    if predicted is None:
        return Score(race_id, method, 0, 0, None, None)
    real_stops = [s.end_lap for s in actual.stints[:-1]]
    error = abs(predicted.stop_laps[0] - real_stops[0]) if predicted.stop_laps and real_stops else None
    return Score(race_id, method, int(predicted.sequence == actual.sequence),
                 int(len(predicted.stop_laps) == len(real_stops)), error,
                 None if error is None else int(error <= 3))


def era_mode_real(history: Sequence[CarStrategy], era: str, total_laps: int, wet: bool) -> Strategy | None:
    pool = [h for h in history if h.regulation_era == era and h.wet == wet and (wet or is_legal_dry(h.sequence))]
    if not pool:
        return None
    sequence, _ = Counter(h.sequence for h in pool).most_common(1)[0]
    rows = [h for h in pool if h.sequence == sequence]
    stops = [round(median(h.stop_fractions[i] for h in rows) * total_laps) for i in range(len(sequence) - 1)]
    return build_strategy(sequence, stops, total_laps)


def run(db: Any, *, start_year: int, end_year: int, wet: bool) -> list[Score]:
    cars = load_car_strategies(db, start_year=2018, end_year=end_year)
    targets = defaultdict(list)
    for c in cars:
        if start_year <= c.season_year <= end_year and c.wet == wet:
            targets[(c.race_date, c.race_id)].append(c)
    precedent_cache: dict = {}
    scores: list[Score] = []
    for (race_date, race_id), race_cars in sorted(targets.items()):
        history = [c for c in cars if c.race_date < race_date]
        era, track, total_laps = race_cars[0].regulation_era, race_cars[0].track_id, race_cars[0].race_laps
        if race_date not in precedent_cache:
            records = [p for p in load_precedents(db, before_date=race_date) if p.regulation_era == era]
            options = historical_strategy_options((p.strategy for p in records), total_laps)
            precedent_cache[race_date] = options[0][0] if options else None
        production = precedent_cache[race_date]
        dry_hierarchy = {}
        for car in race_cars:
            key = (car.grid or 99) <= 10
            if key not in dry_hierarchy:
                dry_hierarchy[key] = predict_strategy(history, track_id=track, regulation_era=era,
                                                      grid=car.grid, total_laps=total_laps).strategy
            scores.append(score(race_id, "A production", production, car))
            scores.append(score(race_id, "B era mode (real stints)", era_mode_real(history, era, total_laps, False), car))
            scores.append(score(race_id, "C hierarchy", dry_hierarchy[key], car))
            scores.append(score(race_id, "D pre-start (known start tyre)",
                                predict_strategy(history, track_id=track, regulation_era=era, grid=car.grid,
                                                 total_laps=total_laps, start_compound=car.sequence[0]).strategy, car))
            if wet:
                scores.append(score(race_id, "W wet precedent",
                                    predict_strategy(history, track_id=track, regulation_era=era, grid=car.grid,
                                                     total_laps=total_laps, wet=True).strategy, car))
        print(f"race {race_id} ({race_date}): {len(race_cars)} cars scored", flush=True)
    return scores


def summarise(scores: Sequence[Score], baseline: str = "A production") -> list[tuple]:
    methods = sorted({s.method for s in scores})
    by_method = {m: [s for s in scores if s.method == m] for m in methods}
    rows = []
    for metric, lower_is_better in (("sequence_hit", False), ("stop_count_hit", False),
                                    ("first_stop_error", True), ("first_stop_within_3", False)):
        for m in methods:
            value = race_clustered_ci(by_method[m], lambda s, k=metric: getattr(s, k))[0]
            if m == baseline:
                rows.append((metric, m, value, None))
                continue
            paired = defaultdict(list)
            base_rows = by_method[baseline]
            for s, b in zip(by_method[m], base_rows):
                x, y = getattr(s, metric), getattr(b, metric)
                if x is not None and y is not None:
                    paired[s.race_id].append(x - y)

            class _R:  # adapter for race_clustered_ci
                def __init__(self, race_id, value):
                    self.race_id, self.value = race_id, value
            diffs = [_R(r, v) for r, vs in paired.items() for v in vs]
            d, lo, hi = race_clustered_ci(diffs, lambda r: r.value)
            better = (hi < 0) if lower_is_better else (lo > 0)
            worse = (lo > 0) if lower_is_better else (hi < 0)
            rows.append((metric, m, value, (d, lo, hi, "BETTER" if better else "WORSE" if worse else "no clear difference")))
    return rows


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start-year", type=int, default=2024)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument("--wet", action="store_true", help="score wet races (adds the wet-precedent method)")
    args = parser.parse_args()

    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as db:
        scores = run(db, start_year=args.start_year, end_year=args.end_year, wet=args.wet)
    if not scores:
        print("No races scored.")
        return 1
    n_cars = len([s for s in scores if s.method == "A production"])
    print(f"\n=== STRATEGY PREDICTION BACKTEST ({'wet' if args.wet else 'dry'} races "
          f"{args.start_year}-{args.end_year}, {n_cars} cars, truth = real stints) ===")
    print("metric               method                           value   vs A production [90% CI, races resampled]")
    for metric, method, value, cmp in summarise(scores):
        tail = "" if cmp is None else f"{cmp[0]:+.3f} [{cmp[1]:+.3f}, {cmp[2]:+.3f}]  {cmp[3]}"
        print(f"{metric:20} {method:32} {value:6.3f}   {tail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
