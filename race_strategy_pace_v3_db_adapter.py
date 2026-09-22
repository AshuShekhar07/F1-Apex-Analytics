"""Load leakage-safe race pace observations with team/car identity."""
from __future__ import annotations

from typing import Any
from sqlalchemy import text
from race_strategy_pace_calibration_v3 import EnrichedPaceObservation


def _rows(db: Any, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    """Execute through either a SQLAlchemy Connection or Engine."""
    if hasattr(db, "execute"):
        result = db.execute(text(sql), params)
        if hasattr(result, "mappings"):
            return [dict(row) for row in result.mappings().all()]
        return [dict(row) for row in result]

    if hasattr(db, "connect"):
        with db.connect() as conn:
            result = conn.execute(text(sql), params)
            if hasattr(result, "mappings"):
                return [dict(row) for row in result.mappings().all()]
            return [dict(row) for row in result]

    raise TypeError("db must be a SQLAlchemy Engine or Connection")


def load_pace_observations(db: Any, *, start_year: int = 2018, end_year: int = 2026, era: str | None = None) -> tuple[tuple[EnrichedPaceObservation, ...], tuple[str, ...]]:
    """Load clean dry race laps while preserving driver/team/track/era identity."""
    where = [
        "r.race_date IS NOT NULL",
        "r.season_year BETWEEN :start_year AND :end_year",
        "s.session_type = 'R'",
        "l.lap_time IS NOT NULL",
        "l.is_valid = TRUE",
        "l.lap_time BETWEEN 40.0 AND 180.0",
        "l.tire_compound IN ('SOFT', 'MEDIUM', 'HARD')",
        "COALESCE(sw.rainfall, FALSE) = FALSE",
        "re.driver_id IS NOT NULL",
        "re.team_id IS NOT NULL",
        "r.regulation_era IS NOT NULL",
    ]
    params: dict[str, Any] = {"start_year": start_year, "end_year": end_year}
    if era is not None:
        where.append("r.regulation_era = :era")
        params["era"] = era
    rows = _rows(db, f"""
        SELECT r.id AS race_id, r.track_id, r.season_year, r.regulation_era,
               re.driver_id, re.team_id, l.lap_number, l.lap_time,
               l.tire_compound
        FROM laps l
        JOIN sessions s ON s.id = l.session_id
        JOIN races r ON r.id = s.race_id
        JOIN race_entries re ON re.id = l.race_entry_id
        LEFT JOIN session_weather sw ON sw.session_id = s.id
        WHERE {' AND '.join(where)}
        ORDER BY r.race_date, r.id, re.driver_id, l.lap_number
    """, params)
    observations = tuple(
        EnrichedPaceObservation(
            race_id=int(row['race_id']), track_id=int(row['track_id']),
            season_year=int(row['season_year']), regulation_era=str(row['regulation_era']),
            driver_key=str(row['driver_id']), team_key=str(row['team_id']),
            lap_number=int(row['lap_number']), lap_time_seconds=float(row['lap_time']),
            compound=str(row['tire_compound']).upper(), is_valid=True,
        )
        for row in rows
    )
    warnings: list[str] = []
    if not observations:
        warnings.append('No clean dry race pace observations matched the requested filter')
    elif len(observations) < 1000:
        warnings.append(f'Low race pace sample: n={len(observations)}')
    return observations, tuple(warnings)
