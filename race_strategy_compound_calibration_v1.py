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
    max_lap_distance: int = 3,
) -> CompoundPaceCalibration:
    """Estimate compound offsets using matched same-age, same-race-driver laps.

    Each SOFT/HARD observation is matched to the nearest MEDIUM observation for
    the same race, driver and tyre age. The matched race-lap numbers must be
    within ``max_lap_distance``. We then take a median delta per
    race-driver-compound group and a robust median across groups.

    Matching on tyre age removes most age confounding, while matching close in
    race lap limits fuel-load / track-evolution differences. Groups without
    enough matched pairs are excluded instead of forcing an unstable regression.
    """
    if max_lap_distance < 0:
        raise ValueError("max_lap_distance must be non-negative")

    rows = _valid_rows(observations, min_age, max_age)
    groups: dict[tuple[int, str], list[CompoundPaceObservation]] = {}
    for row in rows:
        groups.setdefault((row.race_id, row.driver_key), []).append(row)

    comparison_groups: dict[tuple[int, str, str], list[float]] = {}
    for key, values in groups.items():
        medium = [v for v in values if v.compound == "MEDIUM"]
        if len(medium) < min_group_observations:
            continue
        for compound in ("SOFT", "HARD"):
            targets = [v for v in values if v.compound == compound]
            if len(targets) < min_group_observations:
                continue
            for target in targets:
                candidates = [
                    ref for ref in medium
                    if ref.tyre_age_laps == target.tyre_age_laps
                    and abs(ref.lap_number - target.lap_number) <= max_lap_distance
                ]
                if not candidates:
                    continue
                reference = min(
                    candidates,
                    key=lambda ref: (
                        abs(ref.lap_number - target.lap_number),
                        ref.lap_number,
                    ),
                )
                comparison_groups.setdefault((*key, compound), []).append(
                    float(target.lap_time_seconds - reference.lap_time_seconds)
                )

    usable = {
        key: deltas
        for key, deltas in comparison_groups.items()
        if len(deltas) >= min_group_observations
    }
    if not usable:
        raise ValueError(
            "No usable same-age, near-lap compound comparisons against MEDIUM were found"
        )

    group_medians = {key: median(values) for key, values in usable.items()}
    soft_values = [v for (*_, compound), v in group_medians.items() if compound == "SOFT"]
    hard_values = [v for (*_, compound), v in group_medians.items() if compound == "HARD"]
    if not soft_values and not hard_values:
        raise ValueError("No usable SOFT/HARD compound comparison groups were found")

    soft_delta = median(soft_values) if soft_values else 0.0
    hard_delta = median(hard_values) if hard_values else 0.0

    all_deltas = soft_values + hard_values
    residual_scale = max(
        0.005,
        1.4826 * median(abs(v - median(all_deltas)) for v in all_deltas),
    )

    warnings: list[str] = []
    if not soft_values:
        warnings.append("No usable SOFT-vs-MEDIUM groups were found")
    if not hard_values:
        warnings.append("No usable HARD-vs-MEDIUM groups were found")
    if len(usable) < 50:
        warnings.append(f"Low matched race-driver compound-group sample: n={len(usable)}")
    if abs(soft_delta) > 1.5 or abs(hard_delta) > 1.5:
        warnings.append("Estimated compound offset is unusually large; inspect matching and selection confounding")
    warnings.append(
        "Offsets are relative to MEDIUM and use same-age, near-lap matched dry-race observations; research calibration only"
    )

    return CompoundPaceCalibration(
        reference_compound="MEDIUM",
        offsets_seconds={"SOFT": float(soft_delta), "MEDIUM": 0.0, "HARD": float(hard_delta)},
        offset_std_seconds={"SOFT": float(residual_scale), "MEDIUM": 0.0, "HARD": float(residual_scale)},
        observations=len(rows),
        groups=len(usable),
        warnings=tuple(warnings),
    )
