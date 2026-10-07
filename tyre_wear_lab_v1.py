"""Tyre wear lab: which measure, from data we already have, best predicts a race's real tyre wear?

Earlier work judged practice wear only through the stop count, which safety cars, team
choices and 3-stop oddities blur (TYRE WEAR v2 / W3 in race_strategy_prediction_backtest_v1).
This lab judges wear measures against WEAR ITSELF:

  truth      each dry race's own tyre wear per compound (s/lap of tyre age), from its clean
             green-flag laps relative to the field median on the same lap (fuel burn and track
             evolution cancel) with a level per car -- race_tyre_model_v3.fit_tyre_model on that
             race alone. Hindsight, used only as the answer key.

  candidates per (race, compound), using only data from before the race:
    practice_current   median least-squares slope of FP2 (else FP1) long runs of the compound
                       (production's run_wear, kept in s/lap and split by compound)
    practice_v2        same runs, warm-up laps dropped, fuel burn added back, Theil-Sen slope
    practice_pooled    every FP1/FP2/FP3 long run of the compound, warm-up dropped, fuel added
                       back, each run centred on itself, one pooled slope (more laps, less noise)
    era_compound       the compound label's average real wear over earlier races of the era
                       (the naive baseline every measure must beat)
    track_history      this track's real wear for the compound in earlier races (this era
                       weight 1, earlier era 0.5)
    c_compound         the real wear of the same Pirelli C-compound in earlier races of the era
    stacked            walk-forward least-squares blend of practice_pooled, track_history and
                       c_compound (missing values imputed, with indicators), fitted only on
                       earlier races

  scores    rank correlation with the truth (90% interval, races resampled), and the absolute
            error after a walk-forward linear calibration (each candidate mapped to race units by
            a line fitted on earlier races only), compared pair by pair with practice_current
            and with era_compound.

The winner is decided here, by how well it predicts real wear, and only then tested once in
the strategy backtest -- so the strategy test is not one more try among many.

    python tyre_wear_lab_v1.py --start-year 2022 --end-year 2025
"""

from __future__ import annotations

import argparse
import os
import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from statistics import mean, median
from typing import Any, Callable, Iterable, Sequence

import numpy as np
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from race_practice_pace_v1 import FUEL_SECONDS_PER_LAP, MIN_WEAR_RUN_LAPS, WARMUP_LAPS, long_runs, theil_sen
from race_strategy_backtest_v2 import race_clustered_ci

COMPOUNDS = ("SOFT", "MEDIUM", "HARD")
PRACTICE_SESSIONS = ("FP1", "FP2", "FP3")
MIN_RUNS = 2                    # runs of a compound needed for a practice measure
MIN_POOLED_LAPS = 12            # laps (after warm-up) needed for the pooled measure
EARLIER_ERA_WEIGHT = 0.5
MIN_C_PAIRS = 3                 # earlier races with the same C-compound
MIN_CALIBRATION_PAIRS = 20      # earlier pairs needed to map a candidate to race units
MIN_STACK_PAIRS = 40
CANDIDATES = ("practice_current", "practice_v2", "practice_pooled", "era_compound", "track_history",
              "c_compound", "stacked")


@dataclass(frozen=True)
class RaceInfo:
    race_id: int
    race_date: date
    season_year: int
    track_id: int
    regulation_era: str
    c_compounds: dict[str, int]     # label -> Pirelli C number ({} when not nominated)


@dataclass(frozen=True)
class PracticeRun:
    session_type: str
    compound: str
    laps: tuple[tuple[int, float], ...]     # (lap_number, lap_time)


# --- truth ---------------------------------------------------------------------------------

def race_truth(obs_by_race: dict[int, list]) -> dict[int, dict[str, float]]:
    """race_id -> compound -> real wear (s/lap), from that race's own laps only."""
    from race_tyre_model_v3 import fit_tyre_model

    out = {}
    for race_id, obs in obs_by_race.items():
        model = fit_tyre_model(obs)
        if model is not None:
            out[race_id] = {c: s for c, (_, s) in model.params.items() if c in COMPOUNDS}
    return out


# --- practice measures -----------------------------------------------------------------------

def _ls_slope(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    mx, my = mean(xs), mean(ys)
    den = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den if den else None


def _main_session(runs: Sequence[PracticeRun]) -> list[PracticeRun]:
    """FP2's runs, else FP1's (production's choice of session)."""
    for session in ("FP2", "FP1"):
        picked = [r for r in runs if r.session_type == session]
        if picked:
            return picked
    return []


def _corrected(run: PracticeRun) -> list[tuple[float, float]] | None:
    """Warm-up laps dropped, fuel burn added back: [(lap, corrected time)]."""
    kept = sorted(run.laps)[WARMUP_LAPS.get(run.compound, 1):]
    if len(kept) < MIN_WEAR_RUN_LAPS - 1:
        return None
    start = kept[0][0]
    return [(float(n), t + FUEL_SECONDS_PER_LAP * (n - start)) for n, t in kept]


def practice_current(runs: Sequence[PracticeRun], compound: str) -> float | None:
    slopes = [s for r in _main_session(runs) if r.compound == compound and len(r.laps) >= MIN_WEAR_RUN_LAPS
              and (s := _ls_slope([n for n, _ in r.laps], [t for _, t in r.laps])) is not None]
    return median(slopes) if len(slopes) >= MIN_RUNS else None


def practice_v2(runs: Sequence[PracticeRun], compound: str) -> float | None:
    slopes = []
    for r in _main_session(runs):
        if r.compound == compound and (pts := _corrected(r)) is not None:
            if (s := theil_sen([x for x, _ in pts], [y for _, y in pts])) is not None:
                slopes.append(s)
    return median(slopes) if len(slopes) >= MIN_RUNS else None


def practice_pooled(runs: Sequence[PracticeRun], compound: str) -> float | None:
    """One slope over every practice run of the compound, each run centred on its own mean."""
    num = den = 0.0
    n_runs = n_laps = 0
    for r in runs:
        if r.compound != compound or (pts := _corrected(r)) is None:
            continue
        mx, my = mean(x for x, _ in pts), mean(y for _, y in pts)
        num += sum((x - mx) * (y - my) for x, y in pts)
        den += sum((x - mx) ** 2 for x, _ in pts)
        n_runs, n_laps = n_runs + 1, n_laps + len(pts)
    if n_runs < MIN_RUNS or n_laps < MIN_POOLED_LAPS or den == 0:
        return None
    return num / den


# --- history measures ------------------------------------------------------------------------

def _earlier(truth: dict[int, dict[str, float]], info: dict[int, RaceInfo], target: RaceInfo):
    return [(info[r], by_c) for r, by_c in truth.items() if r in info and info[r].race_date < target.race_date]


def era_compound(truth, info, target: RaceInfo, compound: str) -> float | None:
    values = [by_c[compound] for i, by_c in _earlier(truth, info, target)
              if i.regulation_era == target.regulation_era and compound in by_c]
    return mean(values) if values else None


def track_history(truth, info, target: RaceInfo, compound: str) -> float | None:
    rows = [(by_c[compound], 1.0 if i.regulation_era == target.regulation_era else EARLIER_ERA_WEIGHT)
            for i, by_c in _earlier(truth, info, target) if i.track_id == target.track_id and compound in by_c]
    return sum(v * w for v, w in rows) / sum(w for _, w in rows) if rows else None


def _c_key(i: RaceInfo, compound: str) -> tuple | None:
    c = i.c_compounds.get(compound)
    return None if c is None else (i.regulation_era, i.season_year >= 2023, c)   # C numbers redefined in 2023


def c_compound(truth, info, target: RaceInfo, compound: str) -> float | None:
    key = _c_key(target, compound)
    if key is None:
        return None
    values = [v for i, by_c in _earlier(truth, info, target) for label, v in by_c.items() if _c_key(i, label) == key]
    return mean(values) if len(values) >= MIN_C_PAIRS else None


# --- evaluation ------------------------------------------------------------------------------

@dataclass
class Pair:
    race_id: int
    race_date: date
    compound: str
    truth: float
    values: dict[str, float | None]
    calibrated: dict[str, float | None]


def build_pairs(truth, info, practice: dict[int, list[PracticeRun]]) -> list[Pair]:
    """Every (race, compound) with a real wear value and every raw candidate value."""
    pairs = []
    for race_id, by_c in sorted(truth.items(), key=lambda kv: info[kv[0]].race_date if kv[0] in info else date.min):
        if race_id not in info:
            continue
        target, runs = info[race_id], practice.get(race_id, [])
        for compound, value in by_c.items():
            pairs.append(Pair(race_id, target.race_date, compound, value, {
                "practice_current": practice_current(runs, compound),
                "practice_v2": practice_v2(runs, compound),
                "practice_pooled": practice_pooled(runs, compound),
                "era_compound": era_compound(truth, info, target, compound),
                "track_history": track_history(truth, info, target, compound),
                "c_compound": c_compound(truth, info, target, compound),
            }, {}))
    return pairs


STACK_FEATURES = ("practice_pooled", "track_history", "c_compound", "era_compound")


def _stack_row(values: dict, means: dict) -> list[float]:
    row = [1.0]
    for f in STACK_FEATURES:
        v = values.get(f)
        row += [means[f] if v is None else v, 1.0 if v is None else 0.0]
    return row


def add_stacked(pairs: list[Pair]) -> None:
    """Walk-forward blend: for each race, least squares on every earlier pair."""
    for p in pairs:
        train = [q for q in pairs if q.race_date < p.race_date]
        if len(train) < MIN_STACK_PAIRS:
            p.values["stacked"] = None
            continue
        means = {f: mean(vs) if (vs := [q.values[f] for q in train if q.values.get(f) is not None]) else 0.0
                 for f in STACK_FEATURES}
        x = np.array([_stack_row(q.values, means) for q in train])
        y = np.array([q.truth for q in train])
        coef, *_ = np.linalg.lstsq(x, y, rcond=None)
        p.values["stacked"] = float(np.array(_stack_row(p.values, means)) @ coef)


def add_calibrated(pairs: list[Pair]) -> None:
    """Each candidate mapped to race units by a line fitted on earlier pairs only."""
    for p in pairs:
        for name in CANDIDATES:
            x = p.values.get(name)
            train = [(q.values[name], q.truth) for q in pairs
                     if q.race_date < p.race_date and q.values.get(name) is not None]
            if x is None or len(train) < MIN_CALIBRATION_PAIRS or np.ptp([a for a, _ in train]) == 0:
                p.calibrated[name] = None
                continue
            b, a = np.polyfit([a for a, _ in train], [t for _, t in train], 1)
            p.calibrated[name] = float(a + b * x)


def spearman(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) < 3:
        return float("nan")
    ra, rb = np.argsort(np.argsort(a)), np.argsort(np.argsort(b))
    return float(np.corrcoef(ra, rb)[0, 1])


def clustered_spearman(pairs: Sequence[Pair], value: Callable[[Pair], float | None], *, seed: int = 11,
                       draws: int = 2000) -> tuple[float, float, float, int]:
    """Rank correlation with the truth, 90% interval resampling races; n pairs."""
    usable = [(p.race_id, v, p.truth) for p in pairs if (v := value(p)) is not None]
    if len(usable) < 3:
        return float("nan"), float("nan"), float("nan"), len(usable)
    by_race = defaultdict(list)
    for r, v, t in usable:
        by_race[r].append((v, t))
    races = list(by_race)
    rng = random.Random(seed)
    boots = []
    for _ in range(draws):
        sample = [vt for r in rng.choices(races, k=len(races)) for vt in by_race[r]]
        boots.append(spearman([v for v, _ in sample], [t for _, t in sample]))
    boots = sorted(b for b in boots if not np.isnan(b))
    rho = spearman([v for _, v, _ in usable], [t for _, _, t in usable])
    if not boots:
        return rho, float("nan"), float("nan"), len(usable)
    return rho, boots[int(0.05 * len(boots))], boots[int(0.95 * len(boots)) - 1], len(usable)


class _Row:
    def __init__(self, race_id: int, value: float):
        self.race_id, self.value = race_id, value


def error_vs(pairs: Sequence[Pair], name: str, baseline: str):
    """Mean (|calibrated name - truth| - |calibrated baseline - truth|) over pairs both cover."""
    rows = [_Row(p.race_id, abs(p.calibrated[name] - p.truth) - abs(p.calibrated[baseline] - p.truth))
            for p in pairs if p.calibrated.get(name) is not None and p.calibrated.get(baseline) is not None]
    return race_clustered_ci(rows, lambda r: r.value) + (len(rows),) if rows else None


def report(pairs: Sequence[Pair], label: str) -> None:
    races = {p.race_id for p in pairs}
    print(f"\n=== TYRE WEAR LAB: {label} ({len(pairs)} race-compound pairs, {len(races)} races) ===")
    print(f"real wear (s/lap of tyre age), median by compound: " + ", ".join(
        f"{c} {median(vs):+.4f} (n={len(vs)})" for c in COMPOUNDS
        if (vs := [p.truth for p in pairs if p.compound == c])))
    print("\ncandidate          pairs  rank corr with real wear [90% CI]   calibrated error s/lap [90% CI]")
    for name in CANDIDATES:
        rho, lo, hi, n = clustered_spearman(pairs, lambda p, k=name: p.values.get(k))
        errs = [_Row(p.race_id, abs(p.calibrated[name] - p.truth)) for p in pairs if p.calibrated.get(name) is not None]
        err = race_clustered_ci(errs, lambda r: r.value) if errs else (float("nan"),) * 3
        print(f"{name:18} {n:5}  {rho:+.3f} [{lo:+.3f}, {hi:+.3f}]            "
              f"{err[0]:.4f} [{err[1]:.4f}, {err[2]:.4f}] (n={len(errs)})")
    for baseline in ("practice_current", "era_compound"):
        print(f"\nerror vs {baseline}, same pairs (negative = closer to the real wear):")
        for name in CANDIDATES:
            if name == baseline or (cmp := error_vs(pairs, name, baseline)) is None:
                continue
            d, lo, hi, n = cmp
            verdict = "BETTER" if hi < 0 else "WORSE" if lo > 0 else "no clear difference"
            print(f"  {name:18} {d:+.4f} [{lo:+.4f}, {hi:+.4f}]  n={n:4}  {verdict}")
    print("\nby compound, rank corr with real wear:")
    for c in COMPOUNDS:
        sub = [p for p in pairs if p.compound == c]
        cells = []
        for name in CANDIDATES:
            rho, _, _, n = clustered_spearman(sub, lambda p, k=name: p.values.get(k), draws=1)
            cells.append(f"{name} {rho:+.2f} (n={n})")
        print(f"  {c:6} " + ", ".join(cells))


# --- loading ---------------------------------------------------------------------------------

def load_race_info(db: Any) -> dict[int, RaceInfo]:
    from pirelli_compounds_v1 import c_number

    noms = defaultdict(dict)
    for race_id, label, c in db.execute(text("SELECT race_id, label, c_compound FROM race_compound_nominations")):
        try:
            noms[int(race_id)][str(label).upper()] = c_number(c)
        except ValueError:
            pass
    return {int(r): RaceInfo(int(r), d, int(y), int(t), era, dict(noms.get(int(r), {})))
            for r, d, y, t, era in db.execute(text("""
                SELECT id, race_date, season_year, track_id, regulation_era FROM races
                WHERE regulation_era IS NOT NULL"""))}


def load_practice_runs(db: Any, race_ids: Iterable[int]) -> dict[int, list[PracticeRun]]:
    """Every dry FP1/FP2/FP3 long run per race (wet sessions skipped, production's rule)."""
    from race_practice_pace_v1 import session_is_wet

    out = {}
    for race_id in race_ids:
        runs = []
        for session_id, session_type, rain in db.execute(text("""
                SELECT s.id, s.session_type, COALESCE(sw.rainfall, FALSE)
                FROM sessions s LEFT JOIN session_weather sw ON sw.session_id = s.id
                WHERE s.race_id = :r AND s.session_type IN ('FP1', 'FP2', 'FP3')"""), {"r": race_id}).all():
            if session_is_wet(db, session_id, rain):
                continue
            laps = defaultdict(list)
            for entry, n, t, c in db.execute(text("""
                    SELECT race_entry_id, lap_number, lap_time, tire_compound FROM laps
                    WHERE session_id = :s AND COALESCE(is_deleted, FALSE) = FALSE"""), {"s": session_id}):
                laps[int(entry)].append((int(n), float(t) if t is not None else None, c))
            for rows in laps.values():
                runs += [PracticeRun(session_type, compound, tuple(run)) for compound, run in long_runs(rows)]
        out[race_id] = runs
    return out


def run(db: Any) -> list[Pair]:
    from race_tyre_model_v3 import load_tyre_observations

    info = load_race_info(db)
    obs_by_race = defaultdict(list)
    for o in load_tyre_observations(db):
        obs_by_race[o.race_id].append(o)
    truth = race_truth(obs_by_race)
    practice = load_practice_runs(db, truth)
    pairs = build_pairs(truth, info, practice)
    add_stacked(pairs)
    add_calibrated(pairs)
    return pairs


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start-year", type=int, default=2022)
    parser.add_argument("--end-year", type=int, default=2025)
    args = parser.parse_args()
    with create_engine(os.environ["DATABASE_URL"]).connect() as db:
        pairs = run(db)
    scored = [p for p in pairs if args.start_year <= p.race_date.year <= args.end_year]
    if not scored:
        print("No race-compound pairs scored.")
        return 1
    report(scored, f"dry races {args.start_year}-{args.end_year}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
