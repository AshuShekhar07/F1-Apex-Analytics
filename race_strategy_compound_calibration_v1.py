"""Data-backed dry-tyre compound pace calibration v1.

Estimates SOFT/HARD baseline pace offsets relative to MEDIUM using only early
stint laps. Race-driver fixed effects absorb the driver/race pace level and a
lap-number term absorbs the coarse fuel-load progression.

This module is calibration/research-only. It does not modify simulator inputs.
Negative compound offsets mean faster than MEDIUM.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from statistics import median
from typing import Iterable

from race_strategy_calibration_v1 import robust_distribution

DRY_COMPOUNDS = ("SOFT", "MEDIUM", "HARD")


@dataclass(frozen=True)
class CompoundPaceObservation:
    race_id: int
    season_year: int
    regulation_era: str
    driver_key: str
    lap_number: int
    tyre_age_laps: int
    compound: str
    lap_time_seconds: float
    is_valid: bool = True


@dataclass(frozen=True)
class CompoundPaceCalibration:
    reference_compound: str
    offsets_seconds: dict[str, float]
    offset_std_seconds: dict[str, float]
    observations: int
    groups: int
    warnings: tuple[str, ...] = ()


def _solve_3x3(matrix: list[list[float]], vector: list[float]) -> list[float]:
    aug = [row[:] + [vector[i]] for i, row in enumerate(matrix)]
    for col in range(3):
        pivot = max(range(col, 3), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot][col]) <= 1e-12:
            raise ValueError("Compound regression matrix is singular")
        aug[col], aug[pivot] = aug[pivot], aug[col]
        pivot_value = aug[col][col]
        for j in range(col, 4):
            aug[col][j] /= pivot_value
        for r in range(3):
            if r == col:
                continue
            factor = aug[r][col]
            for j in range(col, 4):
                aug[r][j] -= factor * aug[col][j]
    return [aug[i][3] for i in range(3)]


def _valid_rows(rows: Iterable[CompoundPaceObservation], min_age: int, max_age: int) -> list[CompoundPaceObservation]:
    result: list[CompoundPaceObservation] = []
    for row in rows:
        compound = row.compound.upper()
        if (
            row.is_valid
            and compound in DRY_COMPOUNDS
            and min_age <= row.tyre_age_laps <= max_age
            and row.lap_number > 0
            and isfinite(float(row.lap_time_seconds))
            and 40.0 <= float(row.lap_time_seconds) <= 180.0
        ):
            result.append(
                CompoundPaceObservation(
                    race_id=int(row.race_id),
                    season_year=int(row.season_year),
                    regulation_era=str(row.regulation_era),
                    driver_key=str(row.driver_key),
                    lap_number=int(row.lap_number),
                    tyre_age_laps=int(row.tyre_age_laps),
                    compound=compound,
                    lap_time_seconds=float(row.lap_time_seconds),
                    is_valid=True,
                )
            )
    return result


def calibrate_compound_pace(
    observations: Iterable[CompoundPaceObservation],
    *,
    min_age: int = 1,
    max_age: int = 3,
    min_group_observations: int = 3,
) -> CompoundPaceCalibration:
    """Estimate compound offsets with race-driver fixed effects and lap trend."""
    rows = _valid_rows(observations, min_age, max_age)
    groups: dict[tuple[int, str], list[CompoundPaceObservation]] = {}
    for row in rows:
        groups.setdefault((row.race_id, row.driver_key), []).append(row)

    usable: dict[tuple[int, str], list[CompoundPaceObservation]] = {
        key: values for key, values in groups.items() if len(values) >= min_group_observations
    }
    if not usable:
        raise ValueError("No usable race-driver groups for compound pace calibration")

    # Within-group demean removes the unknown race-driver pace intercept.
    x_rows: list[tuple[float, float, float]] = []
    y_rows: list[float] = []
    for values in usable.values():
        # Use arithmetic means for the exact within-group fixed-effect
        # transformation; the median would leave an implicit intercept.
        y_bar = sum(v.lap_time_seconds for v in values) / len(values)
        lap_bar = sum(v.lap_number for v in values) / len(values)
        soft_bar = sum(v.compound == "SOFT" for v in values) / len(values)
        hard_bar = sum(v.compound == "HARD" for v in values) / len(values)
        for row in values:
            x_rows.append(
                (
                    float(row.lap_number - lap_bar),
                    float((row.compound == "SOFT") - soft_bar),
                    float((row.compound == "HARD") - hard_bar),
                )
            )
            y_rows.append(float(row.lap_time_seconds - y_bar))

    xtx = [[0.0] * 3 for _ in range(3)]
    xty = [0.0] * 3
    for x, y in zip(x_rows, y_rows):
        for i in range(3):
            xty[i] += x[i] * y
            for j in range(3):
                xtx[i][j] += x[i] * x[j]
    for i in range(3):
        xtx[i][i] += 1e-9

    beta = _solve_3x3(xtx, xty)
    lap_slope, soft_delta, hard_delta = beta

    # A robust residual-derived scale is reported for uncertainty diagnostics.
    residuals = []
    for x, y in zip(x_rows, y_rows):
        residuals.append(y - (lap_slope * x[0] + soft_delta * x[1] + hard_delta * x[2]))
    residual_scale = max(0.005, 1.4826 * median(abs(v - median(residuals)) for v in residuals))

    warnings: list[str] = []
    if len(usable) < 50:
        warnings.append(f"Low race-driver group sample: n={len(usable)}")
    if abs(soft_delta) > 1.5 or abs(hard_delta) > 1.5:
        warnings.append("Estimated compound offset is unusually large; inspect age/fuel confounding")
    warnings.append(
        "Offsets are relative to MEDIUM and estimated from early stint laps; this is not yet a production-calibrated tyre model"
    )

    return CompoundPaceCalibration(
        reference_compound="MEDIUM",
        offsets_seconds={
            "SOFT": float(soft_delta),
            "MEDIUM": 0.0,
            "HARD": float(hard_delta),
        },
        offset_std_seconds={
            "SOFT": float(residual_scale),
            "MEDIUM": 0.0,
            "HARD": float(residual_scale),
        },
        observations=len(rows),
        groups=len(usable),
        warnings=tuple(warnings),
    )
