"""Read-only DB adapter for early-stint compound pace calibration v1."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from race_strategy_compound_calibration_v1 import CompoundPaceObservation, DRY_COMPOUNDS


def load_compound_pace_observations(
    db: Any,
    *,
    start_year: int = 2018,
    end_year: int = 2026,
    era: str | None = None,
    min_age: int = 1,
    max_age: int = 3,
) -> tuple[tuple[CompoundPaceObservation, ...], tuple[str, ...]]:
    """Load clean dry early-stint laps with explicit stint age."""
    if min_age < 0 or max_age < min_age:
        raise ValueError("Invalid tyre-age bounds")

    where = [
        "r.race_date IS NOT NULL",
        "r.season_year BETWEEN :start_year AND :end_year",
        "s.session_type = 'R'",
        "sw.rainfall = FALSE",
        "rs.start_lap IS NOT NULL",
        "rs.end_lap IS NOT NULL",
        "rs.compound IN ('SOFT', 'MEDIUM', 'HARD')",
        "l.lap_number BETWEEN rs.start_lap AND rs.end_lap",
        "l.lap_time IS NOT NULL",
        "l.is_valid = TRUE",
        "l.lap_number - rs.start_lap BETWEEN :min_age AND :max_age",
        "re.driver_id IS NOT NULL",
        "r.regulation_era IS NOT NULL",
    ]
    params: dict[str, Any] = {
        "start_year": start_year,
        "end_year": end_year,
        "min_age": min_age,
        "max_age": max_age,
    }
    if era is not None:
        where.append("r.regulation_era = :era")
        params["era"] = era

    result = db.execute(
        text(f"""
            SELECT
                r.id AS race_id,
                r.season_year,
                r.regulation_era,
                re.driver_id,
                l.lap_number,
                (l.lap_number - rs.start_lap) AS tyre_age_laps,
                rs.compound,
                l.lap_time
            FROM race_stints rs
            JOIN races r ON r.id = rs.race_id
            JOIN sessions s ON s.race_id = r.id AND s.session_type = 'R'
            JOIN race_entries re ON re.id = rs.race_entry_id
            JOIN laps l
              ON l.session_id = s.id
             AND l.race_entry_id = rs.race_entry_id
            JOIN session_weather sw ON sw.session_id = s.id
            WHERE {' AND '.join(where)}
            ORDER BY r.race_date, r.id, re.driver_id, l.lap_number
        """),
        params,
    )
    rows = result.mappings().all() if hasattr(result, "mappings") else result

    observations = tuple(
        CompoundPaceObservation(
            race_id=int(row["race_id"]),
            season_year=int(row["season_year"]),
            regulation_era=str(row["regulation_era"]),
            driver_key=str(row["driver_id"]),
            lap_number=int(row["lap_number"]),
            tyre_age_laps=int(row["tyre_age_laps"]),
            compound=str(row["compound"]).upper(),
            lap_time_seconds=float(row["lap_time"]),
            is_valid=True,
        )
        for row in rows
        if str(row["compound"]).upper() in DRY_COMPOUNDS
    )
    warnings: list[str] = []
    if not observations:
        warnings.append("No clean dry early-stint compound observations matched the requested filter")
    elif len(observations) < 1000:
        warnings.append(f"Low compound pace sample: n={len(observations)}")
    return observations, tuple(warnings)
