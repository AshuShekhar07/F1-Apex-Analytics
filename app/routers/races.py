from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.database import get_db

router = APIRouter(prefix="/races", tags=["races"])


@router.get("")
def list_races(season: int | None = None, db: Session = Depends(get_db)):
    query = """
        SELECT r.id, r.season_year, r.round_number, r.race_date,
               r.weekend_format, t.name AS track_name, t.country
        FROM races r
        JOIN tracks t ON t.id = r.track_id
    """
    params = {}
    if season is not None:
        query += " WHERE r.season_year = :season"
        params["season"] = season
    query += " ORDER BY r.season_year, r.round_number"

    rows = db.execute(text(query), params).mappings().all()
    return list(rows)


@router.get("/{race_id}")
def get_race(race_id: int, db: Session = Depends(get_db)):
    race = db.execute(text("""
        SELECT r.id, r.season_year, r.round_number, r.race_date,
               r.weekend_format, t.id AS track_id, t.name AS track_name,
               t.country, t.length_km, t.lap_record
        FROM races r
        JOIN tracks t ON t.id = r.track_id
        WHERE r.id = :id
    """), {"id": race_id}).mappings().first()

    if race is None:
        raise HTTPException(status_code=404, detail="Race not found")

    sessions = db.execute(text("""
        SELECT id, session_type, start_time
        FROM sessions
        WHERE race_id = :id
        ORDER BY start_time NULLS LAST
    """), {"id": race_id}).mappings().all()

    race_result_session = db.execute(text("""
        SELECT s.id FROM sessions s
        WHERE s.race_id = :id AND s.session_type = 'R'
    """), {"id": race_id}).scalar()

    results = []
    if race_result_session:
        results = db.execute(text("""
            SELECT d.id AS driver_id, d.name AS driver_name, tm.name AS team_name,
                   tm.color_hex, rr.finishing_position, rr.points, rr.status
            FROM race_results rr
            JOIN race_entries re ON re.id = rr.race_entry_id
            JOIN drivers d ON d.id = re.driver_id
            JOIN teams tm ON tm.id = re.team_id
            WHERE rr.session_id = :sid
            ORDER BY rr.finishing_position
        """), {"sid": race_result_session}).mappings().all()

    return {
        **dict(race),
        "sessions": list(sessions),
        "results": list(results),
    }
