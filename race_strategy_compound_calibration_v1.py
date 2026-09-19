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
    """Estimate compound offsets from within race-driver comparisons to MEDIUM.

    A race-driver group contributes only when MEDIUM and another dry compound
    are both observed in the early-stint window. For each compound pair we use
    the median lap time and median race-lap number within that early window,
    then estimate:
        time_delta = compound_offset + fuel_slope * lap_number_delta + error

    This avoids the previous ill-conditioned fixed-effect regression, where
    groups containing only one compound could not identify a compound effect.
    """
    rows = _valid_rows(observations, min_age, max_age)
    groups: dict[tuple[int, str], list[CompoundPaceObservation]] = {}
    for row in rows:
        groups.setdefault((row.race_id, row.driver_key), []).append(row)

    comparison_rows: list[tuple[str, float, float]] = []
    comparison_groups: set[tuple[int, str]] = set()
    for key, values in groups.items():
        by_compound: dict[str, list[CompoundPaceObservation]] = {}
        for row in values:
            by_compound.setdefault(row.compound, []).append(row)
        medium = by_compound.get("MEDIUM")
        if not medium or len(medium) < min_group_observations:
            continue

        medium_time = median(v.lap_time_seconds for v in medium)
        medium_lap = median(v.lap_number for v in medium)

        for compound in ("SOFT", "HARD"):
            other = by_compound.get(compound)
            if not other or len(other) < min_group_observations:
                continue
            other_time = median(v.lap_time_seconds for v in other)
            other_lap = median(v.lap_number for v in other)
            comparison_rows.append(
                (
                    compound,
                    float(other_time - medium_time),
                    float(other_lap - medium_lap),
                )
            )
            comparison_groups.add(key)

    if not comparison_rows:
        raise ValueError(
            "No usable race-driver compound comparisons against MEDIUM were found"
        )

    # OLS with no intercept: MEDIUM is the zero reference. The lap-number delta
    # absorbs the coarse fuel/load progression caused by compounds being run at
    # different race positions.
    xtx = [[0.0] * 3 for _ in range(3)]
    xty = [0.0] * 3
    for compound, y, lap_delta in comparison_rows:
        x = [
            float(compound == "SOFT"),
            float(compound == "HARD"),
            float(lap_delta),
        ]
        for i in range(3):
            xty[i] += x[i] * y
            for j in range(3):
                xtx[i][j] += x[i] * x[j]
    for i in range(3):
        xtx[i][i] += 1e-9

    beta = _solve_3x3(xtx, xty)
    soft_delta, hard_delta, fuel_slope = beta

    residuals = []
    for compound, y, lap_delta in comparison_rows:
        predicted = (
            soft_delta * float(compound == "SOFT")
            + hard_delta * float(compound == "HARD")
            + fuel_slope * lap_delta
        )
        residuals.append(y - predicted)

    residual_scale = max(
        0.005,
        1.4826 * median(abs(v - median(residuals)) for v in residuals),
    )

    warnings: list[str] = []
    if len(comparison_groups) < 50:
        warnings.append(
            f"Low race-driver comparison-group sample: n={len(comparison_groups)}"
        )
    if abs(soft_delta) > 1.5 or abs(hard_delta) > 1.5:
        warnings.append(
            "Estimated compound offset is unusually large; inspect compound-selection and lap-position confounding"
        )
    if abs(fuel_slope) > 0.2:
        warnings.append(
            "Estimated race-lap progression slope is unusually large; inspect fuel/load confounding"
        )
    warnings.append(
        "Offsets are relative to MEDIUM and estimated from early stint laps in "
        "within race-driver comparisons; research calibration only"
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
        groups=len(comparison_groups),
        warnings=tuple(warnings),
    )

