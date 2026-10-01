"""Read-only adapter for stored FastF1 pit-lane observations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import text


@dataclass(frozen=True)
class StoredPitLaneObservation:
    race_id: int
    race_entry_id: int
    pit_lap: int
    total_pit_lane_seconds: float
    source: str


def load_pit_lane_observations(
    db: Any,
    *,
    start_year: int = 2018,
    end_year: int = 2026,
    era: str | None = None,
) -> tuple[tuple[StoredPitLaneObservation, ...], tuple[str, ...]]:
    """Load reconstructed total pit-lane observations without inventing service time."""
    where = [
        "r.season_year BETWEEN :start_year AND :end_year",
        "p.total_pit_lane_seconds IS NOT NULL",
    ]
    params: dict[str, Any] = {"start_year": start_year, "end_year": end_year}
    if era is not None:
        where.append("r.regulation_era = :era")
        params["era"] = era

    result = db.execute(
        text(f"""
            SELECT
                p.race_id,
                p.race_entry_id,
                p.pit_lap,
                p.total_pit_lane_seconds,
                p.source
            FROM race_strategy_pit_stops p
            JOIN races r ON r.id = p.race_id
            WHERE {' AND '.join(where)}
            ORDER BY r.season_year, p.race_id, p.race_entry_id, p.pit_lap
        """),
        params,
    )
    rows = result.mappings().all() if hasattr(result, "mappings") else result
    observations = tuple(
        StoredPitLaneObservation(
            race_id=int(row["race_id"]),
            race_entry_id=int(row["race_entry_id"]),
            pit_lap=int(row["pit_lap"]),
            total_pit_lane_seconds=float(row["total_pit_lane_seconds"]),
            source=str(row["source"]),
        )
        for row in rows
    )
    warnings: tuple[str, ...] = () if observations else ("No stored FastF1 pit-lane observations matched the requested filter",)
    return observations, warnings
