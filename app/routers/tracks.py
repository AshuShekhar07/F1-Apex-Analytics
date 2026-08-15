from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.database import get_db

router = APIRouter(prefix="/tracks", tags=["tracks"])


@router.get("/{track_id}/stats")
def get_track_stats(track_id: int, db: Session = Depends(get_db)):
    track = db.execute(text("""
        SELECT id, name, country, length_km, lap_record
        FROM tracks WHERE id = :id
    """), {"id": track_id}).mappings().first()

    if track is None:
        raise HTTPException(status_code=404, detail="Track not found")

    races_here = db.execute(text("""
        SELECT r.id, r.season_year, r.round_number
        FROM races r WHERE r.track_id = :id
        ORDER BY r.season_year
    """), {"id": track_id}).mappings().all()

    first_year = races_here[0]["season_year"] if races_here else None

    winners = db.execute(text("""
        SELECT r.season_year, d.name AS driver_name, tm.name AS team_name, tm.color_hex
        FROM races r
        JOIN sessions s ON s.race_id = r.id AND s.session_type = 'R'
        JOIN race_results rr ON rr.session_id = s.id AND rr.finishing_position = 1
        JOIN race_entries re ON re.id = rr.race_entry_id
        JOIN drivers d ON d.id = re.driver_id
        JOIN teams tm ON tm.id = re.team_id
        WHERE r.track_id = :id
        ORDER BY r.season_year DESC
    """), {"id": track_id}).mappings().all()

    return {
        **dict(track),
        "first_year_raced": first_year,
        "total_races_recorded": len(races_here),
        "historical_winners": list(winners),
    }
