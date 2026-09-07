from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.database import get_db

router = APIRouter(prefix="/predict-and-compete", tags=["predict-and-compete"])


@router.get("/{season_year}/{round_number}")
def get_predict_and_compete(
    season_year: int = Path(..., ge=2018, le=2026),
    round_number: int = Path(..., ge=1, le=23),
    db: Session = Depends(get_db)
):
    race = db.execute(text("""
        SELECT r.id AS race_id, r.race_date, t.name AS track_name
        FROM races r
        JOIN tracks t ON t.id = r.track_id
        WHERE r.season_year = :sy AND r.round_number = :rn
    """), {"sy": season_year, "rn": round_number}).mappings().first()

    if race is None:
        raise HTTPException(status_code=404, detail="Race not found")

    # the model's real output: per-driver win probability for this specific race.
    # NOT a full podium prediction -- the underlying simulator only computes win
    # probability, not joint podium combinations, so we don't fabricate a fake
    # "predicted P2/P3" the model never actually claimed.
    prediction_rows = db.execute(text("""
        SELECT sp.entity_id AS driver_id, sp.entity_name AS driver_name,
               sp.probability_pct, d.photo_url,
               tm.name AS team_name, tm.color_hex, tm.logo_url
        FROM season_predictions sp
        LEFT JOIN drivers d ON d.id = sp.entity_id
        LEFT JOIN race_entries re ON re.driver_id = sp.entity_id AND re.race_id = :race_id
        LEFT JOIN teams tm ON tm.id = re.team_id
        WHERE sp.season_year = :sy AND sp.prediction_type = 'next_race' AND sp.as_of_round = :rn
        ORDER BY sp.probability_pct DESC
    """), {"sy": season_year, "rn": round_number, "race_id": race["race_id"]}).mappings().all()

    model_prediction = None
    if prediction_rows:
        model_prediction = {
            "top_pick": dict(prediction_rows[0]),
            "full_ranking": [dict(r) for r in prediction_rows],
        }

    # the real result, if this race has actually happened
    result_rows = db.execute(text("""
        SELECT rr.finishing_position, d.id AS driver_id, d.name AS driver_name, d.photo_url,
               tm.name AS team_name, tm.color_hex, tm.logo_url
        FROM race_results rr
        JOIN race_entries re ON re.id = rr.race_entry_id
        JOIN drivers d ON d.id = re.driver_id
        JOIN teams tm ON tm.id = re.team_id
        JOIN sessions s ON s.id = rr.session_id AND s.session_type = 'R'
        WHERE s.race_id = :race_id AND rr.finishing_position IS NOT NULL
        ORDER BY rr.finishing_position
        LIMIT 3
    """), {"race_id": race["race_id"]}).mappings().all()

    actual_result = None
    race_has_happened = len(result_rows) > 0
    if race_has_happened:
        winner = dict(result_rows[0])
        model_was_correct = (
            model_prediction is not None and
            model_prediction["top_pick"]["driver_id"] == winner["driver_id"]
        )
        actual_result = {
            "podium": [dict(r) for r in result_rows],
            "winner": winner,
            "model_top_pick_was_correct": model_was_correct,
        }

    return {
        "season_year": season_year,
        "round_number": round_number,
        "race_id": race["race_id"],
        "track_name": race["track_name"],
        "race_date": race["race_date"],
        "race_has_happened": race_has_happened,
        "model_prediction": model_prediction,
        "actual_result": actual_result,
    }
