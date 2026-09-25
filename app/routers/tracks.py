from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.database import get_db
from race_status import CLASSIFIED_STATUSES

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


@router.get("/{track_id}/overtaking-index")
def get_overtaking_index(track_id: int, db: Session = Depends(get_db)):
    track = db.execute(text("SELECT id, name FROM tracks WHERE id = :id"), {"id": track_id}).mappings().first()
    if track is None:
        raise HTTPException(status_code=404, detail="Track not found")

    # average |grid position - finishing position| per driver per race, broken out by
    # regulation era since 2022+ ground-effect cars were specifically designed to enable
    # closer following -- mixing eras would dilute a real, meaningful shift in overtaking
    by_era = db.execute(text("""
        SELECT r.regulation_era,
               AVG(ABS(rr.starting_grid_position - rr.finishing_position)) AS avg_position_change,
               COUNT(*) AS driver_race_count,
               COUNT(DISTINCT r.id) AS races_counted,
               AVG(ABS(rr.starting_grid_position - rr.finishing_position)) FILTER (
                   WHERE rr.status = ANY(:classified) AND rr.starting_grid_position > 0
               ) AS avg_position_change_classified
        FROM race_results rr
        JOIN race_entries re ON re.id = rr.race_entry_id
        JOIN races r ON r.id = re.race_id
        JOIN sessions s ON s.id = rr.session_id AND s.session_type = 'R'
        WHERE r.track_id = :id
          AND rr.starting_grid_position IS NOT NULL
          AND rr.finishing_position IS NOT NULL
        GROUP BY r.regulation_era
        ORDER BY r.regulation_era
    """), {"id": track_id, "classified": list(CLASSIFIED_STATUSES)}).mappings().all()

    overall = db.execute(text("""
        SELECT AVG(ABS(rr.starting_grid_position - rr.finishing_position)) AS avg_position_change,
               COUNT(*) AS driver_race_count,
               COUNT(DISTINCT r.id) AS races_counted,
               AVG(ABS(rr.starting_grid_position - rr.finishing_position)) FILTER (
                   WHERE rr.status = ANY(:classified) AND rr.starting_grid_position > 0
               ) AS avg_position_change_classified
        FROM race_results rr
        JOIN race_entries re ON re.id = rr.race_entry_id
        JOIN races r ON r.id = re.race_id
        JOIN sessions s ON s.id = rr.session_id AND s.session_type = 'R'
        WHERE r.track_id = :id
          AND rr.starting_grid_position IS NOT NULL
          AND rr.finishing_position IS NOT NULL
    """), {"id": track_id, "classified": list(CLASSIFIED_STATUSES)}).mappings().first()

    # safety car / VSC historical likelihood at this track (same logic used by the
    # tire strategy predictor's flexibility note)
    sc_row = db.execute(text("""
        SELECT
            COUNT(*) AS total_races,
            SUM(CASE WHEN COALESCE(safety_car_periods, 0) > 0 OR COALESCE(vsc_periods, 0) > 0 THEN 1 ELSE 0 END) AS races_with_sc
        FROM races
        WHERE track_id = :id AND race_date < CURRENT_DATE
    """), {"id": track_id}).mappings().first()

    sc_likelihood_pct = (
        round(100 * sc_row["races_with_sc"] / sc_row["total_races"])
        if sc_row and sc_row["total_races"] > 0 else None
    )

    return {
        "track_id": track["id"],
        "track_name": track["name"],
        "overtaking": {
            "overall": {
                "avg_position_change": round(float(overall["avg_position_change"]), 2) if overall["avg_position_change"] is not None else None,
                "avg_position_change_classified": round(float(overall["avg_position_change_classified"]), 2) if overall["avg_position_change_classified"] is not None else None,
                "races_counted": overall["races_counted"],
            },
            "by_regulation_era": [
                {
                    "regulation_era": row["regulation_era"],
                    "avg_position_change": round(float(row["avg_position_change"]), 2),
                    "avg_position_change_classified": round(float(row["avg_position_change_classified"]), 2) if row["avg_position_change_classified"] is not None else None,
                    "races_counted": row["races_counted"],
                }
                for row in by_era
            ],
        },
        "notes": {
            "avg_position_change": "all cars with a grid and finishing position; retirements count as positions lost",
            "avg_position_change_classified": "classified finishers only, pit-lane starts excluded",
        },
        "safety_car_likelihood_pct": sc_likelihood_pct,
        "safety_car_sample_races": sc_row["total_races"] if sc_row else 0,
    }
