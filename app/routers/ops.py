"""Operational endpoints: /health for uptime checks and monitoring, /about for the public
disclaimer and how the predictions are validated."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.settings import settings

router = APIRouter(tags=["ops"])

STALE_RUN_HOURS = 7 * 24 + 12      # the pipeline runs at least weekly; older than this = stale


def _table_exists(db: Session, name: str) -> bool:
    return bool(db.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": name}).scalar())


@router.get("/health")
def health(db: Session = Depends(get_db)):
    """200 when the database answers and the data is reasonably fresh; 503 otherwise."""
    from race_forecast_v1 import MODEL_VERSION

    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:                             # database down or misconfigured
        return JSONResponse(status_code=503, content={"status": "error", "database": f"unavailable: {type(exc).__name__}"})

    latest_result = db.execute(text("""
        SELECT r.id, r.season_year, r.round_number, r.race_date FROM races r
        JOIN sessions s ON s.race_id = r.id AND s.session_type = 'R'
        WHERE EXISTS (SELECT 1 FROM race_results rr WHERE rr.session_id = s.id)
        ORDER BY r.race_date DESC LIMIT 1""")).mappings().first()
    latest_forecast = db.execute(text("""
        SELECT f.race_id, f.grid_source, f.computed_at FROM race_forecasts f
        WHERE f.model_version = :v ORDER BY f.computed_at DESC LIMIT 1"""), {"v": MODEL_VERSION}).mappings().first()
    last_run, stale = None, False
    if _table_exists(db, "pipeline_runs"):
        last_run = db.execute(text("""
            SELECT job, started_at, finished_at, status, detail,
                   EXTRACT(EPOCH FROM (now() - started_at)) / 3600 AS hours_ago
            FROM pipeline_runs ORDER BY started_at DESC LIMIT 1""")).mappings().first()
        stale = last_run is None or float(last_run["hours_ago"]) > STALE_RUN_HOURS
    problems = []
    if last_run is not None and last_run["status"] == "failed":
        problems.append("last pipeline run failed")
    if stale:
        problems.append("no pipeline run in the last week")
    body = {
        "status": "degraded" if problems else "ok",
        "problems": problems,
        "environment": settings.env,
        "database": "ok",
        "model_version": MODEL_VERSION,
        "latest_race_with_results": dict(latest_result) if latest_result else None,
        "latest_forecast": dict(latest_forecast) if latest_forecast else None,
        "last_pipeline_run": ({k: v for k, v in last_run.items() if k != "hours_ago"} if last_run else None),
        # computed by the database itself, so the age is right whatever its time zone
        "last_pipeline_run_minutes_ago": round(float(last_run["hours_ago"]) * 60) if last_run else None,
    }
    return JSONResponse(status_code=200, content=_jsonable(body))


def _jsonable(value):
    from fastapi.encoders import jsonable_encoder
    return jsonable_encoder(value)


@router.get("/about")
def about():
    from championship_model_v1 import VALIDATION as CHAMPIONSHIP_VALIDATION
    from race_forecast_v1 import VALIDATION
    from strategy_service_v7 import VALIDATION as STRATEGY_VALIDATION

    return {
        "name": "Apex21",
        "disclaimer": ("Independent fan project. Not affiliated with, endorsed by or connected to Formula 1, "
                       "the FIA or any Formula 1 team. F1, FORMULA 1 and related marks are trademarks of "
                       "Formula One Licensing B.V.; team names are used only to identify the teams."),
        "data_sources": ["FastF1 (F1 live-timing archive)", "OpenF1 (provisional starting grid)",
                         "Jolpica/Ergast (historical results)"],
        "how_predictions_are_tested": ("Every model is scored walk-forward: each past race is predicted using only "
                                       "races before it. A change goes live only if it is better on some measure "
                                       "and worse on none. Forecasts made before a race are also frozen and "
                                       "scored as the live record."),
        "finishing_order": VALIDATION.get("predicted_position"),
        "race_winner": CHAMPIONSHIP_VALIDATION["race_winner"],
        "drivers_title": CHAMPIONSHIP_VALIDATION["drivers_title"],
        "constructors_title": CHAMPIONSHIP_VALIDATION["constructors_title"],
        "championship_caveat": CHAMPIONSHIP_VALIDATION["caveat"],
        "strategy": STRATEGY_VALIDATION.get("dry"),
    }
