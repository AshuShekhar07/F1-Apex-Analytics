"""FP2 long-run compound pace calibration audit v1.

Research-only. Uses same-session, same-driver FP2 long runs to compare SOFT/HARD
against MEDIUM. Compound differences are reported only when there is overlap in
tyre age and the comparison laps are reasonably close in session time. No
simulator integration is performed.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from statistics import median
from typing import Iterable


DRY_COMPOUNDS = ("SOFT", "MEDIUM", "HARD")


@dataclass(frozen=True)
class FP2CompoundObservation:
    session_id: int
    race_id: int
    season_year: int
    regulation_era: str
    driver_key: str
    lap_number: int
    stint_age: int
    compound: str
    lap_time_seconds: float


@dataclass(frozen=True)
class FP2CompoundAuditResult:
    offsets_seconds: dict[str, float]
    group_counts: dict[str, int]
    matched_pairs: dict[str, int]
    observations: int
    warnings: tuple[str, ...]


def build_fp2_stints(rows: Iterable[FP2CompoundObservation], *, min_stint_laps: int = 5) -> list[FP2CompoundObservation]:
    """Keep only dry-like long runs, dropping the first lap of each compound run."""
    grouped: dict[tuple[int, str], list[FP2CompoundObservation]] = {}
    for row in rows:
        if (
            row.compound.upper() in DRY_COMPOUNDS
            and row.lap_number > 0
            and 1 <= row.stint_age
            and isfinite(float(row.lap_time_seconds))
            and 40.0 <= float(row.lap_time_seconds) <= 180.0
        ):
            grouped.setdefault((row.session_id, row.driver_key), []).append(row)

    result: list[FP2CompoundObservation] = []
    for _, values in grouped.items():
        ordered = sorted(values, key=lambda r: r.lap_number)
        current: list[FP2CompoundObservation] = []
        previous_compound: str | None = None
        for row in ordered:
            if previous_compound is not None and row.compound != previous_compound:
                if len(current) >= min_stint_laps:
                    result.extend(current[1:])
                current = []
            current.append(row)
            previous_compound = row.compound
        if len(current) >= min_stint_laps:
            result.extend(current[1:])

    return result


def calibrate_fp2_compound_pace(
    rows: Iterable[FP2CompoundObservation],
    *,
    min_pairs_per_group: int = 2,
    max_lap_distance: int = 15,
) -> FP2CompoundAuditResult:
    """Estimate robust SOFT/HARD offsets relative to MEDIUM."""
    if max_lap_distance < 0:
        raise ValueError("max_lap_distance must be non-negative")

    clean = build_fp2_stints(rows)
    groups: dict[tuple[int, int, str], list[FP2CompoundObservation]] = {}
    for row in clean:
        groups.setdefault((row.session_id, row.race_id, row.driver_key), []).append(row)

    deltas: dict[str, dict[tuple[int, int, str], list[float]]] = {"SOFT": {}, "HARD": {}}
    for key, values in groups.items():
        med = [r for r in values if r.compound == "MEDIUM"]
        if not med:
            continue
        for compound in ("SOFT", "HARD"):
            targets = [r for r in values if r.compound == compound]
            for target in targets:
                candidates = [
                    ref for ref in med
                    if ref.stint_age == target.stint_age
                    and abs(ref.lap_number - target.lap_number) <= max_lap_distance
                ]
                if not candidates:
                    continue
                ref = min(candidates, key=lambda r: (abs(r.lap_number - target.lap_number), r.lap_number))
                deltas[compound].setdefault(key, []).append(
                    target.lap_time_seconds - ref.lap_time_seconds
                )

    group_medians: dict[str, list[float]] = {"SOFT": [], "HARD": []}
    matched_pairs = {"SOFT": 0, "HARD": 0}
    group_counts = {"SOFT": 0, "HARD": 0}
    for compound in ("SOFT", "HARD"):
        for values in deltas[compound].values():
            if len(values) >= min_pairs_per_group:
                group_counts[compound] += 1
                matched_pairs[compound] += len(values)
                group_medians[compound].append(median(values))

    warnings: list[str] = []
    if not group_medians["SOFT"]:
        warnings.append("No usable SOFT-vs-MEDIUM FP2 comparison groups")
    if not group_medians["HARD"]:
        warnings.append("No usable HARD-vs-MEDIUM FP2 comparison groups")
    if len(clean) < 1000:
        warnings.append(f"Low FP2 long-run observation sample: n={len(clean)}")

    offsets = {
        "SOFT": median(group_medians["SOFT"]) if group_medians["SOFT"] else 0.0,
        "MEDIUM": 0.0,
        "HARD": median(group_medians["HARD"]) if group_medians["HARD"] else 0.0,
    }
    if abs(offsets["SOFT"]) > 1.5 or abs(offsets["HARD"]) > 1.5:
        warnings.append("Compound offset exceeds 1.5s; treat calibration as unidentified/confounded")

    warnings.append(
        "FP2 compound offsets are research estimates; session fuel load and track evolution can still confound results"
    )
    return FP2CompoundAuditResult(
        offsets_seconds=offsets,
        group_counts=group_counts,
        matched_pairs=matched_pairs,
        observations=len(clean),
        warnings=tuple(warnings),
    )
