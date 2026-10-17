"""Walk-forward backtest of strategy PREDICTION (which strategy a car will run).

Every car that covered >= 90% of each target race is scored against its REAL
stints (rebuilt from pit/tyre-life lap data, so same-compound stops count).

Methods (history strictly before the race date):
  A  old production  : era's most common strategy from race_stints (compound-change stints)
  B  era mode, real   : era's most common strategy from real stints
  C  hierarchy        : track + grid band -> track -> era + band -> era (race_strategy_timing_v1)
  D  pre-start        : C, filtered to strategies that started on the car's declared compound
  E  track-adjusted   : stop count, sequence and stop laps = the era's, pulled toward this
                        track's record by how much of it there is (predict_track_adjusted)
  F  last race here   : what most cars did the last time this track was raced
  H  era mode + track timing : B's sequence, stop laps timed like C. REJECTED 2026-09-30:
     first-stop error 8.64 vs 8.34 laps (worse) in 2022-25
  J  practice wear + compounds : I's stop count; among the era's 4 most common sequences
                        with that many stops, the one whose compounds teams long-ran in practice
  K  era stops + compounds     : same sequence choice with B's stop count (isolates the compounds)
     J/K REJECTED 2026-09-30: sequence 0.203 vs 0.255 (WORSE) in 2022-25; K never changed B's
     pick. Practice compound mix does not say which tyres teams race on (every team runs its
     whole allocation in practice).
  L  Pirelli compounds : stop count voted by earlier races with the same C-number set (the 7
                        closest in practice wear if more), era's most common sequence with it
     L REJECTED 2026-09-30: stop count 0.446 vs I's 0.470 in 2022-25 (no gain over B); 2026
     sequence 0.087 vs 0.225 (WORSE) -- the same C-numbers on the 2026 cars do not behave as
     on 2023-25 cars, and within one era there are too few same-set races to vote.
  M  wear + wear timing : I's stop count and sequence; stop laps from the cars with that many
                        stops in the 7 earlier races with the closest practice wear (compare to I,
                        which it replaced). PROMOTED 2026-09-30: sequence and stop count equal
                        to I by construction; first stop 2026 9.53 vs 10.00 laps, within 3 laps
                        25.8% vs 18.6% (BETTER vs B where I was not); 2022-25 equal (8.20/8.21)
  N  wear neighbours' sequence : the sequence most CARS ran in the 7 races with the closest
                        practice wear, timed from them (M takes only the stop count from them)
  O  expected match    : argmax over sequences of P(stop count | closest-wear races' cars) x
                        P(sequence | stop count, era) -- does not commit to the majority stop
                        count first. N and O are compared with M, the production method.
     N/O REJECTED 2026-10-05 (vs M, 2022-25, 1248 cars): N sequence 0.213 vs 0.234 (n.s.), first
     stop 8.79 vs 8.20 laps and within 3 laps 0.243 vs 0.296 (both WORSE); O sequence 0.251 vs
     0.234 (+0.017 [-0.008, +0.045], not clear), stop count 0.480 vs 0.470 (n.s.), first stop
     8.67 vs 8.20 laps (WORSE: timed from the cars running the exact sequence, a thinner pool
     than M's same-stop-count cars). 2026 (229 cars): no clear difference either way. The
     single-plan ceiling stays 0.460: the gap is cars in one race running different plans,
     which no one-plan-for-every-car method can close.
  P0 race chances   : O's pick with M's stop-lap rule (isolates O's timing problem)
  P1/P2/P3 per car    : a different plan PER CAR after qualifying -- O's race-level sequence
                        chances x how cars from the same grid band (P1) / the same team (P2) /
                        both (P3) ran each sequence earlier in the era (shrunk to the era average),
                        stop laps by M's rule. Compared with M.
     P0-P3 REJECTED 2026-10-05 (vs M, 2022-25, 1248 cars): sequence P0 0.251, P1 0.248, P2 0.247,
     P3 0.239 vs 0.234 (all n.s.); stop count 0.445-0.481 vs 0.470 (n.s.); first stop P0 8.56,
     P1 8.59, P2 8.62, P3 8.64 vs 8.20 laps (ALL WORSE, CIs exclude 0). 2026 (229 cars): no clear
     difference on any metric (first stop 9.1-9.4 vs 9.53, within 3 laps 0.28-0.31 vs 0.26, n.s.).
     Grid band and team habit add nothing measurable over the race-level chances, and any plan
     other than M's moves the first stop away from the wear-timed lap.
  Q1/Q2/Q3 stop count : M with a different stop count (race_stop_count_v1): Q1 adds votes from
                        earlier races at this track to M's wear votes; Q2 a logistic model on wear,
                        track history and era average; Q3 adds pit loss and practice track
                        temperature. Sequence and stop laps by M's rules. Compared with M, plus a
                        race-level stop count table and the signals by real stop count.
     Q1-Q3 NOT PROMOTED 2026-10-05: stop count Q1 0.466 / Q2 0.446 / Q3 0.426 vs M 0.470 in 2022-25
     (all n.s.); only practice wear separates the races' stop counts (see race_stop_count_v1).
  Wet-rule fix (2026-10-06, same cars, 2022-25, 80 races; wet_fix_same_cars.txt): 26 races
     flagged as rained-on but run on slicks only were added to M's history (M0 = old history):
     tyre order 0.201 vs M0 0.223 (WORSE), others n.s. -> REJECTED, history keeps the rain flag.
     Practice wear judged by tyres instead of the rain flag (+~16 FP2 sessions): tyre order
     +0.012, stop count +0.021, within 3 laps +0.014, all n.s. -> NOT PROMOTED (no clear gain).
  I  practice wear    : stop count voted by the 7 earlier races with the closest practice
                        long-run tyre wear; the era's most common sequence with that many stops
  G  CEILING (hindsight, not a method): the most common strategy actually run in the race
     itself -- the best any single-strategy-for-every-car prediction could score
B is production (strategy_service_v7 before the start tyre is known); others are compared to it.

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
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Sequence

from dotenv import load_dotenv
from sqlalchemy import create_engine

from race_strategy_backtest_v2 import race_clustered_ci
from race_strategy_precedent_v1 import historical_strategy_options, load_precedents
from race_strategy_simulator_v1 import Strategy
from race_stop_count_v1 import clip_stops, load_signals, model_stops, race_labels, signal_report, stops_wear_and_track
from race_strategy_timing_v1 import (
    CarStrategy,
    load_car_strategies,
    predict_era_mode,
    predict_era_mode_track_timing,
    predict_expected_match,
    predict_from_wear_sequence,
    PerCarPredictor,
    load_allocations,
    predict_from_allocation,
    predict_from_practice,
    predict_from_wear,
    predict_from_wear_timed,
    predict_last_race_at_track,
    predict_strategy,
    predict_track_adjusted,
)


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


def era_mode_real(history: Sequence[CarStrategy], era: str, total_laps: int) -> Strategy | None:
    """Method B -- exactly what strategy_service_v7 serves before the start tyre is known."""
    return predict_era_mode(history, regulation_era=era, total_laps=total_laps).strategy


WEAR: dict[int, float | None] = {}     # last run's practice wear per race (for the report)
LAST_CARS: list[CarStrategy] = []
ALLOCATION_COVERAGE: list[int] = [0, 0]
RACE_STOPS: list[tuple[int, int, dict[str, int | None]]] = []   # (race_id, real race stop count, predicted)
STOP_SIGNALS: list = [{}, {}]                                    # last run's signals and labels
STOP_METHODS = ("M wear + wear timing", "Q1 wear + track votes", "Q2 model: wear + track", "Q3 model: all signals")


def wear_report(cars: Sequence[CarStrategy], start_year: int, end_year: int) -> None:
    """Does practice wear go with the race's stop count at all? (all races with both)"""
    from race_strategy_timing_v1 import race_modes

    modes = race_modes(cars)
    pairs = [(WEAR[r], stops) for r, (_, when, stops) in modes.items()
             if WEAR.get(r) is not None and start_year <= when.year <= end_year]
    print(f"\npractice wear measured for {sum(w is not None for w in WEAR.values())} of {len(WEAR)} races")
    if len(pairs) >= 5:
        by_stops = defaultdict(list)
        for w, n in pairs:
            by_stops[n].append(w)
        print("  median practice wear by the race's most common stop count: " + ", ".join(
            f"{n} stop(s): {sorted(ws)[len(ws) // 2] * 100:+.3f}%/lap (n={len(ws)})" for n, ws in sorted(by_stops.items())))


def hindsight_mode(race_cars: Sequence[CarStrategy], total_laps: int) -> Strategy | None:
    """The race's own most common legal dry strategy (median stop laps) -- an upper bound for
    methods that give every car the same strategy, never a prediction."""
    from collections import Counter
    from statistics import median

    from race_strategy_precedent_v1 import build_strategy
    from race_strategy_timing_v1 import is_legal_dry

    legal = [c for c in race_cars if is_legal_dry(c.sequence)]
    if not legal:
        return None
    sequence = Counter(c.sequence for c in legal).most_common(1)[0][0]
    same = [c for c in legal if c.sequence == sequence]
    return build_strategy(sequence, [round(median(c.stop_fractions[i] for c in same) * total_laps)
                                     for i in range(len(sequence) - 1)], total_laps)


def run(db: Any, *, start_year: int, end_year: int, wet: bool) -> list[Score]:
    cars = load_car_strategies(db, start_year=2018, end_year=end_year)
    targets = defaultdict(list)
    for c in cars:
        if start_year <= c.season_year <= end_year and c.wet == wet:
            targets[(c.race_date, c.race_id)].append(c)
    precedent_cache: dict = {}
    from race_practice_pace_v1 import load_weekend_wear
    wear_by_race = {race_id: load_weekend_wear(db, race_id) for race_id in {c.race_id for c in cars}}
    LAST_CARS[:] = cars
    from race_practice_pace_v1 import load_weekend_compounds
    allocations = load_allocations(db)
    ALLOCATION_COVERAGE[:] = [len({r for _, r in targets} & set(allocations)), len(targets)]
    shares_by_race = {race_id: load_weekend_compounds(db, race_id) for _, race_id in targets}
    WEAR.clear()
    WEAR.update(wear_by_race)
    labels = race_labels(cars)
    signals = load_signals(db, cars, wear_by_race, end_year=end_year)
    STOP_SIGNALS[:] = [signals, labels]
    RACE_STOPS.clear()
    features_by_race: dict = {}
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
        era_mode = era_mode_real(history, era, total_laps)
        track_adjusted = predict_track_adjusted(history, track_id=track, regulation_era=era,
                                                total_laps=total_laps).strategy
        last_here = predict_last_race_at_track(history, track_id=track, regulation_era=era,
                                               total_laps=total_laps).strategy
        ceiling = hindsight_mode(race_cars, total_laps)
        shares = shares_by_race.get(race_id, {})
        with_compounds = predict_from_practice(history, wear=wear_by_race.get(race_id), wear_by_race=wear_by_race,
                                               shares=shares, regulation_era=era, total_laps=total_laps).strategy
        era_compounds = predict_from_practice(history, wear=None, wear_by_race=wear_by_race, shares=shares,
                                              regulation_era=era, total_laps=total_laps, use_wear=False).strategy
        from_allocation = predict_from_allocation(
            history, allocation=allocations.get(race_id), allocations=allocations, wear=wear_by_race.get(race_id),
            wear_by_race=wear_by_race, regulation_era=era, total_laps=total_laps).strategy
        wear_timed = predict_from_wear_timed(history, wear=wear_by_race.get(race_id), wear_by_race=wear_by_race,
                                             regulation_era=era, total_laps=total_laps).strategy
        from_wear = predict_from_wear(history, wear=wear_by_race.get(race_id), wear_by_race=wear_by_race,
                                      regulation_era=era, total_laps=total_laps).strategy
        wear_sequence = predict_from_wear_sequence(history, wear=wear_by_race.get(race_id), wear_by_race=wear_by_race,
                                                   regulation_era=era, total_laps=total_laps).strategy
        expected = predict_expected_match(history, wear=wear_by_race.get(race_id), wear_by_race=wear_by_race,
                                          regulation_era=era, total_laps=total_laps).strategy
        wear = wear_by_race.get(race_id)
        stop_counts = {
            "Q1 wear + track votes": stops_wear_and_track(labels, wear_by_race, wear=wear, track_id=track,
                                                          regulation_era=era, before=race_date),
            "Q2 model: wear + track": model_stops(signals, labels, race_id=race_id, feature_set="wear_track",
                                                  features_by_race=features_by_race),
            "Q3 model: all signals": model_stops(signals, labels, race_id=race_id, feature_set="all",
                                                 features_by_race=features_by_race),
        }
        stop_methods = {name: predict_from_wear_timed(history, wear=wear, wear_by_race=wear_by_race,
                                                      regulation_era=era, total_laps=total_laps,
                                                      stops=stops).strategy
                        for name, stops in stop_counts.items()}
        if race_id in labels:
            RACE_STOPS.append((race_id, labels[race_id].stops, {
                name: (len(strategy.sequence) - 1 if strategy else None)
                for name, strategy in (("M wear + wear timing", wear_timed), *stop_methods.items())}))
        timed = {}
        per_car = {name: PerCarPredictor(history, wear=wear_by_race.get(race_id), wear_by_race=wear_by_race,
                                         regulation_era=era, total_laps=total_laps, use_grid=g, use_team=t)
                   for name, g, t in (("P0 race chances, M timing", False, False),
                                      ("P1 per car: grid", True, False), ("P2 per car: team", False, True),
                                      ("P3 per car: grid + team", True, True))}
        for car in race_cars:
            for name, strategy in stop_methods.items():
                scores.append(score(race_id, name, strategy, car))
            for name, predictor in per_car.items():
                scores.append(score(race_id, name, predictor.predict(grid=car.grid, team_id=car.team_id).strategy, car))
            key = (car.grid or 99) <= 10
            if key not in dry_hierarchy:
                dry_hierarchy[key] = predict_strategy(history, track_id=track, regulation_era=era,
                                                      grid=car.grid, total_laps=total_laps).strategy
            scores.append(score(race_id, "A old production", production, car))
            scores.append(score(race_id, "B era mode (real stints)", era_mode, car))
            scores.append(score(race_id, "E track-adjusted", track_adjusted, car))
            scores.append(score(race_id, "F last race here", last_here, car))
            if key not in timed:
                timed[key] = predict_era_mode_track_timing(history, track_id=track, regulation_era=era,
                                                           grid=car.grid, total_laps=total_laps).strategy
            scores.append(score(race_id, "H era mode + track timing", timed[key], car))
            scores.append(score(race_id, "G CEILING (hindsight)", ceiling, car))
            scores.append(score(race_id, "I practice wear", from_wear, car))
            scores.append(score(race_id, "M wear + wear timing", wear_timed, car))
            scores.append(score(race_id, "N wear neighbours' sequence", wear_sequence, car))
            scores.append(score(race_id, "O expected match", expected, car))
            scores.append(score(race_id, "J practice wear + compounds", with_compounds, car))
            scores.append(score(race_id, "L Pirelli compounds", from_allocation, car))
            scores.append(score(race_id, "K era stops + compounds", era_compounds, car))
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


PRODUCTION = "B era mode (real stints)"
SERVED = "M wear + wear timing"       # what strategy_service_v7 serves when practice wear is known


def summarise(scores: Sequence[Score], baseline: str = PRODUCTION) -> list[tuple]:
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


def stop_count_report() -> None:
    """Race-level stop count: how often each method names the race's most common stop count."""
    if not RACE_STOPS:
        return
    print(f"\n=== race stop count ({len(RACE_STOPS)} races; truth = the race's most common stop count, 1-3) ===")
    print("method                           races right   share   when real = 1 / 2 / 3")
    for name in STOP_METHODS:
        right = [clip_stops(p[name]) == real for _, real, p in RACE_STOPS if p.get(name) is not None]
        by_real = []
        for k in (1, 2, 3):
            rows = [clip_stops(p[name]) == real for _, real, p in RACE_STOPS if real == k and p.get(name) is not None]
            by_real.append(f"{sum(rows)}/{len(rows)}")
        print(f"{name:32} {sum(right):4}/{len(right):<4}   {sum(right) / len(right):5.3f}   {' / '.join(by_real)}")
    signal_report(*STOP_SIGNALS, [r for r, _, _ in RACE_STOPS])


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
    n_cars = len([s for s in scores if s.method == PRODUCTION])
    print(f"\n=== STRATEGY PREDICTION BACKTEST ({'wet' if args.wet else 'dry'} races "
          f"{args.start_year}-{args.end_year}, {n_cars} cars, truth = real stints) ===")
    print("metric               method                           value   vs B (production) [90% CI, races resampled]")
    for metric, method, value, cmp in summarise(scores):
        tail = "" if cmp is None else f"{cmp[0]:+.3f} [{cmp[1]:+.3f}, {cmp[2]:+.3f}]  {cmp[3]}"
        print(f"{metric:20} {method:32} {value:6.3f}   {tail}")
    print(f"\n=== vs M, the method production serves (same cars) ===")
    for metric, method, value, cmp in summarise([s for s in scores if s.method in
                                                 (SERVED, "N wear neighbours' sequence", "O expected match",
                                                  "Q1 wear + track votes", "Q2 model: wear + track",
                                                  "Q3 model: all signals",
                                                  "B era mode (real stints)", "G CEILING (hindsight)")],
                                                baseline=SERVED):
        tail = "" if cmp is None else f"{cmp[0]:+.3f} [{cmp[1]:+.3f}, {cmp[2]:+.3f}]  {cmp[3]}"
        print(f"{metric:20} {method:32} {value:6.3f}   {tail}")
    stop_count_report()
    wear_report(LAST_CARS, args.start_year, args.end_year)
    print(f"Pirelli compound set known for {ALLOCATION_COVERAGE[0]} of {ALLOCATION_COVERAGE[1]} scored races")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
