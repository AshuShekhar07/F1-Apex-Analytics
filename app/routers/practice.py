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
        -- rank per race entry, not per driver: FP1 reserve/rookie drivers have
        -- their own entry (role != 'race_driver') and belong in the classification.
        -- Laps deleted for track limits (FastF1 enrichment) do not count, matching
        -- official timing; unenriched laps have is_deleted NULL and still count.
        eligible_laps AS (
            SELECT l.*
            FROM laps l
            WHERE l.session_id = (SELECT session_id FROM session_pick)
              AND l.lap_time IS NOT NULL
              AND COALESCE(l.is_deleted, false) = false
        ),
        lap_counts AS (
            SELECT l.race_entry_id, COUNT(l.id) AS total_laps
            FROM laps l
            WHERE l.session_id = (SELECT session_id FROM session_pick)
            GROUP BY l.race_entry_id
        ),
        best_laps AS (
            SELECT el.race_entry_id, MIN(el.lap_time) AS best_lap_time
            FROM eligible_laps el
            GROUP BY el.race_entry_id
        ),
        best_lap_detail AS (
            SELECT DISTINCT ON (el.race_entry_id)
                el.race_entry_id, el.sector_1_time, el.sector_2_time, el.sector_3_time, el.tire_compound
            FROM eligible_laps el
            ORDER BY el.race_entry_id, el.lap_time ASC, el.lap_number ASC
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
            re.role,
            tm.name AS team,
            tm.logo_url,
            bl.best_lap_time,
            bl.best_lap_time - MIN(bl.best_lap_time) OVER () AS gap_to_fastest,
            lc.total_laps,
            bld.tire_compound,
            bld.sector_1_time,
            bld.sector_2_time,
            bld.sector_3_time,
            bl.best_lap_time - (SELECT record_time FROM track_record) AS delta_to_track_record
        FROM best_laps bl
        JOIN race_entries re ON re.id = bl.race_entry_id AND re.race_id = :race_id
        JOIN drivers d ON d.id = re.driver_id
        JOIN best_lap_detail bld ON bld.race_entry_id = bl.race_entry_id
        JOIN lap_counts lc ON lc.race_entry_id = bl.race_entry_id
        JOIN teams tm ON re.team_id = tm.id
        ORDER BY position, d.name
    """), {"race_id": race_id, "session_type": session_type}).mappings().all()

    return {
        "race": dict(race),
        "session_type": session_type,
        "track_record_note": (
            "delta_to_track_record compares with the fastest valid race lap stored for this "
            "circuit since 2018 (any layout), not the official FIA lap record"
        ),
        "results": [dict(r) for r in rows],
    }
