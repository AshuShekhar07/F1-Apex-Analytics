"""Read-only DB adapter for FP2 compound pace research."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from race_strategy_fp2_compound_calibration_v1 import FP2CompoundObservation


def load_fp2_compound_observations(
    db: Any,
    *,
    start_year: int = 2018,
    end_year: int = 2026,
) -> tuple[tuple[FP2CompoundObservation, ...], tuple[str, ...]]:
    where = [
        "r.season_year BETWEEN :start_year AND :end_year",
        "r.race_date IS NOT NULL",
        "s.session_type = 'FP2'",
        "l.lap_time IS NOT NULL",
        "l.is_valid = TRUE",
        "l.tire_compound IN ('SOFT', 'MEDIUM', 'HARD')",
        "l.lap_number > 0",
        "re.driver_id IS NOT NULL",
    ]
    params: dict[str, Any] = {"start_year": start_year, "end_year": end_year}
    result = db.execute(
        text(f"""
            SELECT
                s.id AS session_id,
                r.id AS race_id,
                r.season_year,
                r.regulation_era,
                re.driver_id,
                l.lap_number,
                l.tire_compound,
                l.lap_time,
                sw.rainfall
            FROM laps l
            JOIN sessions s ON s.id = l.session_id
            JOIN races r ON r.id = s.race_id
            JOIN race_entries re ON re.id = l.race_entry_id
            LEFT JOIN session_weather sw ON sw.session_id = s.id
            WHERE {' AND '.join(where)}
            ORDER BY r.race_date, s.id, re.driver_id, l.lap_number
        """),
        params,
    )
    rows = result.mappings().all() if hasattr(result, "mappings") else result

    # Reconstruct compound-run age from consecutive rows. Missing laps reset the
    # run, so a telemetry gap is not silently treated as normal stint progression.
    observations: list[FP2CompoundObservation] = []
    prev: dict[tuple[int, int], tuple[str, int, int]] = {}
    for row in rows:
        if row.get("rainfall") is True:
            continue
        session_id = int(row["session_id"])
        driver = str(row["driver_id"])
        lap = int(row["lap_number"])
        compound = str(row["tire_compound"]).upper()
        key = (session_id, int(row["driver_id"]))
        prior = prev.get(key)
        if prior and prior[0] == compound and lap == prior[1] + 1:
            age = prior[2] + 1
        else:
            age = 1
        prev[key] = (compound, lap, age)
        observations.append(
            FP2CompoundObservation(
                session_id=session_id,
                race_id=int(row["race_id"]),
                season_year=int(row["season_year"]),
                regulation_era=str(row["regulation_era"]),
                driver_key=driver,
                lap_number=lap,
                stint_age=age,
                compound=compound,
                lap_time_seconds=float(row["lap_time"]),
            )
        )
    warnings: list[str] = []
    if not observations:
        warnings.append("No clean FP2 compound observations found")
    return tuple(observations), tuple(warnings)
