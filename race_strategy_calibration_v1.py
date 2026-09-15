"""Data-backed calibration primitives for race_strategy_simulator_v1.

The simulator should consume calibrated distributions, not hard-coded F1 priors.
This module deliberately accepts plain Python observations so DB/CSV adapters can
remain separate and historical backtests can pass leakage-safe snapshots.

No claim is made that these estimators are production-calibrated until real data
is supplied and walk-forward validation is run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import sqrt
from statistics import median
from typing import Iterable, Mapping, Sequence

from race_strategy_simulator_v1 import Distribution, SimulationParameters


@dataclass(frozen=True)
class CalibrationConfig:
    prior_event_alpha: float = 1.0
    prior_event_beta: float = 99.0
    min_distribution_std: float = 0.05
    robust_clip_z: float = 3.5
    tyre_min_observations: int = 20
    event_min_laps: int = 100


@dataclass(frozen=True)
class TyreCalibrationObservation:
    compound: str
    tyre_age_laps: int
    lap_time_delta_seconds: float
    wet_state: str = "dry"


@dataclass(frozen=True)
class EventCalibrationObservation:
    total_laps: int
    safety_car_count: int = 0
    vsc_count: int = 0
    red_flag_count: int = 0
    wet_laps: int = 0


@dataclass(frozen=True)
class PitCalibrationObservation:
    service_seconds: float
    pit_lane_loss_seconds: float


@dataclass(frozen=True)
class PaceCalibrationObservation:
    normalized_lap_seconds: float


@dataclass(frozen=True)
class CalibrationReport:
    observations: dict[str, int]
    warnings: tuple[str, ...] = ()
    model_version: str = "race-strategy-calibration-v1"


@dataclass(frozen=True)
class CalibratedInputs:
    tyre_degradation_per_lap: dict[str, Distribution]
    pit_stop_seconds: Distribution
    pit_lane_loss_seconds: Distribution
    our_base_pace_seconds: Distribution | None
    sc_probability_per_lap_dry: float
    vsc_probability_per_lap_dry: float
    red_flag_probability_per_lap_dry: float
    sc_probability_per_lap_wet: float
    vsc_probability_per_lap_wet: float
    red_flag_probability_per_lap_wet: float
    report: CalibrationReport

    def to_simulation_parameters(self, *, weather_onset_lap=None, weather_duration_laps=None,
                                  rain_intensity_mm_h=None, track_temp_c=None) -> SimulationParameters:
        """Convert calibrated pieces into simulator parameters without inventing weather inputs."""
        kwargs = {}
        if weather_onset_lap is not None:
            kwargs["weather_onset_lap"] = weather_onset_lap
        if weather_duration_laps is not None:
            kwargs["weather_duration_laps"] = weather_duration_laps
        if rain_intensity_mm_h is not None:
            kwargs["rain_intensity_mm_h"] = rain_intensity_mm_h
        if track_temp_c is not None:
            kwargs["track_temp_c"] = track_temp_c
        return SimulationParameters(**kwargs)


def _clean(values: Iterable[float]) -> list[float]:
    return [float(v) for v in values if v is not None]


def robust_distribution(
    values: Iterable[float],
    *,
    lower: float | None = None,
    upper: float | None = None,
    min_std: float = 0.05,
    clip_z: float = 3.5,
) -> Distribution:
    """Fit a conservative normal approximation with MAD-based outlier clipping."""
    clean = _clean(values)
    if not clean:
        raise ValueError("At least one finite observation is required")

    centre = median(clean)
    deviations = [abs(v - centre) for v in clean]
    mad = median(deviations)
    robust_sigma = 1.4826 * mad

    if robust_sigma > 0:
        cutoff = clip_z * robust_sigma
        clipped = [v for v in clean if abs(v - centre) <= cutoff]
    else:
        clipped = clean

    if not clipped:
        clipped = [centre]

    mean_value = sum(clipped) / len(clipped)
    if len(clipped) > 1:
        variance = sum((v - mean_value) ** 2 for v in clipped) / (len(clipped) - 1)
        std = max(min_std, sqrt(max(0.0, variance)))
    else:
        std = min_std

    return Distribution(
        mean=mean_value,
        std=std,
        lower=lower,
        upper=upper,
    )


def _fit_nonnegative_slope(x: Sequence[float], y: Sequence[float]) -> float:
    """Fit y ~= intercept + slope*x with a non-negative OLS slope."""
    if len(x) != len(y) or not x:
        raise ValueError("x and y must have equal non-zero length")
    mean_x = sum(x) / len(x)
    mean_y = sum(y) / len(y)
    denom = sum((value - mean_x) ** 2 for value in x)
    if denom <= 1e-12:
        return 0.0
    numer = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
    return max(0.0, numer / denom)


def calibrate_tyre_degradation(
    observations: Iterable[TyreCalibrationObservation],
    *,
    config: CalibrationConfig | None = None,
) -> tuple[dict[str, Distribution], tuple[str, ...]]:
    """Estimate per-lap degradation from age-vs-time-delta observations.

    The observation is expected to already be normalized for fuel, traffic,
    compound baseline, weather, and other confounders by the upstream adapter.
    """
    config = config or CalibrationConfig()
    grouped: dict[str, list[TyreCalibrationObservation]] = {}
    for obs in observations:
        compound = obs.compound.upper()
        if obs.tyre_age_laps < 0:
            continue
        grouped.setdefault(compound, []).append(obs)

    results: dict[str, Distribution] = {}
    warnings: list[str] = []
    for compound, rows in grouped.items():
        if len(rows) < config.tyre_min_observations:
            warnings.append(f"Low tyre-degradation sample for {compound}: n={len(rows)}")

        slopes: list[float] = []
        # Robustly estimate slopes on sequential age pairs rather than letting
        # one extreme long-run determine the whole compound coefficient.
        ordered = sorted(rows, key=lambda r: (r.tyre_age_laps, r.lap_time_delta_seconds))
        for left, right in zip(ordered, ordered[1:]):
            age_delta = right.tyre_age_laps - left.tyre_age_laps
            if age_delta <= 0:
                continue
            slopes.append(max(0.0, (right.lap_time_delta_seconds - left.lap_time_delta_seconds) / age_delta))

        if slopes:
            dist = robust_distribution(slopes, lower=0.0, upper=0.5, min_std=0.005, clip_z=config.robust_clip_z)
        else:
            slope = _fit_nonnegative_slope(
                [float(r.tyre_age_laps) for r in rows],
                [float(r.lap_time_delta_seconds) for r in rows],
            )
            dist = Distribution(mean=min(0.5, slope), std=0.02, lower=0.0, upper=0.5)
            warnings.append(f"Sparse age variation for {compound}; used fallback slope")
        results[compound] = dist

    return results, tuple(warnings)


def smoothed_event_probability(event_count: int, exposure_laps: int, *, alpha: float = 1.0, beta: float = 99.0) -> float:
    """Posterior mean of a Bernoulli hazard with a transparent Beta prior."""
    if event_count < 0 or exposure_laps < 0 or event_count > exposure_laps:
        raise ValueError("Invalid event count/exposure")
    if alpha <= 0 or beta <= 0:
        raise ValueError("Beta prior parameters must be positive")
    return (alpha + event_count) / (alpha + beta + exposure_laps)


def calibrate_event_hazards(
    observations: Iterable[EventCalibrationObservation],
    *,
    config: CalibrationConfig | None = None,
) -> tuple[dict[str, float], tuple[str, ...]]:
    """Estimate dry and wet per-lap SC/VSC/red-flag hazards with smoothing."""
    config = config or CalibrationConfig()
    rows = [row for row in observations if row.total_laps > 0]
    if not rows:
        raise ValueError("At least one event observation is required")

    dry_laps = sum(max(0, r.total_laps - r.wet_laps) for r in rows)
    wet_laps = sum(min(r.total_laps, max(0, r.wet_laps)) for r in rows)
    counts = {
        "sc_dry": sum(r.safety_car_count for r in rows if r.wet_laps < r.total_laps),
        "vsc_dry": sum(r.vsc_count for r in rows if r.wet_laps < r.total_laps),
        "red_dry": sum(r.red_flag_count for r in rows if r.wet_laps < r.total_laps),
        "sc_wet": sum(r.safety_car_count for r in rows if r.wet_laps > 0),
        "vsc_wet": sum(r.vsc_count for r in rows if r.wet_laps > 0),
        "red_wet": sum(r.red_flag_count for r in rows if r.wet_laps > 0),
    }

    if dry_laps < config.event_min_laps:
        dry_warning = f"Low dry-lap event exposure: n_laps={dry_laps}"
    else:
        dry_warning = None
    if wet_laps < config.event_min_laps:
        wet_warning = f"Low wet-lap event exposure: n_laps={wet_laps}"
    else:
        wet_warning = None

    exposure = {
        "sc_probability_per_lap_dry": dry_laps,
        "vsc_probability_per_lap_dry": dry_laps,
        "red_flag_probability_per_lap_dry": dry_laps,
        "sc_probability_per_lap_wet": wet_laps,
        "vsc_probability_per_lap_wet": wet_laps,
        "red_flag_probability_per_lap_wet": wet_laps,
    }
    result = {
        "sc_probability_per_lap_dry": smoothed_event_probability(counts["sc_dry"], exposure["sc_probability_per_lap_dry"], alpha=config.prior_event_alpha, beta=config.prior_event_beta),
        "vsc_probability_per_lap_dry": smoothed_event_probability(counts["vsc_dry"], exposure["vsc_probability_per_lap_dry"], alpha=config.prior_event_alpha, beta=config.prior_event_beta),
        "red_flag_probability_per_lap_dry": smoothed_event_probability(counts["red_dry"], exposure["red_probability_per_lap_dry"], alpha=config.prior_event_alpha, beta=config.prior_event_beta) if "red_probability_per_lap_dry" in exposure else smoothed_event_probability(counts["red_dry"], dry_laps, alpha=config.prior_event_alpha, beta=config.prior_event_beta),
        "sc_probability_per_lap_wet": smoothed_event_probability(counts["sc_wet"], exposure["sc_probability_per_lap_wet"], alpha=config.prior_event_alpha, beta=config.prior_event_beta),
        "vsc_probability_per_lap_wet": smoothed_event_probability(counts["vsc_wet"], exposure["vsc_probability_per_lap_wet"], alpha=config.prior_event_alpha, beta=config.prior_event_beta),
        "red_flag_probability_per_lap_wet": smoothed_event_probability(counts["red_wet"], exposure["red_flag_probability_per_lap_wet"], alpha=config.prior_event_alpha, beta=config.prior_event_beta),
    }

    warnings = tuple(w for w in (dry_warning, wet_warning) if w)
    return result, warnings


def calibrate_pit_stops(
    observations: Iterable[PitCalibrationObservation],
    *,
    config: CalibrationConfig | None = None,
) -> tuple[Distribution, Distribution, tuple[str, ...]]:
    config = config or CalibrationConfig()
    rows = list(observations)
    if not rows:
        raise ValueError("At least one pit-stop observation is required")

    service = robust_distribution(
        (r.service_seconds for r in rows),
        lower=1.5,
        upper=5.5,
        min_std=0.05,
        clip_z=config.robust_clip_z,
    )
    lane = robust_distribution(
        (r.pit_lane_loss_seconds for r in rows),
        lower=10.0,
        upper=40.0,
        min_std=0.20,
        clip_z=config.robust_clip_z,
    )
    warnings = (f"Low pit-stop sample: n={len(rows)}",) if len(rows) < 20 else ()
    return service, lane, warnings


def calibrate_pace(
    observations: Iterable[PaceCalibrationObservation],
    *,
    config: CalibrationConfig | None = None,
) -> tuple[Distribution, tuple[str, ...]]:
    config = config or CalibrationConfig()
    rows = list(observations)
    if not rows:
        raise ValueError("At least one pace observation is required")
    dist = robust_distribution(
        (r.normalized_lap_seconds for r in rows),
        lower=40.0,
        upper=150.0,
        min_std=max(config.min_distribution_std, 0.02),
        clip_z=config.robust_clip_z,
    )
    warnings = (f"Low pace sample: n={len(rows)}",) if len(rows) < 30 else ()
    return dist, warnings


def calibrate_inputs(
    *,
    tyre_observations: Iterable[TyreCalibrationObservation],
    event_observations: Iterable[EventCalibrationObservation],
    pit_observations: Iterable[PitCalibrationObservation],
    pace_observations: Iterable[PaceCalibrationObservation] = (),
    config: CalibrationConfig | None = None,
) -> CalibratedInputs:
    """Fit all currently supported simulator inputs and keep a data-quality report."""
    config = config or CalibrationConfig()
    tyre_rows = list(tyre_observations)
    event_rows = list(event_observations)
    pit_rows = list(pit_observations)
    pace_rows = list(pace_observations)

    tyre, tyre_warnings = calibrate_tyre_degradation(tyre_rows, config=config)
    hazards, event_warnings = calibrate_event_hazards(event_rows, config=config)
    pit_service, pit_lane, pit_warnings = calibrate_pit_stops(pit_rows, config=config)

    pace = None
    pace_warnings: tuple[str, ...] = ()
    if pace_rows:
        pace, pace_warnings = calibrate_pace(pace_rows, config=config)

    report = CalibrationReport(
        observations={
            "tyre": len(tyre_rows),
            "events": len(event_rows),
            "pit_stops": len(pit_rows),
            "pace": len(pace_rows),
        },
        warnings=tuple(tyre_warnings + event_warnings + pit_warnings + pace_warnings),
    )
    return CalibratedInputs(
        tyre_degradation_per_lap=tyre,
        pit_stop_seconds=pit_service,
        pit_lane_loss_seconds=pit_lane,
        our_base_pace_seconds=pace,
        sc_probability_per_lap_dry=hazards["sc_probability_per_lap_dry"],
        vsc_probability_per_lap_dry=hazards["vsc_probability_per_lap_dry"],
        red_flag_probability_per_lap_dry=hazards["red_flag_probability_per_lap_dry"],
        sc_probability_per_lap_wet=hazards["sc_probability_per_lap_wet"],
        vsc_probability_per_lap_wet=hazards["vsc_probability_per_lap_wet"],
        red_flag_probability_per_lap_wet=hazards["red_flag_probability_per_lap_wet"],
        report=report,
    )
