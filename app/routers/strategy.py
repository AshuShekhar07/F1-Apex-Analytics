from datetime import date

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

from strategy_production_v6 import predict


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


class StrategyPrediction(BaseModel):
    status: str
    strategy: list[StrategyStint]
    primary_strategy: StrategyOption
    alternatives: list[StrategyOption]
    stop_probabilities: dict[str, float]
    confidence_pct: int
    confidence_level: str
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


@router.get("/predict", response_model=StrategyPrediction)
def predict_strategy(
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
        raise HTTPException(
            status_code=404,
            detail=result.get(
                "message",
                "Strategy prediction unavailable",
            ),
        )

    return result
