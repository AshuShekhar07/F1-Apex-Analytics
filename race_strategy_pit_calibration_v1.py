"""Calibrate total pit-lane loss from observed FastF1 race stops.

FastF1's PitInTime -> PitOutTime interval is a total pit-lane elapsed time.
It is not a separately measured crew-service duration. This module therefore
calibrates that observable directly instead of fabricating a service-time split.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from race_strategy_pit_db_adapter_v1 import StoredPitLaneObservation, load_pit_lane_observations
from race_strategy_simulator_v1 import Distribution, RaceContext
from race_strategy_calibration_v1 import robust_distribution


@dataclass(frozen=True)
class PitLaneCalibrationResult:
    total_pit_lane_seconds: Distribution
    observations: int
    warnings: tuple[str, ...] = ()


def calibrate_total_pit_lane_loss(
    observations: Iterable[StoredPitLaneObservation],
    *,
    min_observations: int = 20,
) -> PitLaneCalibrationResult:
    """Fit a robust total pit-lane-time distribution from real observations."""
    rows = list(observations)
    values = [
        float(row.total_pit_lane_seconds)
        for row in rows
        if 10.0 <= float(row.total_pit_lane_seconds) <= 60.0
    ]
    if not values:
        raise ValueError("At least one valid total pit-lane observation is required")

    dist = robust_distribution(
        values,
        lower=10.0,
        upper=60.0,
        min_std=0.20,
        clip_z=3.5,
    )
    warnings: list[str] = []
    if len(values) < min_observations:
        warnings.append(f"Low total pit-lane sample: n={len(values)}")
    warnings.append(
        "Total pit-lane time is used directly; crew stationary/service time is not separately identified"
    )
    return PitLaneCalibrationResult(dist, len(values), tuple(warnings))


def context_with_total_pit_lane_calibration(
    context: RaceContext,
    calibrated: PitLaneCalibrationResult,
) -> RaceContext:
    """Return a simulator context that uses the observed total pit-lane loss.

    The existing simulator adds ``pit_stop_seconds`` and ``pit_lane_loss_seconds``
    together. To avoid double-counting or inventing service time, this bridge
    represents the observed total as the lane component and sets service time to
    zero only for this explicitly marked context.
    """
    return RaceContext(
        total_laps=context.total_laps,
        starting_grid=context.starting_grid,
        our_base_pace_seconds=context.our_base_pace_seconds,
        tyre_degradation_per_lap=context.tyre_degradation_per_lap,
        pit_stop_seconds=Distribution(0.0, 0.0, 0.0, 0.0),
        pit_lane_loss_seconds=calibrated.total_pit_lane_seconds,
        sc_probability_per_lap=context.sc_probability_per_lap,
        vsc_probability_per_lap=context.vsc_probability_per_lap,
        red_flag_probability_per_lap=context.red_flag_probability_per_lap,
        track_position_seconds_per_place=context.track_position_seconds_per_place,
    )


def calibrate_total_pit_lane_from_db(
    db,
    *,
    start_year: int = 2018,
    end_year: int = 2026,
    era: str | None = None,
    min_observations: int = 20,
) -> PitLaneCalibrationResult:
    """Load stored FastF1 stops and return the calibrated simulator input."""
    observations, warnings = load_pit_lane_observations(
        db,
        start_year=start_year,
        end_year=end_year,
        era=era,
    )
    result = calibrate_total_pit_lane_loss(observations, min_observations=min_observations)
    return PitLaneCalibrationResult(
        total_pit_lane_seconds=result.total_pit_lane_seconds,
        observations=result.observations,
        warnings=tuple(warnings) + result.warnings,
    )
