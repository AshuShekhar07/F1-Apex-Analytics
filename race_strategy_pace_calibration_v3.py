"""Hierarchical, leakage-safe race pace calibration v3.

Separates absolute track pace from team/car and driver residuals. Historical
observations used for a prediction are strictly earlier than the target year.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from statistics import median
from typing import Iterable

from race_strategy_pace_normalization_v1 import PaceObservation, normalize_race_pace


@dataclass(frozen=True)
class EnrichedPaceObservation(PaceObservation):
    team_key: str = ""


@dataclass(frozen=True)
class PaceResidualObservation:
    race_id: int
    track_id: int
    season_year: int
    regulation_era: str
    driver_key: str
    team_key: str
    track_reference_seconds: float
    relative_gap_seconds: float


@dataclass(frozen=True)
class PacePrediction:
    mean_seconds: float
    std_seconds: float
    track_reference_seconds: float
    team_effect_seconds: float
    driver_adjustment_seconds: float
    team_history_n: int
    driver_history_n: int
    warnings: tuple[str, ...] = ()


def build_residual_observations(observations: Iterable[EnrichedPaceObservation]) -> tuple[PaceResidualObservation, ...]:
    """Create one observation per driver-race, preserving team identity."""
    rows = tuple(observations)
    normalized = normalize_race_pace(rows)
    team_by_driver_race: dict[tuple[int, str], str] = {}
    for row in rows:
        team_by_driver_race.setdefault((row.race_id, row.driver_key), str(row.team_key))
    return tuple(
        PaceResidualObservation(
            race_id=row.race_id,
            track_id=row.track_id,
            season_year=row.season_year,
            regulation_era=row.regulation_era,
            driver_key=row.driver_key,
            team_key=team_by_driver_race.get((row.race_id, row.driver_key), ""),
            track_reference_seconds=float(row.track_reference_seconds),
            relative_gap_seconds=float(row.relative_gap_seconds),
        )
        for row in normalized
    )


def _history(rows: Iterable[PaceResidualObservation], as_of_year: int, era: str) -> list[PaceResidualObservation]:
    return [
        row for row in rows
        if row.season_year < as_of_year and row.regulation_era == era
        and isfinite(row.relative_gap_seconds) and isfinite(row.track_reference_seconds)
    ]


def _robust_sigma(values: list[float]) -> float:
    if not values:
        return 0.05
    centre = median(values)
    mad = median(abs(v - centre) for v in values)
    return max(0.05, 1.4826 * mad)


def predict_target_pace(
    observations: Iterable[PaceResidualObservation],
    *,
    target_track_id: int,
    target_era: str,
    target_driver_key: str,
    target_team_key: str,
    as_of_year: int,
    min_track_races: int = 2,
    min_team_races: int = 3,
    min_driver_races: int = 3,
) -> PacePrediction:
    """Predict absolute target pace from earlier history only."""
    rows = _history(observations, as_of_year, target_era)
    track_rows = [r for r in rows if r.track_id == target_track_id]
    team_rows = [r for r in rows if r.team_key == target_team_key]
    driver_rows = [r for r in team_rows if r.driver_key == target_driver_key]
    warnings: list[str] = []
    if len(track_rows) < min_track_races:
        warnings.append(f"Low track/era history: n={len(track_rows)}")
    if len(team_rows) < min_team_races:
        warnings.append(f"Low team/era history: n={len(team_rows)}")
    if len(driver_rows) < min_driver_races:
        warnings.append(f"Low driver/team history: n={len(driver_rows)}")
    if not track_rows:
        raise ValueError("No historical track/era pace observations available")

    track_reference = median(r.track_reference_seconds for r in track_rows)
    team_effect = median(r.relative_gap_seconds for r in team_rows) if team_rows else 0.0
    team_reference = median(r.relative_gap_seconds for r in team_rows) if team_rows else 0.0
    driver_adjustment = median(r.relative_gap_seconds for r in driver_rows) - team_reference if driver_rows else 0.0
    prediction = track_reference + team_effect + driver_adjustment
    historical_absolute = [r.track_reference_seconds + r.relative_gap_seconds for r in track_rows]
    sigma = _robust_sigma([v - prediction for v in historical_absolute])

    return PacePrediction(
        mean_seconds=prediction,
        std_seconds=sigma,
        track_reference_seconds=track_reference,
        team_effect_seconds=team_effect,
        driver_adjustment_seconds=driver_adjustment,
        team_history_n=len(team_rows),
        driver_history_n=len(driver_rows),
        warnings=tuple(warnings),
    )


def walk_forward_validate(observations: Iterable[PaceResidualObservation], *, min_train_races: int = 3) -> dict[str, float | int]:
    """Evaluate one-step-ahead absolute pace without future leakage."""
    rows = list(observations)
    years = sorted({r.season_year for r in rows})
    errors: list[float] = []
    attempted = 0
    scored = 0
    for year in years:
        history = [r for r in rows if r.season_year < year]
        if len({r.season_year for r in history}) < min_train_races:
            continue
        for target in (r for r in rows if r.season_year == year):
            attempted += 1
            track_rows = [r for r in history if r.track_id == target.track_id and r.regulation_era == target.regulation_era]
            if not track_rows:
                continue
            team_rows = [r for r in history if r.team_key == target.team_key and r.regulation_era == target.regulation_era]
            driver_rows = [r for r in team_rows if r.driver_key == target.driver_key]
            track_ref = median(r.track_reference_seconds for r in track_rows)
            team_effect = median(r.relative_gap_seconds for r in team_rows) if team_rows else 0.0
            team_ref = median(r.relative_gap_seconds for r in team_rows) if team_rows else 0.0
            driver_adj = median(r.relative_gap_seconds for r in driver_rows) - team_ref if driver_rows else 0.0
            prediction = track_ref + team_effect + driver_adj
            actual = target.track_reference_seconds + target.relative_gap_seconds
            errors.append(abs(actual - prediction))
            scored += 1
    return {
        "years_evaluated": len(years),
        "predictions_attempted": attempted,
        "predictions_scored": scored,
        "coverage_rate": scored / attempted if attempted else 0.0,
        "mae_seconds": sum(errors) / len(errors) if errors else float("nan"),
    }
