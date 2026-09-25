"""Race pace from qualifying pace, calibrated walk-forward per regulation era.

The first v2 backtest predicted pole sitters to finish 5th-9th because race pace
came from track/team/driver history and ignored qualifying -- the strongest
pre-race signal of car speed. This model uses only information known before the
race:

    quali_gap = driver's best qualifying time / pole time - 1
    race_gap  = driver's clean race pace / fastest clean race pace - 1   (history only)
    race_gap ~ intercept + slope * quali_gap                              (fit per era)

Clean race pace = median of laps 2+ after dropping laps slower than 105% of that
driver's median (removes pit, Safety Car and traffic-blocked laps without needing
lap enrichment). Drivers need >= MIN_CLEAN_LAPS clean laps.

Qualifying gaps above MAX_QUALI_GAP (crash, no representative time) are treated
as missing and placed at the back of the valid field.

    python race_pace_from_quali_v1.py --start-year 2019 --end-year 2025
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from statistics import median
from typing import Any, Iterable

import numpy as np
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

MAX_QUALI_GAP = 0.07
CLEAN_LAP_FACTOR = 1.05
MIN_CLEAN_LAPS = 10


@dataclass(frozen=True)
class PaceObservation:
    race_id: int
    season_year: int
    regulation_era: str
    driver_id: int
    quali_gap: float
    race_gap: float
    pole_time: float
    race_reference: float


@dataclass(frozen=True)
class QualiPaceModel:
    intercept: float
    slope: float
    residual_std: float
    race_to_pole_ratio: float  # fastest clean race lap / pole time
    n_observations: int
    n_races: int

    def race_gap(self, quali_gap: float) -> float:
        return self.intercept + self.slope * quali_gap


# --- pure helpers --------------------------------------------------------------

def quali_gaps(best_times: dict[int, float | None]) -> dict[int, float]:
    """Gap to pole for every driver; missing/unrepresentative times go to the back."""
    valid = {d: t for d, t in best_times.items() if t is not None and t > 0}
    if not valid:
        return {}
    pole = min(valid.values())
    gaps = {d: t / pole - 1 for d, t in valid.items()}
    kept = {d: g for d, g in gaps.items() if g <= MAX_QUALI_GAP}
    back = (max(kept.values()) if kept else 0.0) + 0.005
    return {d: kept.get(d, back) for d in best_times}


def clean_race_pace(lap_times: Iterable[tuple[int, float | None]]) -> float | None:
    """Median of clean racing laps (lap 2+, within 105% of the driver's median)."""
    laps = [t for n, t in lap_times if n >= 2 and t is not None]
    if len(laps) < MIN_CLEAN_LAPS:
        return None
    centre = median(laps)
    clean = [t for t in laps if t <= CLEAN_LAP_FACTOR * centre]
    return median(clean) if len(clean) >= MIN_CLEAN_LAPS else None


def fit_model(observations: Iterable[PaceObservation]) -> QualiPaceModel:
    rows = list(observations)
    if len(rows) < 30:
        raise ValueError(f"too few pace observations to fit: {len(rows)}")
    x = np.array([r.quali_gap for r in rows])
    y = np.array([r.race_gap for r in rows])
    design = np.column_stack([np.ones_like(x), x])
    (intercept, slope), *_ = np.linalg.lstsq(design, y, rcond=None)
    residuals = y - (intercept + slope * x)
    ratio = median(r.race_reference / r.pole_time for r in rows)
    return QualiPaceModel(float(intercept), float(slope), float(np.std(residuals, ddof=2)),
                          float(ratio), len(rows), len({r.race_id for r in rows}))


def spearman(a: list[float], b: list[float]) -> float:
    ra = np.argsort(np.argsort(a))
    rb = np.argsort(np.argsort(b))
    return float(np.corrcoef(ra, rb)[0, 1]) if len(a) > 2 else float("nan")


# --- database ------------------------------------------------------------------

def best_quali_times(db: Any, race_id: int) -> dict[int, float | None]:
    rows = db.execute(text("""
        SELECT re.driver_id, LEAST(q.q1_time, q.q2_time, q.q3_time) AS best
        FROM qualifying_results q
        JOIN sessions s ON s.id = q.session_id AND s.session_type = 'Q'
        JOIN race_entries re ON re.id = q.race_entry_id
        WHERE s.race_id = :r
    """), {"r": race_id}).all()
    return {int(d): (float(b) if b is not None else None) for d, b in rows}


def load_observations(db: Any, *, start_year: int, end_year: int) -> list[PaceObservation]:
    """Per driver-race quali gap and clean race-pace gap (dry races only)."""
    races = db.execute(text("""
        SELECT r.id, r.season_year, r.regulation_era, s.id AS session_id
        FROM races r
        JOIN sessions s ON s.race_id = r.id AND s.session_type = 'R'
        JOIN session_weather sw ON sw.session_id = s.id AND sw.rainfall = FALSE
        WHERE r.season_year BETWEEN :a AND :b AND r.regulation_era IS NOT NULL
        ORDER BY r.race_date
    """), {"a": start_year, "b": end_year}).mappings().all()

    observations: list[PaceObservation] = []
    for race in races:
        times = best_quali_times(db, race["id"])
        if not times:
            continue
        pole = min((t for t in times.values() if t), default=None)
        gaps = quali_gaps(times)
        laps: dict[int, list[tuple[int, float | None]]] = {}
        for driver_id, lap_number, lap_time in db.execute(text("""
            SELECT re.driver_id, l.lap_number, l.lap_time
            FROM laps l JOIN race_entries re ON re.id = l.race_entry_id
            WHERE l.session_id = :s
        """), {"s": race["session_id"]}):
            laps.setdefault(int(driver_id), []).append((int(lap_number), float(lap_time) if lap_time is not None else None))
        pace = {d: p for d, l in laps.items() if (p := clean_race_pace(l)) is not None}
        if len(pace) < 10 or pole is None:
            continue
        reference = min(pace.values())
        for driver_id, p in pace.items():
            if driver_id in gaps:
                observations.append(PaceObservation(
                    race["id"], race["season_year"], race["regulation_era"], driver_id,
                    gaps[driver_id], p / reference - 1, pole, reference,
                ))
    return observations


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start-year", type=int, default=2019)
    parser.add_argument("--end-year", type=int, default=2025)
    args = parser.parse_args()

    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as db:
        observations = load_observations(db, start_year=2018, end_year=args.end_year)

    print("Walk-forward check: fit on earlier seasons of the same era, predict each season.")
    print("year era                         n_fit  slope  intercept  resid_std  | pace-vs-race rho  grid-vs-race rho")
    for year in range(args.start_year, args.end_year + 1):
        targets = [o for o in observations if o.season_year == year]
        if not targets:
            continue
        era = targets[0].regulation_era
        history = [o for o in observations if o.regulation_era == era and o.season_year < year]
        try:
            model = fit_model(history)
        except ValueError as exc:
            print(f"{year} {era:28} skipped: {exc}")
            continue
        # per race: does predicted race pace order match the realised race-pace order better than quali order?
        pace_rho, quali_rho = [], []
        for race_id in sorted({o.race_id for o in targets}):
            rows = [o for o in targets if o.race_id == race_id]
            predicted = [model.race_gap(o.quali_gap) for o in rows]
            actual = [o.race_gap for o in rows]
            pace_rho.append(spearman(predicted, actual))
            quali_rho.append(spearman([o.quali_gap for o in rows], actual))
        error = np.mean([abs(model.race_gap(o.quali_gap) - o.race_gap) for o in targets]) * 100
        print(f"{year} {era:28} {model.n_observations:5}  {model.slope:5.2f}  {model.intercept * 100:8.3f}%  "
              f"{model.residual_std * 100:8.3f}%  | {np.nanmean(pace_rho):6.3f} (MAE {error:.3f}%)  {np.nanmean(quali_rho):6.3f}")
    print("A linear map cannot reorder drivers, so both rho columns match by construction;")
    print("the useful outputs are the slope (how much of the quali gap survives into the race)")
    print("and resid_std (race-day spread the simulator must sample). Read-only.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
