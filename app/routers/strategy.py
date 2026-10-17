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


_WINNING_TTL_SECONDS = 1800
_winning_cache: dict = {}


@router.get("/winning")
def winning_strategy(
    race_id: int = Query(..., ge=1, description="Database race ID"),
    db: Session = Depends(get_db),
):
    """Predicted winning strategy for the FRONT of the grid (P1-P5), with pit windows.

    Stop count from this weekend's practice tyre wear (else the era's usual count); tyre order
    and pit windows from front-grid cars with that stop count at this track (stops under
    SC/VSC/red flag left out; windows at most 10 laps). Also: the runner-up plan, a confidence
    level, the last three races here and, once the race is run, how it played out.
    Walk-forward 2022-25 (top-5 starters): stop count right 47.8%, tyre order 30.8%, first stop
    inside its window 38.6% -- level with the all-car strategy (/strategy/predict).
    """
    import time

    from race_winning_strategy_v1 import (predict_for_race, record_to_json, review_to_json,
                                          strategy_to_json)
    import strategy_service_v7

    key = (strategy_service_v7.db_url(db), race_id)
    hit = _winning_cache.get(key)
    if hit and time.time() - hit[0] < _WINNING_TTL_SECONDS:
        return hit[1]
    try:
        prediction, record, review, race = predict_for_race(db, race_id, "hybrid")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if prediction is None:
        raise HTTPException(status_code=404, detail="no prediction: race distance, era or history unknown")
    body = {
        "race_id": race_id, "track": race["track_name"], "season": race["season_year"],
        "race_date": str(race["race_date"]), "total_laps": race.get("total_laps"),
        "practice_wear_measured": race.get("practice_wear") is not None,
        "prediction": strategy_to_json(prediction), "track_record": record_to_json(record),
        "review": review_to_json(review) if review is not None else None,
    }
    _winning_cache[key] = (time.time(), body)
    return body


@router.get("/winning/pre-start")
def winning_strategy_pre_start(
    race_id: int = Query(..., ge=1, description="Database race ID"),
    db: Session = Depends(get_db),
):
    """The winning strategy once the front grid's starting tyres are declared: one plan per
    starting tyre, from front-grid cars that started on it (stop count still from practice tyre
    wear). Walk-forward 2022-25: first pit window 5.8 laps wide instead of 6.1 at the same hit
    rate; tyre order (no safety-car stop) 41% vs 38%, mostly from knowing the first tyre.
    Starting tyres come from start_tyres_v1 (OpenF1 or entered by hand); 404 until stored.
    """
    from race_winning_strategy_v1 import pre_start_to_json, predict_pre_start_for_race, record_to_json

    try:
        result = predict_pre_start_for_race(db, race_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if result is None:
        raise HTTPException(status_code=404, detail="no starting tyres stored for the front grid yet")
    race = result["race"]
    return {"race_id": race_id, "track": race["track_name"], "season": race["season_year"],
            "race_date": str(race["race_date"]), **pre_start_to_json(result),
            "track_record": record_to_json(result["record"]),
            "note": "Green-flag plans for the front of the grid (P1-P5): a Safety Car or VSC can open an "
                    "earlier, cheaper stop."}


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
        # the weather service being down is a 503; anything else (unknown track, no race data
        # for that track or date) is about the request, not a server failure
        status_code = 503 if result.get("weather_status") == "unavailable" else 404

        raise HTTPException(
            status_code=status_code,
            detail=result.get(
                "message",
                "Strategy prediction unavailable",
            ),
        )

    return result
