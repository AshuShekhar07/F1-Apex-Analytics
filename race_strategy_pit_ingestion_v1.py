"""FastF1 pit-stop reconstruction helpers for race-strategy calibration v1.

FastF1 records pit entry and pit exit timestamps on lap rows. A complete pit
visit is reconstructed by pairing an in-lap ``PitInTime`` with the next
``PitOutTime`` for the same driver. The resulting measurement is *total pit
lane time*; it is intentionally not mislabeled as crew service time.

This module is pure Python apart from the pandas-shaped input protocol, so the
same reconstruction can be tested without downloading FastF1 sessions.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from statistics import median
from typing import Any, Iterable


@dataclass(frozen=True)
class PitIngestionConfig:
    physical_min_seconds: float = 15.0
    physical_max_seconds: float = 300.0
    iqr_multiplier: float = 1.5


@dataclass(frozen=True)
class ReconstructedPitStop:
    driver: str
    pit_lap: int
    pit_in_time_seconds: float
    pit_out_time_seconds: float
    total_pit_lane_seconds: float


def _seconds(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if hasattr(value, "total_seconds"):
            value = value.total_seconds()
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if isfinite(value) else None


def reconstruct_pit_stops(rows: Iterable[Any]) -> list[ReconstructedPitStop]:
    """Pair pit-entry and subsequent pit-exit timestamps for each driver.

    ``rows`` may be pandas ``Series``/dict-like objects. Rows are expected to
    expose ``Driver``, ``LapNumber``, ``PitInTime`` and ``PitOutTime``. A stop
    requires a pit-in timestamp and a later pit-out timestamp for that driver.
    """
    ordered = []
    for row in rows:
        driver = str(row.get("Driver", "")).strip()
        lap = row.get("LapNumber")
        if not driver or lap is None:
            continue
        try:
            lap_number = int(lap)
        except (TypeError, ValueError):
            continue
        ordered.append((driver, lap_number, row))

    ordered.sort(key=lambda item: (item[0], item[1]))
    pit_ins: dict[str, tuple[int, float]] = {}
    stops: list[ReconstructedPitStop] = []

    for driver, lap_number, row in ordered:
        pit_in = _seconds(row.get("PitInTime"))
        if pit_in is not None:
            pit_ins[driver] = (lap_number, pit_in)

        pit_out = _seconds(row.get("PitOutTime"))
        pending = pit_ins.get(driver)
        if pit_out is None or pending is None:
            continue

        pit_lap, pit_in_time = pending
        duration = pit_out - pit_in_time
        if duration > 0:
            stops.append(
                ReconstructedPitStop(
                    driver=driver,
                    pit_lap=pit_lap,
                    pit_in_time_seconds=pit_in_time,
                    pit_out_time_seconds=pit_out,
                    total_pit_lane_seconds=duration,
                )
            )
        pit_ins.pop(driver, None)

    return stops


def filter_pit_stop_outliers(
    stops: Iterable[ReconstructedPitStop],
    *,
    config: PitIngestionConfig | None = None,
) -> tuple[list[ReconstructedPitStop], int]:
    """Remove physically impossible and race-level IQR outlier pit visits.

    The physical bounds prevent tiny/huge values from corrupting IQR when a
    race has only a few stops. IQR filtering is applied around the race sample
    only when it can actually identify a tighter interval.
    """
    config = config or PitIngestionConfig()
    rows = [
        stop
        for stop in stops
        if config.physical_min_seconds <= stop.total_pit_lane_seconds <= config.physical_max_seconds
    ]
    removed = len(list(stops)) - len(rows) if not isinstance(stops, list) else len(stops) - len(rows)
    if len(rows) < 4:
        return rows, removed

    values = sorted(stop.total_pit_lane_seconds for stop in rows)
    midpoint = len(values) // 2
    lower_half = values[:midpoint]
    upper_half = values[midpoint + (len(values) % 2):]
    q1 = median(lower_half)
    q3 = median(upper_half)
    iqr = q3 - q1
    if iqr <= 0:
        return rows, removed

    lower = max(config.physical_min_seconds, q1 - config.iqr_multiplier * iqr)
    upper = min(config.physical_max_seconds, q3 + config.iqr_multiplier * iqr)
    filtered = [stop for stop in rows if lower <= stop.total_pit_lane_seconds <= upper]
    return filtered, removed + (len(rows) - len(filtered))


def extract_pit_stops_from_fastf1_session(session: Any) -> list[ReconstructedPitStop]:
    """Extract reconstructed pit stops from a loaded FastF1 race session."""
    laps = getattr(session, "laps", None)
    if laps is None:
        raise ValueError("FastF1 session has no loaded laps")
    return reconstruct_pit_stops(laps.to_dict("records"))


def fastf1_session_loader(year: int, round_number: int, cache_dir: str | None = None) -> Any:
    """Load a FastF1 race session with only the timing data needed here."""
    import fastf1

    if cache_dir:
        fastf1.Cache.enable_cache(cache_dir)
    session = fastf1.get_session(year, round_number, "R")
    session.load(telemetry=False, weather=False, messages=False)
    return session
