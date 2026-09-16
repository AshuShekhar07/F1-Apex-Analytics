"""Read-only pooled tyre-degradation audit (v2).

The v1 audit fitted lap-time slope separately inside each stint. That leaves
 tyre age strongly entangled with race progression. v2 instead uses cross-driver
strategy diversity and a same-race/same-lap field median to remove shared pace
 evolution before fitting tyre-age slopes pooled across races.

This module is deliberately audit-only. It does not change the production
 calibration or simulator.
"""

from __future__ import annotations

import argparse
import os
from collections import defaultdict
from dataclasses import dataclass
from math import sqrt
from statistics import median

from sqlalchemy import create_engine, text

COMPOUNDS = ("SOFT", "MEDIUM", "HARD")
DEFAULT_MIN_UNIQUE_PIT_LAPS = 4
DEFAULT_MIN_RACE_STINT_LAPS = 5
DEFAULT_MIN_STINT_AGE_SPAN = 4


@dataclass(frozen=True)
class LapRow:
    race_id: int
    season_year: int
    era: str
    driver_id: int
    stint_key: str
    compound: str
    start_lap: int
    end_lap: int
    lap_number: int
    lap_time: float


@dataclass(frozen=True)
class RegressionSummary:
    era: str
    compound: str
    slope: float | None
    intercept: float | None
    ci_low: float | None
    ci_high: float | None
    n_laps: int
    n_stints: int
    n_races: int
    negative_rate: float | None


def _ols_ci(x: list[float], y: list[float]) -> tuple[float, float, float, float] | None:
    n = len(x)
    if n < 3:
        return None
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    sxx = sum((value - mean_x) ** 2 for value in x)
    if sxx <= 1e-12:
        return None
    slope = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y)) / sxx
    intercept = mean_y - slope * mean_x
    residuals = [yi - (intercept + slope * xi) for xi, yi in zip(x, y)]
    if n <= 2:
        return slope, intercept, slope, slope
    sse = sum(value * value for value in residuals)
    se = sqrt(max(0.0, (sse / (n - 2)) / sxx))
    # Large-sample 95% normal approximation. This is an audit diagnostic,
    # not a formal clustered confidence interval.
    margin = 1.96 * se
    return slope, intercept, slope - margin, slope + margin


def _query_rows(db, start_year: int, end_year: int) -> list[dict]:
    result = db.execute(
        text(
            """
            SELECT
                r.id AS race_id,
                r.season_year,
                r.regulation_era AS era,
                rs.race_entry_id,
                rs.stint_number,
                rs.compound,
                rs.start_lap,
                rs.end_lap,
                l.lap_number,
                l.lap_time
            FROM race_stints rs
            JOIN races r ON r.id = rs.race_id
            JOIN sessions s ON s.race_id = r.id AND s.session_type = 'R'
            JOIN session_weather sw ON sw.session_id = s.id
            JOIN laps l
              ON l.session_id = s.id
             AND l.race_entry_id = rs.race_entry_id
            WHERE r.race_date IS NOT NULL
              AND r.season_year BETWEEN :start_year AND :end_year
              AND r.regulation_era IS NOT NULL
              AND sw.rainfall = FALSE
              AND rs.compound IN ('SOFT', 'MEDIUM', 'HARD')
              AND rs.start_lap IS NOT NULL
              AND rs.end_lap IS NOT NULL
              AND l.lap_number BETWEEN rs.start_lap AND rs.end_lap
              AND l.lap_time IS NOT NULL
              AND l.is_valid = TRUE
            ORDER BY r.season_year, r.id, rs.race_entry_id, l.lap_number
            """
        ),
        {"start_year": start_year, "end_year": end_year},
    )
    return [dict(row) for row in result.mappings().all()]


def _build_rows(raw_rows: list[dict]) -> tuple[list[LapRow], dict[int, int], int]:
    grouped_race_stops: dict[int, set[int]] = defaultdict(set)
    normalized: list[LapRow] = []
    for row in raw_rows:
        compound = str(row["compound"]).upper()
        race_id = int(row["race_id"])
        start_lap = int(row["start_lap"])
        end_lap = int(row["end_lap"])
        if start_lap > 1:
            grouped_race_stops[race_id].add(start_lap)
        normalized.append(
            LapRow(
                race_id=race_id,
                season_year=int(row["season_year"]),
                era=str(row["era"]),
                driver_id=int(row["race_entry_id"]),
                stint_key=f"{race_id}:{int(row['race_entry_id'])}:{int(row['stint_number'])}",
                compound=compound,
                start_lap=start_lap,
                end_lap=end_lap,
                lap_number=int(row["lap_number"]),
                lap_time=float(row["lap_time"]),
            )
        )
    usable_race_diversity = {
        race_id: len(stop_laps)
        for race_id, stop_laps in grouped_race_stops.items()
    }
    return normalized, usable_race_diversity, len(grouped_race_stops)


def build_field_relative_rows(
    rows: list[LapRow],
    race_stop_diversity: dict[int, int],
    *,
    min_unique_pit_laps: int = DEFAULT_MIN_UNIQUE_PIT_LAPS,
    min_stint_age_span: int = DEFAULT_MIN_STINT_AGE_SPAN,
) -> tuple[dict[str, list[tuple[int, float, str, str]]], dict[str, int]]:
    """Return pooled (era, compound) observations: tyre age + field-relative delta."""
    allowed_races = {
        race_id for race_id, count in race_stop_diversity.items()
        if count >= min_unique_pit_laps
    }
    if not allowed_races:
        return defaultdict(list), {"eligible_races": 0, "total_races": len(race_stop_diversity)}

    by_race_lap: dict[tuple[int, int], list[float]] = defaultdict(list)
    for row in rows:
        if row.race_id in allowed_races:
            by_race_lap[(row.race_id, row.lap_number)].append(row.lap_time)

    by_stint: dict[str, list[LapRow]] = defaultdict(list)
    for row in rows:
        if row.race_id in allowed_races:
            by_stint[row.stint_key].append(row)

    result: dict[str, list[tuple[int, float, str, str]]] = defaultdict(list)
    eligible_stints = 0
    for stint_key, stint_rows in by_stint.items():
        ordered = sorted(stint_rows, key=lambda r: r.lap_number)
        if not ordered:
            continue
        if ordered[-1].lap_number - ordered[0].lap_number < min_stint_age_span:
            continue
        eligible_stints += 1
        compound = ordered[0].compound
        era = ordered[0].era
        for row in ordered:
            # Exclude out-lap and in-lap, because they have structural pace effects.
            if row.lap_number == row.start_lap or row.lap_number == row.end_lap:
                continue
            field_values = by_race_lap[(row.race_id, row.lap_number)]
            if len(field_values) < 3:
                continue
            field_median = median(field_values)
            age = row.lap_number - row.start_lap
            if age <= 0:
                continue
            result[f"{era}|{compound}"].append((age, row.lap_time - field_median, stint_key, str(row.race_id)))

    return result, {
        "eligible_races": len(allowed_races),
        "total_races": len(race_stop_diversity),
        "eligible_stints": eligible_stints,
    }


def audit_pooled_rows(pooled: dict[str, list[tuple[int, float, str, str]]]) -> dict[str, RegressionSummary]:
    report: dict[str, RegressionSummary] = {}
    for key, observations in pooled.items():
        era, compound = key.split("|", 1)
        x = [float(row[0]) for row in observations]
        y = [float(row[1]) for row in observations]
        fit = _ols_ci(x, y)
        slopes_by_stint: dict[str, list[tuple[int, float]]] = defaultdict(list)
        for age, residual, stint_key, _race in observations:
            slopes_by_stint[stint_key].append((age, residual))
        stint_slopes: list[float] = []
        for stint_rows in slopes_by_stint.values():
            sx = [float(v[0]) for v in stint_rows]
            sy = [float(v[1]) for v in stint_rows]
            local = _ols_ci(sx, sy)
            if local is not None:
                stint_slopes.append(local[0])
        negative_rate = (
            sum(value < 0 for value in stint_slopes) / len(stint_slopes)
            if stint_slopes else None
        )
        report[key] = RegressionSummary(
            era=era,
            compound=compound,
            slope=fit[0] if fit else None,
            intercept=fit[1] if fit else None,
            ci_low=fit[2] if fit else None,
            ci_high=fit[3] if fit else None,
            n_laps=len(observations),
            n_stints=len({row[2] for row in observations}),
            n_races=len({row[3] for row in observations}),
            negative_rate=negative_rate,
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-year", type=int, default=2018)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument("--min-unique-pit-laps", type=int, default=DEFAULT_MIN_UNIQUE_PIT_LAPS)
    parser.add_argument("--min-stint-age-span", type=int, default=DEFAULT_MIN_STINT_AGE_SPAN)
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is not set")

    db = create_engine(database_url).connect()
    try:
        raw = _query_rows(db, args.start_year, args.end_year)
    finally:
        db.close()

    rows, race_diversity, race_count = _build_rows(raw)
    pooled, coverage = build_field_relative_rows(
        rows,
        race_diversity,
        min_unique_pit_laps=args.min_unique_pit_laps,
        min_stint_age_span=args.min_stint_age_span,
    )
    report = audit_pooled_rows(pooled)

    print("=== TYRE MODEL AUDIT V2 ===")
    print(f"years={args.start_year}-{args.end_year}")
    print(f"raw_laps={len(rows)}")
    print(f"races_with_stops={race_count}")
    print(f"eligible_races={coverage['eligible_races']}")
    print(f"eligible_stints={coverage['eligible_stints']}")
    print(f"min_unique_pit_laps={args.min_unique_pit_laps}")
    print(f"min_stint_age_span={args.min_stint_age_span}")
    print("WARNING: exact SC/VSC/red-flag lap windows are not available in the current DB query surface, so event-lap exclusion is not yet possible.")
    print("WARNING: driver traffic/defending effects are not controlled because gap-to-car-ahead data is not exposed here.")

    for key in sorted(report):
        row = report[key]
        print(f"\n{row.era} / {row.compound}")
        print(f"  slope_seconds_per_tyre_age_lap={row.slope}")
        print(f"  ci95_low={row.ci_low}")
        print(f"  ci95_high={row.ci_high}")
        print(f"  laps={row.n_laps}")
        print(f"  stints={row.n_stints}")
        print(f"  races={row.n_races}")
        print(f"  negative_stint_slope_rate={row.negative_rate}")

    print("\nInterpretation guardrails:")
    print("- This is an audit only; production calibration is unchanged.")
    print("- The pooled slope is the raw field-relative estimate; it is not clipped to zero.")
    print("- Do not promote the estimate unless its sign, uncertainty, coverage, and walk-forward behaviour are defensible.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
