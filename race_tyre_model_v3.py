"""Tyre model v3: field-relative compound pace and wear, plus a strategy optimiser.

Lap-time model for a clean green-flag lap of car k on compound c at tyre age a:

    lap_time - field_median(lap) = car_k + offset_c + slope_c * a + noise

- field_median(lap) is the median clean lap time of all cars on that lap number,
  so fuel burn and track evolution (common to every car) cancel out.
- car_k is a per-car-per-race level (car and driver pace), estimated jointly.
- offset_c is the compound's pace relative to MEDIUM (MEDIUM = 0); slope_c is
  the wear in seconds per lap of tyre age.

The fit alternates between car levels and per-compound linear fits. Evidence
comes from the most specific pool with enough laps: same track + era, else era.

The optimiser picks the legal dry strategy (1 or 2 stops, >= 2 compounds) with
the lowest expected race time under the model plus the green pit loss. It is
RESEARCH until race_tyre_model_backtest_v3 passes its gate walk-forward.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from itertools import product
from statistics import median
from typing import Any, Iterable, Sequence

import numpy as np
from sqlalchemy import text

from race_strategy_precedent_v1 import build_strategy
from race_strategy_simulator_v1 import DRY_COMPOUNDS, Strategy

REFERENCE_COMPOUND = "MEDIUM"
MIN_FIELD_CARS = 5          # cars needed on a lap before its field median is trusted
MAX_ABS_RELATIVE = 4.0      # s; laps further from the field median are traffic, mistakes or damage
MIN_CAR_LAPS = 5            # clean laps a car needs in a race to be used
MIN_COMPOUND_LAPS = 60      # laps a compound needs before it enters the model
MIN_TRACK_LAPS = 1500       # laps of track history needed to use the track pool
MIN_STINT_LAPS = 5          # optimiser: shortest stint considered
FIT_ITERATIONS = 15


@dataclass(frozen=True)
class TyreLapObs:
    race_id: int
    race_date: date
    track_id: int
    regulation_era: str
    car: int                 # race_entry_id
    compound: str
    tyre_age: float
    relative: float          # lap time minus the field median on that lap


@dataclass(frozen=True)
class TyreModel:
    params: dict[str, tuple[float, float]]    # compound -> (offset vs MEDIUM, slope s/lap)
    n_laps: int
    n_races: int
    source: str

    def shape(self, compound: str, tyre_age: float) -> float:
        offset, slope = self.params[compound]
        return offset + slope * tyre_age


# --- fitting -------------------------------------------------------------------

def fit_tyre_model(obs: Sequence[TyreLapObs], *, source: str = "") -> TyreModel | None:
    counts = defaultdict(int)
    for o in obs:
        counts[o.compound] += 1
    compounds = sorted(c for c, n in counts.items() if n >= MIN_COMPOUND_LAPS)
    if REFERENCE_COMPOUND not in compounds or len(compounds) < 2:
        return None
    rows = [o for o in obs if o.compound in compounds]
    cars = sorted({(o.race_id, o.car) for o in rows})
    car_index = {c: i for i, c in enumerate(cars)}
    ci = np.array([car_index[(o.race_id, o.car)] for o in rows])
    comp = np.array([o.compound for o in rows])
    age = np.array([o.tyre_age for o in rows], dtype=float)
    y = np.array([o.relative for o in rows], dtype=float)

    level = np.bincount(ci, weights=y, minlength=len(cars)) / np.bincount(ci, minlength=len(cars))
    params: dict[str, tuple[float, float]] = {}
    for _ in range(FIT_ITERATIONS):
        target = y - level[ci]
        for c in compounds:
            m = comp == c
            if np.ptp(age[m]) == 0:
                params[c] = (float(target[m].mean()), 0.0)
            else:
                slope, offset = np.polyfit(age[m], target[m], 1)
                params[c] = (float(offset), float(slope))
        # anchor MEDIUM's offset at 0; the shift moves into the car levels
        shift = params[REFERENCE_COMPOUND][0]
        params = {c: (o - shift, s) for c, (o, s) in params.items()}
        shape = np.zeros_like(y)
        for c, (o, s) in params.items():
            m = comp == c
            shape[m] = o + s * age[m]
        level = np.bincount(ci, weights=y - shape, minlength=len(cars)) / np.bincount(ci, minlength=len(cars))
    return TyreModel({c: (round(o, 4), round(s, 5)) for c, (o, s) in params.items()},
                     len(rows), len({o.race_id for o in rows}), source)


def fit_for_race(history: Sequence[TyreLapObs], *, track_id: int, regulation_era: str,
                 before_date: date) -> TyreModel | None:
    """Walk-forward fit: track + era if it has enough laps, else the era."""
    pool = [o for o in history if o.regulation_era == regulation_era and o.race_date < before_date]
    track = [o for o in pool if o.track_id == track_id]
    if len(track) >= MIN_TRACK_LAPS:
        model = fit_tyre_model(track, source=f"track_era(laps={len(track)})")
        if model is not None and set(DRY_COMPOUNDS) <= set(model.params):
            return model
    return fit_tyre_model(pool, source=f"era(laps={len(pool)})")


def residuals(model_shape, laps: Sequence[TyreLapObs]) -> list[float]:
    """Residuals after giving each car its own level (fair to every candidate shape)."""
    by_car = defaultdict(list)
    for o in laps:
        by_car[(o.race_id, o.car)].append(o.relative - model_shape(o))
    out = []
    for values in by_car.values():
        centre = sum(values) / len(values)
        out.extend(v - centre for v in values)
    return out


# --- optimiser -----------------------------------------------------------------

def _stint_cost(model: TyreModel, compound: str, laps: int) -> float:
    offset, slope = model.params[compound]
    slope = max(slope, 0.0)          # a negative fitted wear rate would reward endless stints
    return laps * offset + slope * laps * (laps + 1) / 2


def optimise(model: TyreModel, *, total_laps: int, pit_loss: float, max_stops: int = 2,
             top_k: int = 4) -> list[tuple[Strategy, float]]:
    """Legal dry strategies ranked by expected time (seconds relative to the best)."""
    compounds = [c for c in DRY_COMPOUNDS if c in model.params]
    best: dict[tuple[str, ...], tuple[float, tuple[int, ...]]] = {}
    for n_stops in range(1, max_stops + 1):
        for sequence in product(compounds, repeat=n_stops + 1):
            if len(set(sequence)) < 2:
                continue
            ranges = [range(MIN_STINT_LAPS, total_laps - MIN_STINT_LAPS * (n_stops - i) + 1) for i in range(n_stops)]
            for stops in product(*ranges):
                bounds = (0, *stops, total_laps)
                if any(b - a < MIN_STINT_LAPS for a, b in zip(bounds, bounds[1:])):
                    continue
                cost = n_stops * pit_loss + sum(
                    _stint_cost(model, c, b - a) for c, a, b in zip(sequence, bounds, bounds[1:]))
                if sequence not in best or cost < best[sequence][0]:
                    best[sequence] = (cost, stops)
    ranked = sorted(best.items(), key=lambda kv: kv[1][0])[:top_k]
    if not ranked:
        return []
    fastest = ranked[0][1][0]
    out = []
    for sequence, (cost, stops) in ranked:
        strategy = build_strategy(sequence, list(stops), total_laps)
        if strategy is not None:
            out.append((Strategy(strategy.name, strategy.stints, source="tyre_model_v3"), round(cost - fastest, 2)))
    return out


# --- database ------------------------------------------------------------------

def clean_relative_laps(race: dict, rows: Iterable[tuple]) -> list[TyreLapObs]:
    """rows: (entry, lap_number, lap_time, compound, tyre_life, status, pit_in, pit_out)."""
    clean = []
    for entry, lap, lap_time, compound, life, status, pit_in, pit_out in rows:
        compound = (compound or "").upper()
        if (lap <= 1 or lap_time is None or life is None or compound not in DRY_COMPOUNDS
                or status != "1" or pit_in or pit_out):
            continue
        clean.append((int(entry), int(lap), float(lap_time), compound, float(life)))
    by_lap = defaultdict(list)
    for entry, lap, t, _, _ in clean:
        by_lap[lap].append(t)
    field = {lap: median(ts) for lap, ts in by_lap.items() if len(ts) >= MIN_FIELD_CARS}
    obs = []
    for entry, lap, t, compound, life in clean:
        if lap in field and abs(t - field[lap]) <= MAX_ABS_RELATIVE:
            obs.append(TyreLapObs(race["id"], race["race_date"], race["track_id"], race["regulation_era"],
                                  entry, compound, life, t - field[lap]))
    per_car = defaultdict(int)
    for o in obs:
        per_car[o.car] += 1
    return [o for o in obs if per_car[o.car] >= MIN_CAR_LAPS]


def load_tyre_observations(db: Any, *, start_year: int = 2018, end_year: int = 2026) -> list[TyreLapObs]:
    """Clean green-flag laps from dry races (session_weather.rainfall is not true)."""
    races = db.execute(text("""
        SELECT r.id, r.race_date, r.track_id, r.regulation_era, s.id AS session_id
        FROM races r
        JOIN sessions s ON s.race_id = r.id AND s.session_type = 'R'
        LEFT JOIN session_weather sw ON sw.session_id = s.id
        WHERE r.season_year BETWEEN :a AND :b AND r.regulation_era IS NOT NULL
          AND COALESCE(sw.rainfall, FALSE) = FALSE
        ORDER BY r.race_date
    """), {"a": start_year, "b": end_year}).mappings().all()
    out: list[TyreLapObs] = []
    for race in races:
        rows = db.execute(text("""
            SELECT race_entry_id, lap_number, lap_time, tire_compound, tyre_life_laps, track_status_code,
                   pit_in_time_seconds IS NOT NULL, pit_out_time_seconds IS NOT NULL
            FROM laps WHERE session_id = :s
        """), {"s": race["session_id"]}).all()
        out.extend(clean_relative_laps(dict(race), rows))
    return out
