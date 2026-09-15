"""Read-only database adapter for race-strategy calibration v1.

This module maps the existing production schema into the plain observation
objects consumed by ``race_strategy_calibration_v1``. It deliberately does
not write to the database and does not invent measurements that are absent.

Current database-backed observations:
- dry-race tyre degradation from race stints + lap times
- race-level SC/VSC/red-flag exposure

Explicitly unavailable from the current schema/query surface:
- true pit service duration / pit-lane loss
- a directly comparable absolute car-pace distribution
- exact lap-by-lap SC/VSC timing

Those gaps are returned as warnings rather than replacements with synthetic data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

from race_strategy_calibration_v1 import (
    EventCalibrationObservation,
    PaceCalibrationObservation,
    PitCalibrationObservation,
    TyreCalibrationObservation,
)

VALID_DRY_COMPOUNDS = ("SOFT", "MEDIUM", "HARD")


@dataclass(frozen=True)
class DatabaseCalibrationDataset:
    tyre_observations: tuple[TyreCalibrationObservation, ...]
    event_observations: tuple[EventCalibrationObservation, ...]
    pit_observations: tuple[PitCalibrationObservation, ...]
    pace_observations: tuple[PaceCalibrationObservation, ...]
    warnings: tuple[str, ...] = ()


def _rows(db: Any, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    result = db.execute(text(sql), params or {})
    if hasattr(result, "mappings"):
        return [dict(row) for row in result.mappings().all()]
    return [dict(row) for row in result]


def load_regulation_eras(db: Any, *, start_year: int = 2017, end_year: int = 2026) -> tuple[str, ...]:
    """Return distinct stored regulation-era labels for a year range.

    Sorting is performed in Python as a deterministic final step so tests and
    non-standard DB adapters do not depend on faithfully reproducing SQL ORDER BY.
    """
    if start_year > end_year:
        raise ValueError("start_year cannot be greater than end_year")
    rows = _rows(
        db,
        """
        SELECT DISTINCT r.regulation_era
        FROM races r
        WHERE r.season_year BETWEEN :start_year AND :end_year
          AND r.regulation_era IS NOT NULL
        ORDER BY r.regulation_era
        """,
        {"start_year": start_year, "end_year": end_year},
    )
    labels = {
        str(row["regulation_era"])
        for row in rows
        if row.get("regulation_era") is not None
    }
    return tuple(sorted(labels))


def load_event_observations(
    db: Any,
    *,
    era: str | None = None,
    start_year: int = 2017,
    end_year: int = 2026,
) -> tuple[tuple[EventCalibrationObservation, ...], tuple[str, ...]]:
    """Load race-level event counts and conservative wet-lap exposure."""
    where = [
        "r.race_date IS NOT NULL",
        "r.season_year BETWEEN :start_year AND :end_year",
        "t.total_race_laps IS NOT NULL",
    ]
    params: dict[str, Any] = {"start_year": start_year, "end_year": end_year}
    if era is not None:
        where.append("r.regulation_era = :era")
        params["era"] = era

    rows = _rows(
        db,
        f"""
        SELECT
            r.id AS race_id,
            r.season_year,
            r.round_number,
            t.total_race_laps,
            COALESCE(r.safety_car_periods, 0) AS safety_car_periods,
            COALESCE(r.vsc_periods, 0) AS vsc_periods,
            COALESCE(r.red_flags, 0) AS red_flags,
            COALESCE(sw.rainfall, FALSE) AS rainfall,
            sw.rain_onset_lap
        FROM races r
        JOIN tracks t ON t.id = r.track_id
        JOIN sessions s ON s.race_id = r.id AND s.session_type = 'R'
        LEFT JOIN session_weather sw ON sw.session_id = s.id
        WHERE {' AND '.join(where)}
        ORDER BY r.race_date
        """,
        params,
    )

    observations: list[EventCalibrationObservation] = []
    warnings: list[str] = []
    for row in rows:
        total_laps = int(row["total_race_laps"])
        wet_laps = 0
        if bool(row["rainfall"]):
            onset = row.get("rain_onset_lap")
            if onset is None:
                warnings.append(
                    f"Race {row['race_id']} is marked wet but has no rain_onset_lap; wet exposure omitted"
                )
            else:
                wet_laps = max(0, min(total_laps, total_laps - int(onset) + 1))

        observations.append(
            EventCalibrationObservation(
                total_laps=total_laps,
                safety_car_count=int(row["safety_car_periods"] or 0),
                vsc_count=int(row["vsc_periods"] or 0),
                red_flag_count=int(row["red_flags"] or 0),
                wet_laps=wet_laps,
            )
        )

    if not observations:
        warnings.append("No race-event observations matched the requested filter")

    return tuple(observations), tuple(sorted(set(warnings)))


def load_tyre_observations(
    db: Any,
    *,
    era: str | None = None,
    start_year: int = 2017,
    end_year: int = 2026,
    min_stint_laps: int = 5,
) -> tuple[tuple[TyreCalibrationObservation, ...], tuple[str, ...]]:
    """Derive within-stint tyre-age deltas from dry race laps."""
    where = [
        "r.race_date IS NOT NULL",
        "r.season_year BETWEEN :start_year AND :end_year",
        "sw.rainfall = FALSE",
        "rs.start_lap IS NOT NULL",
        "rs.end_lap IS NOT NULL",
        "rs.compound IN ('SOFT', 'MEDIUM', 'HARD')",
        "l.lap_number BETWEEN rs.start_lap AND rs.end_lap",
        "l.lap_time IS NOT NULL",
        "l.is_valid = TRUE",
    ]
    params: dict[str, Any] = {"start_year": start_year, "end_year": end_year}
    if era is not None:
        where.append("r.regulation_era = :era")
        params["era"] = era

    rows = _rows(
        db,
        f"""
        SELECT
            r.id AS race_id,
            rs.race_entry_id,
            rs.stint_number,
            rs.compound,
            rs.start_lap,
            rs.end_lap,
            l.lap_number,
            l.lap_time
        FROM race_stints rs
        JOIN races r ON r.id = rs.race_id
        JOIN sessions s ON s.race_id = r.id AND s.session_type = 'R'
        JOIN session_weather sw ON sw.session_id = s.id
        JOIN laps l
          ON l.session_id = s.id
         AND l.race_entry_id = rs.race_entry_id
        WHERE {' AND '.join(where)}
        ORDER BY r.id, rs.race_entry_id, rs.stint_number, l.lap_number
        """,
        params,
    )

    grouped: dict[tuple[int, int, int], list[dict[str, Any]]] = {}
    for row in rows:
        key = (int(row["race_id"]), int(row["race_entry_id"]), int(row["stint_number"]))
        grouped.setdefault(key, []).append(row)

    observations: list[TyreCalibrationObservation] = []
    warnings: list[str] = []
    usable_stints = 0

    for stint_key_tuple, rows_for_stint in grouped.items():
        if len(rows_for_stint) < min_stint_laps:
            continue
        compound = str(rows_for_stint[0]["compound"]).upper()
        if compound not in VALID_DRY_COMPOUNDS:
            continue
        usable_stints += 1

        ordered = sorted(rows_for_stint, key=lambda r: int(r["lap_number"]))
        baseline_values = [float(r["lap_time"]) for r in ordered[:2]]
        baseline = sum(baseline_values) / len(baseline_values)
        start_lap = int(ordered[0]["lap_number"])
        race_id, race_entry_id, stint_number = stint_key_tuple
        stint_key = f"{race_id}:{race_entry_id}:{stint_number}"

        for row in ordered:
            lap_number = int(row["lap_number"])
            age = lap_number - start_lap
            if age < 1:
                continue
            delta = float(row["lap_time"]) - baseline
            observations.append(
                TyreCalibrationObservation(
                    compound=compound,
                    tyre_age_laps=age,
                    lap_time_delta_seconds=delta,
                    wet_state="dry",
                    stint_key=stint_key,
                )
            )

    if usable_stints == 0:
        warnings.append("No usable dry race tyre stints matched the requested filter")
    elif len(observations) < 20:
        warnings.append(f"Only {len(observations)} tyre observations were derived from {usable_stints} stints")

    return tuple(observations), tuple(sorted(set(warnings)))


def load_unavailable_inputs() -> tuple[tuple[PitCalibrationObservation, ...], tuple[PaceCalibrationObservation, ...], tuple[str, ...]]:
    """Return explicit gaps instead of fabricating pit or pace observations."""
    return (
        (),
        (),
        (
            "Pit service-time and pit-lane-loss measurements are not exposed by the current DB schema; FastF1 pit-stop ingestion is required.",
            "A cross-track absolute pace distribution is not directly identifiable from the current stored fields; pace normalization/calibration needs a dedicated upstream feature definition.",
        ),
    )


def load_calibration_dataset(
    db: Any,
    *,
    era: str | None = None,
    start_year: int = 2017,
    end_year: int = 2026,
    min_stint_laps: int = 5,
) -> DatabaseCalibrationDataset:
    """Load every currently supportable calibration observation from the DB."""
    tyre, tyre_warnings = load_tyre_observations(
        db, era=era, start_year=start_year, end_year=end_year, min_stint_laps=min_stint_laps
    )
    events, event_warnings = load_event_observations(
        db, era=era, start_year=start_year, end_year=end_year
    )
    pit, pace, unavailable_warnings = load_unavailable_inputs()

    return DatabaseCalibrationDataset(
        tyre_observations=tyre,
        event_observations=events,
        pit_observations=pit,
        pace_observations=pace,
        warnings=tuple(sorted(set(tyre_warnings + event_warnings + unavailable_warnings))),
    )
