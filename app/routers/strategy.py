from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Literal, Optional

from app.database import get_db
from strategy_production_v6 import predict
import strategy_service_v7


# --- v7 (current) ----------------------------------------------------------------

class V7Stint(BaseModel):
    compound: str
    start_lap: int
    end_lap: int


class V7Strategy(BaseModel):
    sequence: list[str]
    stop_laps: list[int]
    stints: list[V7Stint]
    share: Optional[float] = None


class V7Prediction(BaseModel):
    source: str
    primary_strategy: V7Strategy
    alternatives: list[V7Strategy]


class V7Weather(BaseModel):
    tier: Optional[str] = None
    days_out: Optional[int] = None
    rain_expected: Optional[bool] = None
    rain_onset_lap: Optional[int] = None
    wet_strategy_trigger: Optional[bool] = None
    target_track_temp: Optional[float] = None


class StrategyPredictionV7(V7Prediction):
    status: str
    model_version: str
    track_id: int
    track_name: str
    race_id: Optional[int] = None
    race_date: date
    regulation_era: str
    total_laps: int
    stage: str
    grid: Optional[int] = None
    start_compound: Optional[str] = None
    weather: Optional[V7Weather] = None
    validation: dict
    data_notes: list[str]


# --- v6 (legacy, kept for comparison) -------------------------------------------


class StrategyStint(BaseModel):
    compound: str
    avg_start_lap: Optional[int] = None
    avg_end_lap: Optional[int] = None
    sample_count: Optional[int] = None
    window_source: Optional[str] = None
    c_compound: Optional[str] = None
    start_lap: Optional[int] = None
    end_lap: Optional[int] = None


class StrategyOption(BaseModel):
    sequence: list[str]
    score: float
    historical_races: int
    stints: Optional[list[StrategyStint]] = None
    strategy_source: Optional[str] = None


class StopProbabilities(BaseModel):
    probabilities: dict[str, float]


class ForecastConditions(BaseModel):
    track_temp: Optional[float] = None
    rain_expected: Optional[bool] = None
    rain_onset_lap: Optional[int] = None


class StrategyEvidence(BaseModel):
    same_era_historical_races: int
    condition_matched_analogs: int
    fp2_available: bool
    compound_nominations_available: bool
    wet_evidence_races: int
    era_fallback_used: bool
    evidence_scope: str


class SupportingStint(BaseModel):
    compound: str
    start_lap: int
    end_lap: int


class SupportingRace(BaseModel):
    race_id: int
    season: int
    round: int
    race_date: date
    track_name: str
    sequence: list[str]
    stints: list[SupportingStint]


class PrimaryStrategyEvidence(BaseModel):
    distinct_races: int
    scope: str
    supporting_races: list[SupportingRace]


class WetRecommendation(BaseModel):
    model: str
    mode: str
    sequence: list[str]
    strategy_source: str
    confidence: str
    reason: str


class StrategyPrediction(BaseModel):
    status: str
    strategy: list[StrategyStint]
    primary_strategy: StrategyOption
    alternatives: list[StrategyOption]
    stop_probabilities: dict[str, float]
    confidence_pct: int
    confidence_level: str
    strategy_source: str
    wet_recommendation: Optional[WetRecommendation] = None
    weather_context: str
    wet_race_warning: bool
    wet_model_reliability: str
    tier: str
    days_out: int
    regulation_era: str
    forecast_conditions: ForecastConditions
    evidence: StrategyEvidence
    compound_nominations: dict[str, str]
    evidence_scope: str
    primary_strategy_evidence: PrimaryStrategyEvidence
    selection_method: str
    confidence_note: str

router = APIRouter(
    prefix="/strategy",
    tags=["Tire Strategy"],
)


@router.get("/predict", response_model=StrategyPredictionV7)
def predict_strategy(
    track_id: int = Query(..., ge=1, description="Database track ID"),
    race_date: date = Query(..., description="Race date in YYYY-MM-DD format"),
    grid: Optional[int] = Query(None, ge=1, le=30,
                                description="Grid slot; used with start_compound (pre-start stage)"),
    start_compound: Optional[Literal["SOFT", "MEDIUM", "HARD", "soft", "medium", "hard"]] = Query(
        None, description="Declared starting tyre (pre-start stage)"),
    db: Session = Depends(get_db),
):
    """Most likely strategy + alternatives from real stints before race_date (strategy_service_v7).

    Before the start tyre is known this is the era's most common strategy (the track /
    grid-band variant was worse in the walk-forward backtest); with start_compound, history
    is filtered to strategies that started on it.
    """
    try:
        return strategy_service_v7.predict(db, track_id, race_date, grid=grid, start_compound=start_compound)
    except strategy_service_v7.StrategyNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/predict-v6", response_model=StrategyPrediction, deprecated=True)
def predict_strategy_v6(
    track_id: int = Query(
        ...,
        description="Database track ID",
    ),
    race_date: date = Query(
        ...,
        description="Race date in YYYY-MM-DD format",
    ),
):
    result = predict(
        track_id,
        race_date,
    )

    if result.get("status") != "ok":
        if result.get("weather_status") == "unavailable":
            status_code = 503
        elif result.get("status") == "not_found":
            status_code = 404
        else:
            status_code = 500

        raise HTTPException(
            status_code=status_code,
            detail=result.get(
                "message",
                "Strategy prediction unavailable",
            ),
        )

    return result
