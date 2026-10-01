"""Leakage-safe race-pace normalization for race-strategy calibration v1.

Raw lap times are not directly comparable across circuits, eras, fuel states or
race contexts. This module converts clean race-lap observations into two useful
quantities:

1. a race-specific track reference pace (the median of competitive driver laps),
2. each driver's relative gap to that reference.

The normalized absolute pace for a target driver on a target circuit is then
constructed from target-track historical reference pace plus that driver's
historical relative gap. No future race result is required.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from statistics import median
from typing import Iterable, Sequence

from race_strategy_simulator_v1 import Distribution


@dataclass(frozen=True)
class PaceObservation:
    race_id: int
    track_id: int
    season_year: int
    regulation_era: str
    driver_key: str
    lap_number: int
    lap_time_seconds: float
    compound: str | None = None
    is_valid: bool = True


@dataclass(frozen=True)
class NormalizedPaceObservation:
    race_id: int
    track_id: int
    season_year: int
    regulation_era: str
    driver_key: str
    track_reference_seconds: float
    driver_median_seconds: float
    relative_gap_seconds: float
    normalized_lap_seconds: float


def _finite_laps(rows: Iterable[PaceObservation]) -> list[PaceObservation]:
    return [
        row
        for row in rows
        if row.is_valid
        and row.lap_number > 0
        and isfinite(float(row.lap_time_seconds))
        and 40.0 <= float(row.lap_time_seconds) <= 180.0
    ]


def _competitive_reference(values: Sequence[float], trim_fraction: float = 0.20) -> float:
    """Median of the central competitive field, excluding slow outliers."""
    if not values:
        raise ValueError("At least one lap time is required")
    ordered = sorted(values)
    if len(ordered) < 5:
        return median(ordered)
    cut = int(len(ordered) * trim_fraction)
    core = ordered[cut:max(cut + 1, len(ordered) - cut)]
    return median(core)


def normalize_race_pace(observations: Iterable[PaceObservation]) -> tuple[NormalizedPaceObservation, ...]:
    """Normalize each driver's race pace against the same-race track reference."""
    rows = _finite_laps(observations)
    races: dict[int, list[PaceObservation]] = {}
    drivers: dict[tuple[int, str], list[PaceObservation]] = {}
    for row in rows:
        races.setdefault(row.race_id, []).append(row)
        drivers.setdefault((row.race_id, row.driver_key), []).append(row)

    references = {
        race_id: _competitive_reference([float(r.lap_time_seconds) for r in race_rows])
        for race_id, race_rows in races.items()
    }

    normalized: list[NormalizedPaceObservation] = []
    for (race_id, driver_key), driver_rows in drivers.items():
        driver_median = median(float(r.lap_time_seconds) for r in driver_rows)
        ref = references[race_id]
        anchor = driver_rows[0]
        gap = driver_median - ref
        normalized.append(
            NormalizedPaceObservation(
                race_id=race_id,
                track_id=anchor.track_id,
                season_year=anchor.season_year,
                regulation_era=anchor.regulation_era,
                driver_key=driver_key,
                track_reference_seconds=ref,
                driver_median_seconds=driver_median,
                relative_gap_seconds=gap,
                normalized_lap_seconds=ref + gap,
            )
        )
    return tuple(normalized)


def build_target_pace_distribution(
    observations: Iterable[NormalizedPaceObservation],
    *,
    target_track_id: int,
    target_era: str,
    target_driver_key: str,
    min_races: int = 3,
) -> tuple[Distribution, tuple[str, ...]]:
    """Build a target-driver absolute pace distribution without future leakage."""
    rows = [
        row for row in observations
        if row.track_id == target_track_id
        and row.regulation_era == target_era
        and row.driver_key == target_driver_key
        and isfinite(float(row.normalized_lap_seconds))
    ]
    warnings: list[str] = []
    if len(rows) < min_races:
        warnings.append(
            f"Low target-pace history for driver={target_driver_key}, track={target_track_id}, era={target_era}: n={len(rows)}"
        )
    if not rows:
        raise ValueError("No target pace observations matched track, era and driver")

    values = [float(row.normalized_lap_seconds) for row in rows]
    centre = median(values)
    deviations = sorted(abs(value - centre) for value in values)
    mad = median(deviations)
    sigma = max(0.05, 1.4826 * mad)
    return Distribution(mean=centre, std=sigma, lower=40.0, upper=180.0), tuple(warnings)
