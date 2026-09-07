from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.database import get_db

router = APIRouter(prefix="/predictions", tags=["predictions"])

VALID_TYPES = {"wdc", "constructors", "next_race"}


def _latest_round(db, season_year, prediction_type):
    row = db.execute(text("""
        SELECT MAX(as_of_round) AS latest
        FROM season_predictions
        WHERE season_year = :season_year AND prediction_type = :prediction_type
    """), {"season_year": season_year, "prediction_type": prediction_type}).mappings().first()
    return row["latest"] if row else None


@router.get("/{season_year}/constructors")
def get_constructor_predictions(season_year: int, db: Session = Depends(get_db)):
    latest = _latest_round(db, season_year, "constructors")
    if latest is None:
        raise HTTPException(status_code=404, detail="No constructor predictions for this season")

    rows = db.execute(text("""
        SELECT sp.entity_id AS team_id, sp.entity_name AS team_name,
               sp.probability_pct, sp.as_of_round, sp.computed_at,
               tm.color_hex, tm.logo_url
        FROM season_predictions sp
        LEFT JOIN teams tm ON tm.id = sp.entity_id
        WHERE sp.season_year = :season_year
          AND sp.prediction_type = 'constructors'
          AND sp.as_of_round = :latest
        ORDER BY sp.probability_pct DESC
    """), {"season_year": season_year, "latest": latest}).mappings().all()

    return {"season_year": season_year, "as_of_round": latest, "predictions": [dict(r) for r in rows]}


@router.get("/{season_year}/{prediction_type}")
def get_driver_predictions(season_year: int, prediction_type: str, db: Session = Depends(get_db)):
    """Handles 'wdc' and 'next_race' -- both are driver-keyed predictions,
    enriched with each driver's current team for team-colored UI later."""
    if prediction_type not in ("wdc", "next_race"):
        raise HTTPException(status_code=422, detail="prediction_type must be one of: wdc, next_race, constructors")

    latest = _latest_round(db, season_year, prediction_type)
    if latest is None:
        raise HTTPException(status_code=404, detail=f"No {prediction_type} predictions for this season")

    rows = db.execute(text("""
        WITH current_teams AS (
            SELECT DISTINCT ON (re.driver_id)
                re.driver_id, tm.id AS team_id, tm.name AS team_name,
                tm.color_hex, tm.logo_url
            FROM race_entries re
            JOIN races r ON r.id = re.race_id
            JOIN teams tm ON tm.id = re.team_id
            WHERE re.role = 'race_driver'
            ORDER BY re.driver_id, r.season_year DESC, r.round_number DESC
        )
        SELECT sp.entity_id AS driver_id, sp.entity_name AS driver_name,
               sp.probability_pct, sp.as_of_round, sp.computed_at,
               d.nationality, d.photo_url,
               ct.team_name AS current_team, ct.color_hex, ct.logo_url AS team_logo_url
        FROM season_predictions sp
        LEFT JOIN drivers d ON d.id = sp.entity_id
        LEFT JOIN current_teams ct ON ct.driver_id = sp.entity_id
        WHERE sp.season_year = :season_year
          AND sp.prediction_type = :prediction_type
          AND sp.as_of_round = :latest
        ORDER BY sp.probability_pct DESC
    """), {"season_year": season_year, "prediction_type": prediction_type, "latest": latest}).mappings().all()

    return {"season_year": season_year, "as_of_round": latest, "predictions": [dict(r) for r in rows]}


@router.get("/{season_year}/{prediction_type}/history")
def get_prediction_history(season_year: int, prediction_type: str, db: Session = Depends(get_db)):
    """Every stored round's snapshot for one entity type -- powers a
    probability-over-time trend chart later, using data already captured
    by season_predictions storing every simulation run, not just the latest."""
    if prediction_type not in VALID_TYPES:
        raise HTTPException(status_code=422, detail="prediction_type must be one of: wdc, constructors, next_race")

    rows = db.execute(text("""
        SELECT entity_id, entity_name, probability_pct, as_of_round, computed_at
        FROM season_predictions
        WHERE season_year = :season_year AND prediction_type = :prediction_type
        ORDER BY as_of_round, probability_pct DESC
    """), {"season_year": season_year, "prediction_type": prediction_type}).mappings().all()

    return {"season_year": season_year, "prediction_type": prediction_type, "history": [dict(r) for r in rows]}
