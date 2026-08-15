from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.database import get_db

router = APIRouter(prefix="/drivers", tags=["drivers"])


@router.get("/{driver_id}/history")
def get_driver_history(driver_id: int, db: Session = Depends(get_db)):
    driver = db.execute(text("""
        SELECT id, name, nationality, permanent_number
        FROM drivers WHERE id = :id
    """), {"id": driver_id}).mappings().first()

    if driver is None:
        raise HTTPException(status_code=404, detail="Driver not found")

    history = db.execute(text("""
        SELECT r.season_year, r.round_number, t.name AS track_name,
               tm.name AS team_name, tm.color_hex,
               rr.finishing_position, rr.points, rr.status
        FROM race_entries re
        JOIN races r ON r.id = re.race_id
        JOIN tracks t ON t.id = r.track_id
        JOIN teams tm ON tm.id = re.team_id
        LEFT JOIN race_results rr ON rr.race_entry_id = re.id
            AND rr.session_id = (
                SELECT id FROM sessions
                WHERE race_id = r.id AND session_type = 'R'
                LIMIT 1
            )
        WHERE re.driver_id = :id
        ORDER BY r.season_year, r.round_number
    """), {"id": driver_id}).mappings().all()

    total_points = sum(row["points"] or 0 for row in history)
    wins = sum(1 for row in history if row["finishing_position"] == 1)
    podiums = sum(1 for row in history if row["finishing_position"] and row["finishing_position"] <= 3)

    return {
        **dict(driver),
        "career_summary": {
            "races": len(history),
            "wins": wins,
            "podiums": podiums,
            "total_points": total_points,
        },
        "race_history": list(history),
    }
