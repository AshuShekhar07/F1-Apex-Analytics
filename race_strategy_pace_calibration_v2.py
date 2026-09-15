"""Hierarchical, leakage-safe race pace calibration v2.

The target signal is a driver/team pace residual measured against the competitive
reference of the same race. Calibration is chronological: only observations
strictly before the prediction date are eligible for a target estimate.

This is a calibration layer, not a final claim of causal car pace. Team effects
represent the historical pace signal available in the stored timing data.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from statistics import median
from typing import Iterable

from race_strategy_simulator_v1 import Distribution
from race_strategy_pace_normalization_v1 import PaceObservation, NormalizedPaceObservation, normalize_race_pace


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


def build_residual_observations(
    observations: Iterable[EnrichedPaceObservation],
) -> tuple[PaceResidualObservation, ...]:
    """Normalize laps race-by-race and retain team identity for hierarchical fitting."""
    rows = tuple(observations)
    normalized = normalize_race_pace(rows)
    by_key = {(row.race_id, row.driver_key): row for row in normalized}

    residuals: list[PaceResidualObservation] = []
    for row in rows:
        normalized_row = by_key.get((row.race_id, row.driver_key))
        if normalized_row is None:
            continue
        residuals.append(
            PaceResidualObservation(
                race_id=normalized_row.race_id,
                track_id=normalized_row.track_id,
                season_year=normalized_row.season_year,
                regulation_era=normalized_row.regulation_era,
                driver_key=normalized_row.driver_key,
                team_key=str(row.team_key),
                relative_gap_seconds=float(normalized_row.relative_gap_seconds),
            )
        )
    return tuple(residuals)


def _history(rows: Iterable[PaceResidualObservation], *, as_of_year: int, era: str) -> list[PaceResidualObservation]:
    return [
        row for row in rows
        if row.season_year < as_of_year
        and row.regulation_era == era
        and isfinite(row.relative_gap_seconds)
    ]


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
    """Estimate target race pace using strictly historical track/team/driver evidence."""
    rows = _history(observations, as_of_year=as_of_year, era=target_era)
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

    if track_rows:
        track_reference = median(r.relative_gap_seconds for r in track_rows)
    else:
        # Without a historical track reference there is no defensible absolute pace.
        raise ValueError("No historical track/era pace observations available")

    team_effect = median(r.relative_gap_seconds for r in team_rows) - median(r.relative_gap_seconds for r in track_rows) if team_rows else 0.0
    driver_adjustment = median(r.relative_gap_seconds for r in driver_rows) - median(r.relative_gap_seconds for r in team_rows) if driver_rows else 0.0

    residuals = [
        r.relative_gap_seconds - (track_reference + team_effect + driver_adjustment)
        for r in track_rows
    ]
    centre = median(residuals) if residuals else 0.0
    deviations = sorted(abs(v - centre) for v in residuals)
    mad = median(deviations) if deviations else 0.0
    sigma = max(0.05, 1.4826 * mad)

    # ``relative_gap_seconds`` is a gap-to-reference signal, so the final pace
    # still requires a target-track reference lap time supplied separately.
    return PacePrediction(
        mean_seconds=track_reference + team_effect + driver_adjustment,
        std_seconds=sigma,
        track_reference_seconds=track_reference,
        team_effect_seconds=team_effect,
        driver_adjustment_seconds=driver_adjustment,
        team_history_n=len(team_rows),
        driver_history_n=len(driver_rows),
        warnings=tuple(warnings),
    )


def walk_forward_validate(
    observations: Iterable[PaceResidualObservation],
    *,
    min_train_races: int = 3,
) -> dict[str, float | int]:
    """Evaluate one-step-ahead team/driver residual predictions by race year.

    For each race year, the estimator can only use earlier years. The target is
    the realized normalized residual, not finishing position.
    """
    rows = list(observations)
    years = sorted({r.season_year for r in rows})
    actual: list[float] = []
    errors: list[float] = []
    attempted = 0

    for year in years:
        history = [r for r in rows if r.season_year < year]
        if len({r.season_year for r in history}) < min_train_races:
            continue
        for target in [r for r in rows if r.season_year == year]:
            attempted += 1
            same_track = [
                r for r in history
                if r.track_id == target.track_id and r.regulation_era == target.regulation_era
            ]
            same_team = [
                r for r in history
                if r.team_key == target.team_key and r.regulation_era == target.regulation_era
            ]
            same_driver_team = [
                r for r in same_team if r.driver_key == target.driver_key
            ]
            if not same_track:
                continue
            track_ref = median(r.relative_gap_seconds for r in same_track)
            team_effect = median(r.relative_gap_seconds for r in same_team) - track_ref if same_team else 0.0
            team_ref = median(r.relative_gap_seconds for r in same_team) if same_team else track_ref
            driver_adjustment = median(r.relative_gap_seconds for r in same_driver_team) - team_ref if same_driver_team else 0.0
            prediction = track_ref + team_effect + driver_adjustment
            actual.append(target.relative_gap_seconds)
            errors.append(abs(target.relative_gap_seconds - prediction))

    mae = sum(errors) / len(errors) if errors else float("nan")
    return {
        "years_evaluated": len(years),
        "predictions_attempted": attempted,
        "predictions_scored": len(errors),
        "mae_seconds": mae,
    }
