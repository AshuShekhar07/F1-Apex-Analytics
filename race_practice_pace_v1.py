"""Practice long-run pace: each driver's race-trim pace from Friday practice.

Qualifying shows one-lap speed on low fuel; long runs in practice show pace on race
fuel and worn tyres, which is what the race rewards. For each weekend:

  session   FP2 (race-simulation session on a normal weekend), else FP1 (sprint
            weekends have only one practice); skipped if that session was wet
  long run  >= MIN_RUN_LAPS consecutive laps each within RUN_LAP_FACTOR of the
            driver's fastest lap -- single-lap quali runs and in/out laps drop out
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

RUN_LAP_FACTOR = 1.07
MIN_RUN_LAPS = 5
MIN_DRIVERS = 4
MAX_PRACTICE_GAP = 0.05      # beyond this the run is not representative (traffic, damage)


def long_run_laps(laps: Iterable[tuple[int, float | None, str | None]]) -> dict[str, list[float]]:
    """compound -> lap times from long runs. laps: (lap_number, lap_time, compound)."""
    timed = sorted((n, t, (c or "UNKNOWN").upper()) for n, t, c in laps if t is not None and t > 0)
    if not timed:
        return {}
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
    out: dict[str, list[float]] = defaultdict(list)
    for run in runs:
        if len(run) >= MIN_RUN_LAPS:
            for _, t, c in run:
                out[c].append(t)
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
        if session["wet"]:
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
