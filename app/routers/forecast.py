from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from race_forecast_v1 import MODEL_VERSION, WHAT_IF_NOISE_SCALE, WHAT_IF_THRESHOLD, Scenario, run_what_if

router = APIRouter(prefix="/races", tags=["forecast"])


class StrategyPlan(BaseModel):
    sequence: list[str] = Field(..., min_length=2, examples=[["MEDIUM", "HARD"]])
    stop_laps: list[int] = Field(..., examples=[[24]])


class WhatIfRequest(BaseModel):
    safety_car_laps: list[int] = Field(default_factory=list, description="laps run under the Safety Car")
    vsc_laps: list[int] = Field(default_factory=list, description="laps run under the Virtual Safety Car")
    strategies: dict[int, StrategyPlan] = Field(default_factory=dict,
                                                description="driver_id -> strategy that driver runs instead")
    simulations: int = Field(1000, ge=100, le=5000)


def _load_forecast(db: Session, race_id: int) -> Optional[dict]:
    return db.execute(text("""
        SELECT id, race_id, model_version, as_of_date, grid_source, computed_at, inputs, validation
        FROM race_forecasts WHERE race_id = :r AND model_version = :v
    """), {"r": race_id, "v": MODEL_VERSION}).mappings().first()


@router.get("/{race_id}/forecast")
def get_forecast(race_id: int = Path(..., ge=1), db: Session = Depends(get_db)):
    forecast = _load_forecast(db, race_id)
    if forecast is None:
        raise HTTPException(status_code=404, detail="No forecast computed for this race yet "
                                                    "(run race_forecast_v1.py after qualifying)")
    drivers = db.execute(text("""
        SELECT fd.driver_id, d.name AS driver_name, tm.name AS team_name, tm.color_hex,
               fd.grid, fd.predicted_position, fd.p_win, fd.p_podium, fd.strategy
        FROM race_forecast_drivers fd
        JOIN drivers d ON d.id = fd.driver_id
        LEFT JOIN race_entries re ON re.driver_id = fd.driver_id AND re.race_id = :r
        LEFT JOIN teams tm ON tm.id = re.team_id
        WHERE fd.forecast_id = :f
        ORDER BY fd.predicted_position, fd.grid
    """), {"f": forecast["id"], "r": race_id}).mappings().all()
    inputs = forecast["inputs"]
    return {
        "race_id": race_id,
        "model_version": forecast["model_version"],
        "as_of_date": forecast["as_of_date"],
        "grid_source": forecast["grid_source"],
        "computed_at": forecast["computed_at"],
        "drivers": [{**dict(d), "p_win": float(d["p_win"]), "p_podium": float(d["p_podium"])} for d in drivers],
        "strategy_alternatives": inputs.get("strategy_options", []),
        "data_notes": inputs.get("data_notes", []),
        "validation": forecast["validation"],
    }


@router.post("/{race_id}/what-if")
def what_if(request: WhatIfRequest, race_id: int = Path(..., ge=1), db: Session = Depends(get_db)):
    forecast = _load_forecast(db, race_id)
    if forecast is None:
        raise HTTPException(status_code=404, detail="No forecast computed for this race yet")
    scenario = Scenario(
        safety_car_laps=tuple(request.safety_car_laps),
        vsc_laps=tuple(request.vsc_laps),
        strategies=tuple((driver_id, plan.model_dump()) for driver_id, plan in request.strategies.items()),
    )
    try:
        results = run_what_if(forecast["inputs"], scenario, sims=request.simulations)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    names = {row["driver_id"]: row for row in db.execute(text("""
        SELECT d.id AS driver_id, d.name AS driver_name, tm.name AS team_name, tm.color_hex
        FROM race_entries re JOIN drivers d ON d.id = re.driver_id LEFT JOIN teams tm ON tm.id = re.team_id
        WHERE re.race_id = :r
    """), {"r": race_id}).mappings()}
    results = [{**{k: v for k, v in names.get(r["driver_id"], {}).items() if k != "driver_id"}, **r} for r in results]
    return {
        "race_id": race_id,
        "note": "Scenario comparison from simulator v2. Differences between baseline and scenario are the "
                "meaningful output; absolute numbers are less reliable than the forecast endpoint.",
        "scenario": request.model_dump(),
        "simulation_settings": {"overtake_threshold_s_per_lap": WHAT_IF_THRESHOLD,
                                "race_day_spread_scale": WHAT_IF_NOISE_SCALE},
        "results": sorted(results, key=lambda r: r["scenario"]["expected_position"]),
    }
