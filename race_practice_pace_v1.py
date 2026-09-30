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


def load_weekend_wear(db: Any, race_id: int) -> float | None:
    """Median long-run wear over all drivers' runs in FP2 (else FP1); None if too few runs
    or the session was wet."""
    for session_type in ("FP2", "FP1"):
        session = db.execute(text("""
            SELECT s.id, COALESCE(sw.rainfall, FALSE) AS wet
            FROM sessions s LEFT JOIN session_weather sw ON sw.session_id = s.id
            WHERE s.race_id = :r AND s.session_type = :t
        """), {"r": race_id, "t": session_type}).mappings().first()
        if session is None:
            continue
        if session_is_wet(db, session["id"], session["wet"]):
            return None
        laps = defaultdict(list)
        for driver, n, t, c in db.execute(text("""
            SELECT re.driver_id, l.lap_number, l.lap_time, l.tire_compound
            FROM laps l JOIN race_entries re ON re.id = l.race_entry_id
            WHERE l.session_id = :s AND COALESCE(l.is_deleted, FALSE) = FALSE
        """), {"s": session["id"]}):
            laps[int(driver)].append((int(n), float(t) if t is not None else None, c))
        wear = [w for rows in laps.values() for _, run in long_runs(rows) if (w := run_wear(run)) is not None]
        return median(wear) if len(wear) >= MIN_WEAR_RUNS else None
    return None


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
