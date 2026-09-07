from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.database import get_db

router = APIRouter(prefix="/races", tags=["practice"])

VALID_SESSIONS = {"FP1", "FP2", "FP3"}


@router.get("/{race_id}/practice/{session_type}")
def get_practice_results(race_id: int, session_type: str, db: Session = Depends(get_db)):
    session_type = session_type.upper()
    if session_type not in VALID_SESSIONS:
        raise HTTPException(status_code=422, detail="session_type must be one of FP1, FP2, FP3")

    race = db.execute(text("""
        SELECT r.id, r.season_year, r.round_number, t.name AS track_name
        FROM races r JOIN tracks t ON r.track_id = t.id
        WHERE r.id = :race_id
    """), {"race_id": race_id}).mappings().first()
    if race is None:
        raise HTTPException(status_code=404, detail="Race not found")

    rows = db.execute(text("""
        WITH session_pick AS (
            SELECT s.id AS session_id
            FROM sessions s
            WHERE s.race_id = :race_id AND s.session_type = :session_type
        ),
        best_laps AS (
            SELECT
                re.driver_id,
                MIN(l.lap_time) AS best_lap_time,
                COUNT(l.id) AS total_laps
            FROM laps l
            JOIN race_entries re ON l.race_entry_id = re.id
            WHERE l.session_id = (SELECT session_id FROM session_pick)
            GROUP BY re.driver_id
        ),
        best_lap_detail AS (
            SELECT DISTINCT ON (re.driver_id)
                re.driver_id, l.sector_1_time, l.sector_2_time, l.sector_3_time, l.tire_compound
            FROM laps l
            JOIN race_entries re ON l.race_entry_id = re.id
            WHERE l.session_id = (SELECT session_id FROM session_pick)
            ORDER BY re.driver_id, l.lap_time ASC
        ),
        track_record AS (
            SELECT MIN(l.lap_time) AS record_time
            FROM laps l
            JOIN sessions s ON l.session_id = s.id
            JOIN races ra ON s.race_id = ra.id
            WHERE ra.track_id = (SELECT track_id FROM races WHERE id = :race_id)
              AND l.is_valid = true
              AND s.session_type = 'R'
              AND NOT (ra.season_year = 2020 AND ra.round_number = 16)
        )
        SELECT
            RANK() OVER (ORDER BY bl.best_lap_time ASC) AS position,
            d.id AS driver_id,
            d.name AS driver,
            tm.name AS team,
            tm.logo_url,
            bl.best_lap_time,
            bl.best_lap_time - MIN(bl.best_lap_time) OVER () AS gap_to_fastest,
            bl.total_laps,
            bld.tire_compound,
            bld.sector_1_time,
            bld.sector_2_time,
            bld.sector_3_time,
            bl.best_lap_time - (SELECT record_time FROM track_record) AS delta_to_track_record
        FROM best_laps bl
        JOIN drivers d ON bl.driver_id = d.id
        JOIN best_lap_detail bld ON bld.driver_id = bl.driver_id
        JOIN race_entries re ON re.driver_id = bl.driver_id
            AND re.race_id = :race_id AND re.role = 'race_driver'
        JOIN teams tm ON re.team_id = tm.id
        WHERE bl.best_lap_time IS NOT NULL
        ORDER BY position
    """), {"race_id": race_id, "session_type": session_type}).mappings().all()

    return {
        "race": dict(race),
        "session_type": session_type,
        "results": [dict(r) for r in rows],
    }
