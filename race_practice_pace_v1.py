"""Practice long-run pace: each driver's race-trim pace from Friday practice.

Qualifying shows one-lap speed on low fuel; long runs in practice show pace on race
fuel and worn tyres, which is what the race rewards. For each weekend:

  session   FP2 (race-simulation session on a normal weekend), else FP1 (sprint
            weekends have only one practice); skipped if that session was wet
  long run  >= MIN_RUN_LAPS consecutive laps each within RUN_LAP_FACTOR of the
            driver's fastest lap (out/in/cool-down laps are 20%+ slower and break runs),
            then laps more than RUN_TRIM over the run's median (traffic, mistakes) dropped
            v1 used 7%: heavy-fuel runs are often 6-9% off a low-fuel quali lap, so only
            ~36% of drivers had a long run although every weekend has practice laps
  pace      median long-run lap per compound; a driver's gap on a compound is to the
            best median on it (compounds need >= MIN_DRIVERS drivers); the gaps are
            averaged over the compounds a driver ran and re-based so the best is 0

Fuel loads are team choices, so single values are noisy; the scoreboard tests
whether the signal survives that noise before anything uses it.
"""

from __future__ import annotations

from collections import defaultdict
from statistics import mean, median
from typing import Any, Iterable

from sqlalchemy import text

RUN_LAP_FACTOR = 1.10
RUN_TRIM = 1.02
MIN_RUN_LAPS = 5
MIN_DRIVERS = 4
MAX_PRACTICE_GAP = 0.05      # beyond this the run is not representative (traffic, damage)


def long_runs(laps: Iterable[tuple[int, float | None, str | None]]) -> list[tuple[str, list[tuple[int, float]]]]:
    """Long runs in lap order: [(compound, [(lap_number, lap_time), ...])], trimmed of laps
    more than RUN_TRIM over the run's median. laps: (lap_number, lap_time, compound)."""
    timed = sorted((n, t, (c or "UNKNOWN").upper()) for n, t, c in laps if t is not None and t > 0)
    if not timed:
        return []
    limit = min(t for _, t, _ in timed) * RUN_LAP_FACTOR
    runs: list[list[tuple[int, float, str]]] = []
    for lap in timed:
        n, t, c = lap
        if t > limit:
            runs.append([])
            continue
        if runs and runs[-1] and runs[-1][-1][0] == n - 1 and runs[-1][-1][2] == c:
            runs[-1].append(lap)
        else:
            runs.append([lap])
    out = []
    for run in runs:
        if len(run) < MIN_RUN_LAPS:
            continue
        centre = median(t for _, t, _ in run)
        kept = [(n, t) for n, t, _ in run if t <= centre * RUN_TRIM]
        if len(kept) >= MIN_RUN_LAPS:
            out.append((run[0][2], kept))
    return out


def long_run_laps(laps: Iterable[tuple[int, float | None, str | None]]) -> dict[str, list[float]]:
    """compound -> lap times from long runs. laps: (lap_number, lap_time, compound)."""
    out: dict[str, list[float]] = defaultdict(list)
    for compound, run in long_runs(laps):
        out[compound] += [t for _, t in run]
    return dict(out)


def practice_gaps(runs: dict[int, dict[str, list[float]]]) -> dict[int, float]:
    """driver -> long-run gap to the best driver (fraction), from per-compound medians."""
    medians = defaultdict(dict)                    # compound -> driver -> median
    for driver, by_compound in runs.items():
        for compound, times in by_compound.items():
            medians[compound][driver] = median(times)
    per_driver = defaultdict(list)
    for compound, by_driver in medians.items():
        if len(by_driver) < MIN_DRIVERS:
            continue
        best = min(by_driver.values())
        for driver, m in by_driver.items():
            per_driver[driver].append(m / best - 1)
    gaps = {d: mean(v) for d, v in per_driver.items()}
    if not gaps:
        return {}
    base = min(gaps.values())
    return {d: g - base for d, g in gaps.items() if g - base <= MAX_PRACTICE_GAP}


WET_TYRE_SHARE = 0.1   # a session with >= 10% of timed laps on intermediates/wets was wet


def session_is_wet(db: Any, session_id: int, weather_says_wet: bool | None) -> bool:
    """Rain per session_weather where it is loaded (R, Q, FP2), else from the tyres used:
    weather is not loaded for FP1 or sprints, and a wet session is not a dry pace sample."""
    if weather_says_wet:
        return True
    share = db.execute(text("""
        SELECT AVG((upper(COALESCE(tire_compound, '')) IN ('INTERMEDIATE', 'WET'))::int)
        FROM laps WHERE session_id = :s AND lap_time IS NOT NULL
    """), {"s": session_id}).scalar()
    return bool(share is not None and float(share) >= WET_TYRE_SHARE)


def load_practice_gaps_all(db: Any, race_id: int) -> dict[int, float]:
    """(research) Long-run gap from FP1 AND FP2: each session's gaps measured against that
    session's best driver, then averaged over the sessions a driver has a long run in and
    re-based so the best is 0. More drivers covered than FP2-else-FP1."""
    per_driver = defaultdict(list)
    for session_type in ("FP1", "FP2"):
        session = db.execute(text("""
            SELECT s.id, COALESCE(sw.rainfall, FALSE) AS wet
            FROM sessions s LEFT JOIN session_weather sw ON sw.session_id = s.id
            WHERE s.race_id = :r AND s.session_type = :t
        """), {"r": race_id, "t": session_type}).mappings().first()
        if session is None or session_is_wet(db, session["id"], session["wet"]):
            continue
        laps = defaultdict(list)
        for driver, n, t, c in db.execute(text("""
            SELECT re.driver_id, l.lap_number, l.lap_time, l.tire_compound
            FROM laps l JOIN race_entries re ON re.id = l.race_entry_id
            WHERE l.session_id = :s AND COALESCE(l.is_deleted, FALSE) = FALSE
        """), {"s": session["id"]}):
            laps[int(driver)].append((int(n), float(t) if t is not None else None, c))
        runs = {d: r for d, rows in laps.items() if (r := long_run_laps(rows))}
        for d, g in practice_gaps(runs).items():
            per_driver[d].append(g)
    gaps = {d: mean(v) for d, v in per_driver.items()}
    if not gaps:
        return {}
    base = min(gaps.values())
    return {d: g - base for d, g in gaps.items()}


def load_practice_gaps(db: Any, race_id: int) -> tuple[dict[int, float], str | None]:
    """(driver -> long-run gap, session used) for a race weekend; ({}, None) if unusable."""
    for session_type in ("FP2", "FP1"):
        session = db.execute(text("""
            SELECT s.id, COALESCE(sw.rainfall, FALSE) AS wet
            FROM sessions s LEFT JOIN session_weather sw ON sw.session_id = s.id
            WHERE s.race_id = :r AND s.session_type = :t
        """), {"r": race_id, "t": session_type}).mappings().first()
        if session is None:
            continue
        if session_is_wet(db, session["id"], session["wet"]):
            return {}, None
        laps = defaultdict(list)
        for driver, n, t, c in db.execute(text("""
            SELECT re.driver_id, l.lap_number, l.lap_time, l.tire_compound
            FROM laps l JOIN race_entries re ON re.id = l.race_entry_id
            WHERE l.session_id = :s AND COALESCE(l.is_deleted, FALSE) = FALSE
        """), {"s": session["id"]}):
            laps[int(driver)].append((int(n), float(t) if t is not None else None, c))
        runs = {d: r for d, rows in laps.items() if (r := long_run_laps(rows))}
        return practice_gaps(runs), session_type
    return {}, None


# --- sprint race pace -------------------------------------------------------------
# A sprint is ~100 km of real racing on the Saturday before the Grand Prix: race starts,
# race fuel, tyre management. Its clean pace (same rule as the race pace model: laps 2+
# within 105% of the driver's median) is a direct race-pace sample known before the race.

MAX_SPRINT_GAP = 0.05
MIN_SPRINT_SHARE = 0.6    # clean pace for at least 60% of the sprint's starters, else not used


def load_sprint_gaps(db: Any, race_id: int) -> dict[int, float]:
    """driver -> clean sprint-race pace gap to the best driver; {} without a dry sprint."""
    from race_pace_from_quali_v1 import clean_race_pace

    session = db.execute(text("""
        SELECT s.id, COALESCE(sw.rainfall, FALSE) AS wet
        FROM sessions s LEFT JOIN session_weather sw ON sw.session_id = s.id
        WHERE s.race_id = :r AND s.session_type = 'S'
    """), {"r": race_id}).mappings().first()
    if session is None or session_is_wet(db, session["id"], session["wet"]):
        return {}
    laps = defaultdict(list)
    for driver, n, t in db.execute(text("""
        SELECT re.driver_id, l.lap_number, l.lap_time
        FROM laps l JOIN race_entries re ON re.id = l.race_entry_id
        WHERE l.session_id = :s
    """), {"s": session["id"]}):
        laps[int(driver)].append((int(n), float(t) if t is not None else None))
    pace = {d: p for d, rows in laps.items() if (p := clean_race_pace(rows)) is not None}
    # a partly loaded sprint (2026 R12 had laps for 4 of 22 drivers) would centre the
    # correction on a handful of cars: use it only when it covers most of the field
    starters = db.execute(text("""
        SELECT COUNT(*) FROM race_results rr WHERE rr.session_id = :s
          AND lower(COALESCE(rr.status, '')) NOT IN ('did not start', 'withdrew', 'did not qualify', 'excluded')
    """), {"s": session["id"]}).scalar() or 0
    if len(pace) < max(MIN_DRIVERS, MIN_SPRINT_SHARE * starters, 10 if not starters else 0):
        return {}
    best = min(pace.values())
    return {d: p / best - 1 for d, p in pace.items() if p / best - 1 <= MAX_SPRINT_GAP}


# --- tyre wear this weekend ----------------------------------------------------------
# How fast long-run lap times rise per lap, relative to the lap time (fuel burn-off makes
# this smaller than the true tyre wear, by about the same amount at every track). Higher =
# the tyres fade faster = more stops. Measured on the weekend itself, before the race.

MIN_WEAR_RUN_LAPS = 6
MIN_WEAR_RUNS = 4


def run_wear(run: list[tuple[int, float]]) -> float | None:
    """Least-squares lap-time slope per lap / median lap time; None for short runs."""
    if len(run) < MIN_WEAR_RUN_LAPS:
        return None
    xs = [n for n, _ in run]
    ys = [t for _, t in run]
    mx, my = mean(xs), mean(ys)
    den = sum((x - mx) ** 2 for x in xs)
    return (sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den) / median(ys) if den else None


def load_weekend_runs(db: Any, race_id: int, *, use_rain_flag: bool = True
                      ) -> list[tuple[str, list[tuple[int, float]]]] | None:
    """Every driver's long runs [(compound, [(lap_number, lap_time), ...])] in FP2 (else FP1);
    None when the session is missing or wet. use_rain_flag=False judges wet by the tyres alone."""
    for session_type in ("FP2", "FP1"):
        session = db.execute(text("""
            SELECT s.id, COALESCE(sw.rainfall, FALSE) AS wet
            FROM sessions s LEFT JOIN session_weather sw ON sw.session_id = s.id
            WHERE s.race_id = :r AND s.session_type = :t
        """), {"r": race_id, "t": session_type}).mappings().first()
        if session is None:
            continue
        if session_is_wet(db, session["id"], session["wet"] if use_rain_flag else None):
            return None
        laps = defaultdict(list)
        for driver, n, t, c in db.execute(text("""
            SELECT re.driver_id, l.lap_number, l.lap_time, l.tire_compound
            FROM laps l JOIN race_entries re ON re.id = l.race_entry_id
            WHERE l.session_id = :s AND COALESCE(l.is_deleted, FALSE) = FALSE
        """), {"s": session["id"]}):
            laps[int(driver)].append((int(n), float(t) if t is not None else None, c))
        return [run for rows in laps.values() for run in long_runs(rows)]
    return None


def load_weekend_wear(db: Any, race_id: int, *, use_rain_flag: bool = True) -> float | None:
    """Median long-run wear over all drivers' runs in FP2 (else FP1); None if too few runs
    or the session was wet. use_rain_flag=False judges wet by the tyres alone (research: the
    rainfall flag is set by any rain sample, and 13 FP2 sessions flagged as rained-on had no
    lap on a wet tyre)."""
    runs = load_weekend_runs(db, race_id, use_rain_flag=use_rain_flag)
    if runs is None:
        return None
    wear = [w for _, run in runs if (w := run_wear(run)) is not None]
    return median(wear) if len(wear) >= MIN_WEAR_RUNS else None


# --- tyre wear v2 (research): the method of f1-race-replay's tyre model, tested here -------
# Three changes to run_wear, each a known source of noise:
#   warm-up    the first lap(s) of a run are not wear (tyres coming in): dropped
#   fuel       the car gets ~FUEL_SECONDS_PER_LAP faster every lap as fuel burns, hiding
#              wear: added back (0.032 s/kg x 1.6 kg/lap, the same prior f1-race-replay uses)
#   outliers   Theil-Sen slope (median of pairwise slopes) instead of least squares
# and, separately, per-compound normalisation: a weekend where teams long-ran softs looks
# "high wear" only because softs wear faster. Each run's wear is divided by its compound's
# median wear over EARLIER weekends, so the weekend number compares like with like.

FUEL_SECONDS_PER_LAP = 0.032 * 1.6
WARMUP_LAPS = {"SOFT": 1, "MEDIUM": 2, "HARD": 2}
MIN_V2_RUN_LAPS = 5          # laps left after the warm-up
MIN_BASELINE_RUNS = 20       # earlier runs of a compound needed for its baseline


def theil_sen(xs: list[float], ys: list[float]) -> float | None:
    slopes = [(ys[j] - ys[i]) / (xs[j] - xs[i]) for i in range(len(xs)) for j in range(i + 1, len(xs))
              if xs[j] != xs[i]]
    return median(slopes) if slopes else None


def run_wear_v2(compound: str, run: list[tuple[int, float]]) -> float | None:
    """Fuel-corrected Theil-Sen lap-time slope per lap / median lap time, warm-up laps dropped;
    None for short runs."""
    kept = sorted(run)[WARMUP_LAPS.get(compound, 1):]
    if len(kept) < MIN_V2_RUN_LAPS:
        return None
    start = kept[0][0]
    xs = [float(n) for n, _ in kept]
    ys = [t + FUEL_SECONDS_PER_LAP * (n - start) for n, t in kept]
    slope = theil_sen(xs, ys)
    return slope / median(t for _, t in kept) if slope is not None else None


def run_wears_v2(runs: Iterable[tuple[str, list[tuple[int, float]]]] | None) -> list[tuple[str, float]] | None:
    """[(compound, v2 run wear)] for a weekend's long runs (computed once, reused below)."""
    if runs is None:
        return None
    return [(c, w) for c, run in runs if (w := run_wear_v2(c, run)) is not None]


def weekend_wear_robust(wears: list[tuple[str, float]] | None) -> float | None:
    """v2 run wear, compounds pooled (median over runs)."""
    if wears is None or len(wears) < MIN_WEAR_RUNS:
        return None
    return median(w for _, w in wears)


def compound_baselines(wears_by_race: dict[int, list | None], races: Iterable[int]) -> dict[str, float]:
    """compound -> median v2 run wear over the given (earlier) races; compounds with fewer
    than MIN_BASELINE_RUNS runs, or a non-positive median, are left out."""
    by_compound: dict[str, list[float]] = defaultdict(list)
    for r in races:
        for c, w in wears_by_race.get(r) or ():
            by_compound[c].append(w)
    return {c: m for c, ws in by_compound.items() if len(ws) >= MIN_BASELINE_RUNS and (m := median(ws)) > 0}


def weekend_wear_per_compound(wears: list[tuple[str, float]] | None, baselines: dict[str, float]) -> float | None:
    """Median over runs of v2 run wear / its compound's baseline: 1.0 = a typical weekend for
    whatever compounds were run, above 1 = the tyres fade faster than usual."""
    if wears is None:
        return None
    ratios = [w / baselines[c] for c, w in wears if c in baselines]
    return median(ratios) if len(ratios) >= MIN_WEAR_RUNS else None


# --- which tyres teams practised the race on ------------------------------------------

def load_weekend_compounds(db: Any, race_id: int) -> dict[str, float]:
    """Share of long-run laps on each dry compound in FP2 (else FP1); {} if unusable. Teams
    rehearse race stints on the compounds they expect to race."""
    for session_type in ("FP2", "FP1"):
        session = db.execute(text("""
            SELECT s.id, COALESCE(sw.rainfall, FALSE) AS wet
            FROM sessions s LEFT JOIN session_weather sw ON sw.session_id = s.id
            WHERE s.race_id = :r AND s.session_type = :t
        """), {"r": race_id, "t": session_type}).mappings().first()
        if session is None:
            continue
        if session_is_wet(db, session["id"], session["wet"]):
            return {}
        laps = defaultdict(list)
        for driver, n, t, c in db.execute(text("""
            SELECT re.driver_id, l.lap_number, l.lap_time, l.tire_compound
            FROM laps l JOIN race_entries re ON re.id = l.race_entry_id
            WHERE l.session_id = :s AND COALESCE(l.is_deleted, FALSE) = FALSE
        """), {"s": session["id"]}):
            laps[int(driver)].append((int(n), float(t) if t is not None else None, c))
        counts = defaultdict(int)
        for rows in laps.values():
            for compound, run in long_runs(rows):
                if compound in ("SOFT", "MEDIUM", "HARD"):
                    counts[compound] += len(run)
        total = sum(counts.values())
        return {c: n / total for c, n in counts.items()} if total >= 50 else {}
    return {}
