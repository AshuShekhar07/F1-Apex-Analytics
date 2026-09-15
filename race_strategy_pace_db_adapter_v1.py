"""Database adapter for leakage-safe race-pace normalization v1."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from race_strategy_pace_normalization_v1 import PaceObservation


def _rows(db: Any, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    result = db.execute(text(sql), params)
    if hasattr(result, "mappings"):
        return [dict(row) for row in result.mappings().all()]
    return [dict(row) for row in result]


def load_race_pace_observations(
    db: Any,
    *,
    era: str | None = None,
    start_year: int = 2018,
    end_year: int = 2026,
    min_lap_seconds: float = 40.0,
    max_lap_seconds: float = 180.0,
) -> tuple[tuple[PaceObservation, ...], tuple[str, ...]]:
    """Load clean dry race laps with persistent driver, track and era identity.

    The target model is allowed to use these historical laps only. No finishing
    position, post-race outcome, or future-race observation is used here.
    """
    where = [
        "r.race_date IS NOT NULL",
        "r.season_year BETWEEN :start_year AND :end_year",
        "s.session_type = 'R'",
        "l.lap_time IS NOT NULL",
        "l.is_valid = TRUE",
        "l.lap_time BETWEEN :min_lap AND :max_lap",
        "l.tire_compound IN ('SOFT', 'MEDIUM', 'HARD')",
    ]
    params: dict[str, Any] = {
        "start_year": start_year,
        "end_year": end_year,
        "min_lap": min_lap_seconds,
        "max_lap": max_lap_seconds,
    }
    if era is not None:
        where.append("r.regulation_era = :era")
        params["era"] = era

    rows = _rows(
        db,
        f"""
        SELECT
            r.id AS race_id,
            r.track_id,
            r.season_year,
            r.regulation_era,
            re.driver_id,
            l.lap_number,
            l.lap_time,
            COALESCE(sw.rainfall, FALSE) AS rainfall
        FROM laps l
        JOIN sessions s ON s.id = l.session_id
        JOIN races r ON r.id = s.race_id
        JOIN race_entries re ON re.id = l.race_entry_id
        LEFT JOIN session_weather sw ON sw.session_id = s.id
        WHERE {' AND '.join(where)}
          AND COALESCE(sw.rainfall, FALSE) = FALSE
        ORDER BY r.id, re.driver_id, l.lap_number
        """,
        params,
    )

    observations = tuple(
        PaceObservation(
            race_id=int(row["race_id"]),
            track_id=int(row["track_id"]),
            season_year=int(row["season_year"]),
            regulation_era=str(row["regulation_era"]),
            driver_key=str(row["driver_id"]),
            lap_number=int(row["lap_number"]),
            lap_time_seconds=float(row["lap_time"]),
            compound=str(row["tire_compound"]).upper() if row.get("tire_compound") else None,
            is_valid=True,
        )
        for row in rows
        if row.get("regulation_era") is not None
    )

    warnings: list[str] = []
    if not observations:
        warnings.append("No clean dry race-pace observations matched the requested filter")
    elif len(observations) < 100:
        warnings.append(f"Only {len(observations)} clean race-pace laps matched the requested filter")

    return observations, tuple(warnings)
