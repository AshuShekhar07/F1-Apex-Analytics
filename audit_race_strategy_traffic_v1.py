"""Research-only sustained close-following audit for race strategy.

Primary estimand: an observational within-driver pace association. Close-following
laps are compared with nearby clean-air laps from the same race, driver, stint,
compound and nearly the same tyre age.

This module deliberately does not infer defending intent, a causal traffic
penalty, or any production simulator parameter.
"""

from __future__ import annotations

import argparse
import csv
import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from math import isfinite
from statistics import mean, median
from typing import Any

import fastf1
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from audit_race_strategy_event_timing_v1 import EventWindow, extract_session_events

DEFAULT_CLOSE_DISTANCE_METERS = 150.0
DEFAULT_SENSITIVITY_THRESHOLDS = (100.0, 150.0, 200.0)
DEFAULT_MIN_CLOSE_FRACTION = 0.25
DEFAULT_MIN_AHEAD_COVERAGE = 0.50
DEFAULT_MAX_TELEMETRY_GAP_SECONDS = 2.0
DEFAULT_MAX_MATCH_LAP_DISTANCE = 10
DEFAULT_MAX_TYRE_AGE_DIFF = 1
DEFAULT_MIN_FIELD_LAPS = 3
DEFAULT_FIRST_LAPS_TO_EXCLUDE = 3
DEFAULT_MAX_CLEAN_AIR_REUSE = 1
DEFAULT_MIN_RACES_FOR_GATE = 10
DEFAULT_MIN_PAIRS_FOR_GATE = 50
DEFAULT_MIN_ERA_RACES_FOR_GATE = 5
DEFAULT_MIN_ERA_PAIRS_FOR_GATE = 20
VALID_COMPOUNDS = {"SOFT", "MEDIUM", "HARD"}


@dataclass(frozen=True)
class RaceMeta:
    race_id: int
    season_year: int
    round_number: int
    regulation_era: str
    rainfall: bool


@dataclass(frozen=True)
class LapRecord:
    race_id: int
    season_year: int
    round_number: int
    regulation_era: str
    driver_key: str
    driver_label: str
    lap_number: int
    stint_number: int | None
    compound: str | None
    tyre_age: int | None
    lap_time_seconds: float
    field_median_seconds: float
    field_relative_residual: float
    lap_start_seconds: float | None
    lap_end_seconds: float | None
    current_position: float | None
    previous_position: float | None


@dataclass(frozen=True)
class TrafficExposure:
    lap: LapRecord
    valid_seconds: float
    known_ahead_seconds: float
    close_seconds_by_threshold: tuple[tuple[float, float], ...]
    sustained_close_seconds_by_threshold: tuple[tuple[float, float], ...]
    dominant_driver_ahead: str | None

    def close_seconds(self, threshold: float) -> float:
        return _threshold_value(self.close_seconds_by_threshold, threshold)

    def close_fraction(self, threshold: float) -> float:
        return self.close_seconds(threshold) / self.valid_seconds if self.valid_seconds > 0 else 0.0

    def sustained_close_seconds(self, threshold: float) -> float:
        return _threshold_value(self.sustained_close_seconds_by_threshold, threshold)

    @property
    def known_ahead_fraction(self) -> float:
        return self.known_ahead_seconds / self.valid_seconds if self.valid_seconds > 0 else 0.0


@dataclass(frozen=True)
class TrafficMatch:
    race_id: int
    season_year: int
    round_number: int
    regulation_era: str
    driver_key: str
    driver_label: str
    stint_number: int
    compound: str
    close_lap: int
    clean_lap: int
    close_tyre_age: int
    clean_tyre_age: int
    close_fraction: float
    close_sustained_seconds: float
    traffic_delta_seconds: float
    position_delta: float | None


@dataclass
class AuditCounters:
    races_seen: int = 0
    races_loaded: int = 0
    races_skipped: int = 0
    dry_races: int = 0
    wet_races_skipped: int = 0
    lap_rows_seen: int = 0
    first_lap_excluded: int = 0
    pit_excluded: int = 0
    event_excluded: int = 0
    invalid_lap_excluded: int = 0
    missing_stint_or_compound: int = 0
    insufficient_field_laps: int = 0
    telemetry_attempts: int = 0
    telemetry_errors: int = 0
    telemetry_gap_excluded: int = 0
    telemetry_empty_excluded: int = 0
    missing_driver_ahead_excluded: int = 0
    usable_exposures: int = 0
    close_laps: int = 0
    clear_laps: int = 0
    unknown_laps: int = 0
    unmatched_close_laps: int = 0


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        result = pd.isna(value)
        return bool(result) if not hasattr(result, "__len__") else False
    except (TypeError, ValueError):
        return False


def _as_float(value: Any) -> float | None:
    if _is_missing(value):
        return None
    if hasattr(value, "total_seconds"):
        result = float(value.total_seconds())
    else:
        try:
            result = float(value)
        except (TypeError, ValueError):
            return None
    return result if isfinite(result) else None


def _as_int(value: Any) -> int | None:
    result = _as_float(value)
    return int(result) if result is not None else None


def _threshold_value(values: tuple[tuple[float, float], ...], threshold: float) -> float:
    for candidate, value in values:
        if abs(candidate - threshold) < 1e-9:
            return float(value)
    raise KeyError(f"Threshold {threshold} was not calculated")


def _lap_time_seconds(row: Any) -> float | None:
    result = _as_float(row.get("LapTime"))
    if result is None or result < 40.0 or result > 180.0:
        return None
    return result


def _driver_key(row: Any) -> str | None:
    value = row.get("DriverNumber")
    if _is_missing(value):
        value = row.get("Driver")
    return None if _is_missing(value) else str(value)


def _compound(row: Any) -> str | None:
    value = row.get("Compound")
    if _is_missing(value):
        return None
    compound = str(value).upper().strip()
    return compound if compound in VALID_COMPOUNDS else None


def _stint_number(row: Any) -> int | None:
    return _as_int(row.get("Stint"))


def _pit_lap(row: Any) -> bool:
    return not _is_missing(row.get("PitInTime")) or not _is_missing(row.get("PitOutTime"))


def _lap_interval(row: Any) -> tuple[float, float] | None:
    start = _as_float(row.get("LapStartTime"))
    end = _as_float(row.get("Time"))
    if start is None or end is None or end <= start:
        return None
    return start, end


def _interval_overlap_seconds(start: float, end: float, event: EventWindow) -> float:
    event_end = event.end_seconds
    if event_end is None:
        return max(0.0, end - max(start, event.start_seconds))
    return max(0.0, min(end, event_end) - max(start, event.start_seconds))


def _lap_overlaps_event(row: Any, events: list[EventWindow]) -> bool:
    interval = _lap_interval(row)
    if interval is not None:
        return any(_interval_overlap_seconds(interval[0], interval[1], event) > 0 for event in events)
    lap_number = _as_int(row.get("LapNumber"))
    if lap_number is None:
        return bool(events)
    for event in events:
        if event.start_lap is None:
            continue
        end_lap = event.end_lap if event.end_lap is not None else event.start_lap
        if event.start_lap <= lap_number <= end_lap:
            return True
    return False


def _driver_label_map(session: Any) -> dict[str, str]:
    labels: dict[str, str] = {}
    try:
        results = session.results
    except Exception:
        return labels
    if results is None or len(results) == 0:
        return labels
    for _, row in results.iterrows():
        number = str(row.get("DriverNumber", ""))
        abbreviation = str(row.get("Abbreviation", ""))
        full_name = str(row.get("FullName", ""))
        label = full_name if full_name and full_name != "nan" else abbreviation
        if number and number != "nan" and label and label != "nan":
            labels[number] = label
        if abbreviation and abbreviation != "nan" and label and label != "nan":
            labels[abbreviation] = label
    return labels


def _derive_tyre_age_rows(rows: list[Any]) -> dict[int, int]:
    first_by_stint: dict[tuple[str, int], int] = {}
    for row in rows:
        driver = _driver_key(row)
        stint = _stint_number(row)
        lap = _as_int(row.get("LapNumber"))
        if driver is not None and stint is not None and lap is not None:
            first_by_stint.setdefault((driver, stint), lap)
    result: dict[int, int] = {}
    for index, row in enumerate(rows):
        driver = _driver_key(row)
        stint = _stint_number(row)
        lap = _as_int(row.get("LapNumber"))
        if driver is not None and stint is not None and lap is not None:
            result[index] = max(0, lap - first_by_stint[(driver, stint)])
    return result


def _tyre_age(row: Any, derived_age: int | None) -> int | None:
    value = _as_int(row.get("TyreLife"))
    return value if value is not None and value >= 0 else derived_age


def _prepare_laps(
    session: Any,
    meta: RaceMeta,
    events: list[EventWindow],
    counters: AuditCounters,
    min_field_laps: int,
    first_laps_to_exclude: int,
) -> list[LapRecord]:
    raw_rows = [row for _, row in session.laps.iterrows()]
    derived_age = _derive_tyre_age_rows(raw_rows)

    position_map: dict[tuple[str, int], tuple[float | None, float | None]] = {}
    grouped_positions: dict[str, list[tuple[int, Any]]] = defaultdict(list)
    for row in raw_rows:
        driver = _driver_key(row)
        lap = _as_int(row.get("LapNumber"))
        if driver is not None and lap is not None:
            grouped_positions[driver].append((lap, row))
    for driver, rows_for_driver in grouped_positions.items():
        previous: float | None = None
        previous_lap: int | None = None
        for lap, row in sorted(rows_for_driver, key=lambda item: item[0]):
            current = _as_float(row.get("Position"))
            position_map[(driver, lap)] = (current, previous if previous_lap is not None and lap > previous_lap else None)
            if current is not None:
                previous = current
            previous_lap = lap

    clean_candidates: list[tuple[int, Any, float, str, str | None, int | None, int | None]] = []
    field_values_by_lap: dict[int, list[float]] = defaultdict(list)

    for index, row in enumerate(raw_rows):
        counters.lap_rows_seen += 1
        lap_number = _as_int(row.get("LapNumber"))
        if lap_number is None or lap_number <= first_laps_to_exclude:
            counters.first_lap_excluded += 1
            continue
        if "IsAccurate" in row.index and not _is_missing(row.get("IsAccurate")) and not bool(row.get("IsAccurate")):
            counters.invalid_lap_excluded += 1
            continue
        if "Deleted" in row.index and not _is_missing(row.get("Deleted")) and bool(row.get("Deleted")):
            counters.invalid_lap_excluded += 1
            continue
        lap_time = _lap_time_seconds(row)
        if lap_time is None:
            counters.invalid_lap_excluded += 1
            continue
        if _pit_lap(row):
            counters.pit_excluded += 1
            continue
        if _lap_overlaps_event(row, events):
            counters.event_excluded += 1
            continue

        driver = _driver_key(row)
        if driver is None:
            counters.invalid_lap_excluded += 1
            continue
        compound = _compound(row)
        stint = _stint_number(row)
        tyre_age = _tyre_age(row, derived_age.get(index))
        if stint is None or compound is None or tyre_age is None:
            counters.missing_stint_or_compound += 1
        field_values_by_lap[lap_number].append(lap_time)
        clean_candidates.append((index, row, lap_time, driver, compound, stint, tyre_age))

    field_medians = {
        lap: median(values) for lap, values in field_values_by_lap.items() if len(values) >= min_field_laps
    }
    records: list[LapRecord] = []
    for index, row, lap_time, driver, compound, stint, tyre_age in clean_candidates:
        lap_number = _as_int(row.get("LapNumber"))
        assert lap_number is not None
        field_median = field_medians.get(lap_number)
        if field_median is None:
            counters.insufficient_field_laps += 1
            continue
        interval = _lap_interval(row)
        current, previous = position_map.get((driver, lap_number), (None, None))
        records.append(
            LapRecord(
                race_id=meta.race_id,
                season_year=meta.season_year,
                round_number=meta.round_number,
                regulation_era=meta.regulation_era,
                driver_key=driver,
                driver_label=driver,
                lap_number=lap_number,
                stint_number=stint,
                compound=compound,
                tyre_age=tyre_age,
                lap_time_seconds=lap_time,
                field_median_seconds=float(field_median),
                field_relative_residual=lap_time - float(field_median),
                lap_start_seconds=interval[0] if interval else None,
                lap_end_seconds=interval[1] if interval else None,
                current_position=current,
                previous_position=previous,
            )
        )
    return records


def _sample_rows(telemetry: Any) -> list[dict[str, Any]]:
    required = {"DistanceToDriverAhead", "DriverAhead"}
    if telemetry is None or len(telemetry) < 2 or not required.issubset(telemetry.columns):
        return []
    time_column = "SessionTime" if "SessionTime" in telemetry.columns else "Time" if "Time" in telemetry.columns else None
    if time_column is None:
        return []
    return [
        {"time": _as_float(row[time_column]), "distance": _as_float(row["DistanceToDriverAhead"]), "ahead": row["DriverAhead"]}
        for _, row in telemetry.iterrows()
    ]


def _compute_exposure(
    telemetry: Any,
    events: list[EventWindow],
    thresholds: tuple[float, ...],
    max_gap_seconds: float,
) -> tuple[float, float, dict[float, float], dict[float, float], dict[str, float], bool, bool]:
    rows = _sample_rows(telemetry)
    if not rows:
        return 0.0, 0.0, {}, {}, {}, False, True
    if any(row["time"] is None for row in rows):
        return 0.0, 0.0, {}, {}, {}, False, True

    gaps = [float(b["time"]) - float(a["time"]) for a, b in zip(rows, rows[1:])]
    if any(gap <= 0.0 for gap in gaps):
        return 0.0, 0.0, {}, {}, {}, True, False
    if any(gap > max_gap_seconds for gap in gaps):
        return 0.0, 0.0, {}, {}, {}, True, False

    valid_seconds = 0.0
    known_seconds = 0.0
    close_seconds = {threshold: 0.0 for threshold in thresholds}
    sustained = {threshold: 0.0 for threshold in thresholds}
    streak = {threshold: 0.0 for threshold in thresholds}
    ahead_seconds: dict[str, float] = defaultdict(float)

    for i, gap in enumerate(gaps):
        start = float(rows[i]["time"])
        end = start + gap
        if any(_interval_overlap_seconds(start, end, event) > 0 for event in events):
            for threshold in thresholds:
                streak[threshold] = 0.0
            continue
        valid_seconds += gap
        distance = rows[i]["distance"]
        ahead = rows[i]["ahead"]
        ahead_key = None if _is_missing(ahead) else str(ahead)
        if distance is None or distance < 0.0 or ahead_key is None:
            for threshold in thresholds:
                streak[threshold] = 0.0
            continue
        known_seconds += gap
        ahead_seconds[ahead_key] += gap
        for threshold in thresholds:
            if distance <= threshold:
                close_seconds[threshold] += gap
                streak[threshold] += gap
                sustained[threshold] = max(sustained[threshold], streak[threshold])
            else:
                streak[threshold] = 0.0
    return valid_seconds, known_seconds, close_seconds, sustained, dict(ahead_seconds), False, False


def _lap_telemetry(session: Any, driver_key: str, lap_number: int) -> Any:
    driver_laps = session.laps[session.laps["DriverNumber"].astype(str) == str(driver_key)]
    selected = driver_laps[driver_laps["LapNumber"] == lap_number]
    if len(selected) == 0:
        raise ValueError(f"No FastF1 lap for driver={driver_key} lap={lap_number}")
    # FastF1 recommends add_driver_ahead() on a single lap (or a few laps) to limit integration error.
    return selected.iloc[[0]].get_telemetry().add_driver_ahead()


def _classify(exposure: TrafficExposure, threshold: float, min_close_fraction: float, min_ahead_coverage: float) -> str:
    if exposure.valid_seconds <= 0 or exposure.known_ahead_fraction < min_ahead_coverage:
        return "unknown"
    return "close" if exposure.close_fraction(threshold) >= min_close_fraction else "clear"


def _match_exposures(
    exposures: list[TrafficExposure],
    *,
    threshold: float,
    min_close_fraction: float,
    min_ahead_coverage: float,
    max_lap_distance: int,
    max_tyre_age_diff: int,
    max_clean_air_reuse: int,
) -> tuple[list[TrafficMatch], int, int, int]:
    grouped: dict[tuple[int, str, int, str], list[TrafficExposure]] = defaultdict(list)
    for exposure in exposures:
        lap = exposure.lap
        if lap.stint_number is None or lap.compound is None or lap.tyre_age is None:
            continue
        grouped[(lap.race_id, lap.driver_key, lap.stint_number, lap.compound)].append(exposure)

    matches: list[TrafficMatch] = []
    close_count = clear_count = unmatched_close = 0
    for group in grouped.values():
        ordered = sorted(group, key=lambda item: item.lap.lap_number)
        close_rows = [e for e in ordered if _classify(e, threshold, min_close_fraction, min_ahead_coverage) == "close"]
        clear_rows = [e for e in ordered if _classify(e, threshold, min_close_fraction, min_ahead_coverage) == "clear"]
        close_count += len(close_rows)
        clear_count += len(clear_rows)
        reuse: Counter[int] = Counter()
        for close in close_rows:
            candidates = [
                clear for clear in clear_rows
                if abs(clear.lap.tyre_age - close.lap.tyre_age) <= max_tyre_age_diff
                and abs(clear.lap.lap_number - close.lap.lap_number) <= max_lap_distance
            ]
            candidates.sort(key=lambda e: (
                abs(e.lap.tyre_age - close.lap.tyre_age),
                abs(e.lap.lap_number - close.lap.lap_number),
                e.lap.lap_number,
            ))
            chosen = next((e for e in candidates if reuse[e.lap.lap_number] < max_clean_air_reuse), None)
            if chosen is None:
                unmatched_close += 1
                continue
            reuse[chosen.lap.lap_number] += 1
            matches.append(
                TrafficMatch(
                    race_id=close.lap.race_id,
                    season_year=close.lap.season_year,
                    round_number=close.lap.round_number,
                    regulation_era=close.lap.regulation_era,
                    driver_key=close.lap.driver_key,
                    driver_label=close.lap.driver_label,
                    stint_number=int(close.lap.stint_number),
                    compound=str(close.lap.compound),
                    close_lap=close.lap.lap_number,
                    clean_lap=chosen.lap.lap_number,
                    close_tyre_age=int(close.lap.tyre_age),
                    clean_tyre_age=int(chosen.lap.tyre_age),
                    close_fraction=close.close_fraction(threshold),
                    close_sustained_seconds=close.sustained_close_seconds(threshold),
                    traffic_delta_seconds=close.lap.field_relative_residual - chosen.lap.field_relative_residual,
                    position_delta=(
                        close.lap.current_position - close.lap.previous_position
                        if close.lap.current_position is not None and close.lap.previous_position is not None
                        else None
                    ),
                )
            )
    return matches, unmatched_close, close_count, clear_count


def _race_balanced_summary(
    matches: list[TrafficMatch],
    *,
    threshold: float,
    unmatched_close: int,
    min_races_for_gate: int,
    min_pairs_for_gate: int,
    min_era_races_for_gate: int,
    min_era_pairs_for_gate: int,
) -> list[dict[str, Any]]:
    by_stint: dict[tuple[int, str, int], list[TrafficMatch]] = defaultdict(list)
    for match in matches:
        by_stint[(match.race_id, match.driver_key, match.stint_number)].append(match)
    stint_means = {
        key: mean(m.traffic_delta_seconds for m in rows) for key, rows in by_stint.items() if rows
    }
    by_race: dict[int, list[float]] = defaultdict(list)
    by_era: dict[str, list[TrafficMatch]] = defaultdict(list)
    for match in matches:
        by_era[match.regulation_era].append(match)
    for (race_id, _driver, _stint), value in stint_means.items():
        by_race[race_id].append(value)
    race_means = {race_id: mean(values) for race_id, values in by_race.items() if values}

    def row(scope: str, era: str, selected: list[TrafficMatch], selected_race_means: list[float], gate_races: int, gate_pairs: int, unmatched: int | None) -> dict[str, Any]:
        race_ids = sorted({m.race_id for m in selected})
        stints = {(m.race_id, m.driver_key, m.stint_number) for m in selected}
        deltas = [m.traffic_delta_seconds for m in selected]
        close_fractions = [m.close_fraction for m in selected]
        sustained = [m.close_sustained_seconds for m in selected]
        race_mean = mean(selected_race_means) if selected_race_means else None
        race_median = median(selected_race_means) if selected_race_means else None
        race_std = (
            (sum((value - race_mean) ** 2 for value in selected_race_means) / (len(selected_race_means) - 1)) ** 0.5
            if race_mean is not None and len(selected_race_means) > 1 else 0.0 if selected_race_means else None
        )
        return {
            "scope": scope,
            "threshold_m": threshold,
            "regulation_era": era,
            "races": len(race_ids),
            "stints": len(stints),
            "matched_pairs": len(deltas),
            "mean_traffic_delta_seconds_race_balanced": race_mean,
            "median_race_mean_delta_seconds": race_median,
            "std_race_mean_delta_seconds": race_std,
            "mean_pair_delta_seconds_diagnostic": mean(deltas) if deltas else None,
            "median_pair_delta_seconds_diagnostic": median(deltas) if deltas else None,
            "positive_pair_delta_rate": sum(v > 0 for v in deltas) / len(deltas) if deltas else None,
            "mean_close_fraction": mean(close_fractions) if close_fractions else None,
            "mean_sustained_close_seconds": mean(sustained) if sustained else None,
            "unmatched_close_laps": unmatched,
            "minimum_sample_gate_pass": len(race_ids) >= gate_races and len(deltas) >= gate_pairs,
        }

    rows = [row(
        "overall", "ALL", matches, list(race_means.values()),
        min_races_for_gate, min_pairs_for_gate, unmatched_close,
    )]
    for era, era_matches in sorted(by_era.items()):
        era_by_race: dict[int, list[float]] = defaultdict(list)
        era_stint_groups: dict[tuple[int, str, int], list[TrafficMatch]] = defaultdict(list)
        for match in era_matches:
            era_stint_groups[(match.race_id, match.driver_key, match.stint_number)].append(match)
        for key, stint_matches in era_stint_groups.items():
            era_by_race[key[0]].append(mean(m.traffic_delta_seconds for m in stint_matches))
        rows.append(row(
            "era", era, era_matches, [mean(values) for values in era_by_race.values()],
            min_era_races_for_gate, min_era_pairs_for_gate, None,
        ))
    return rows


def _write_csv(path: str, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    if os.path.exists(path):
        raise FileExistsError(f"Refusing to overwrite existing audit output: {path}")
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _query_races(db: Any, start_year: int, end_year: int) -> list[RaceMeta]:
    result = db.execute(
        text(
            """
            SELECT r.id AS race_id, r.season_year, r.round_number, r.regulation_era,
                   COALESCE(sw.rainfall, FALSE) AS rainfall
            FROM races r
            LEFT JOIN sessions s ON s.race_id = r.id AND s.session_type = 'R'
            LEFT JOIN session_weather sw ON sw.session_id = s.id
            WHERE r.race_date IS NOT NULL
              AND r.season_year BETWEEN :start_year AND :end_year
              AND r.regulation_era IS NOT NULL
            ORDER BY r.season_year, r.round_number, r.id
            """
        ),
        {"start_year": start_year, "end_year": end_year},
    )
    return [
        RaceMeta(int(r["race_id"]), int(r["season_year"]), int(r["round_number"]), str(r["regulation_era"]), bool(r["rainfall"]))
        for r in result.mappings().all()
    ]


def _process_race(
    meta: RaceMeta,
    *,
    cache_dir: str,
    thresholds: tuple[float, ...],
    primary_threshold: float,
    min_close_fraction: float,
    min_ahead_coverage: float,
    min_field_laps: int,
    first_laps_to_exclude: int,
    max_gap_seconds: float,
    counters: AuditCounters,
) -> list[TrafficExposure]:
    fastf1.Cache.enable_cache(os.path.expanduser(cache_dir))
    session = fastf1.get_session(meta.season_year, meta.round_number, "Race")
    session.load(laps=True, telemetry=True, weather=False, messages=False)
    events = extract_session_events(session)
    records = _prepare_laps(session, meta, events, counters, min_field_laps, first_laps_to_exclude)
    labels = _driver_label_map(session)
    exposures: list[TrafficExposure] = []

    for record in records:
        label = labels.get(record.driver_key, record.driver_key)
        lap = LapRecord(**{**record.__dict__, "driver_label": label})
        counters.telemetry_attempts += 1
        try:
            telemetry = _lap_telemetry(session, record.driver_key, record.lap_number)
            valid, known, close, sustained, ahead_seconds, gap_error, empty_error = _compute_exposure(
                telemetry, events, thresholds, max_gap_seconds
            )
        except Exception as exc:
            counters.telemetry_errors += 1
            print(f"  TELEMETRY_SKIP {meta.season_year} R{meta.round_number} {record.driver_key} L{record.lap_number}: {exc}", flush=True)
            continue
        if gap_error:
            counters.telemetry_gap_excluded += 1
            continue
        if empty_error or valid <= 0:
            counters.telemetry_empty_excluded += 1
            continue
        if known / valid < min_ahead_coverage:
            counters.missing_driver_ahead_excluded += 1
            counters.unknown_laps += 1
            continue
        dominant = max(ahead_seconds.items(), key=lambda item: item[1])[0] if ahead_seconds else None
        exposure = TrafficExposure(
            lap=lap,
            valid_seconds=valid,
            known_ahead_seconds=known,
            close_seconds_by_threshold=tuple(sorted(close.items())),
            sustained_close_seconds_by_threshold=tuple(sorted(sustained.items())),
            dominant_driver_ahead=dominant,
        )
        exposures.append(exposure)
        counters.usable_exposures += 1
        state = _classify(exposure, primary_threshold, min_close_fraction, min_ahead_coverage)
        if state == "close":
            counters.close_laps += 1
        elif state == "clear":
            counters.clear_laps += 1
    return exposures


def _exposure_rows(
    exposures: list[TrafficExposure],
    *,
    primary_threshold: float,
    min_close_fraction: float,
    min_ahead_coverage: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for exposure in exposures:
        lap = exposure.lap
        rows.append({
            "season_year": lap.season_year,
            "round_number": lap.round_number,
            "race_id": lap.race_id,
            "regulation_era": lap.regulation_era,
            "driver": lap.driver_label,
            "driver_key": lap.driver_key,
            "driver_ahead": exposure.dominant_driver_ahead,
            "lap_number": lap.lap_number,
            "stint_number": lap.stint_number,
            "compound": lap.compound,
            "tyre_age": lap.tyre_age,
            "lap_time_seconds": lap.lap_time_seconds,
            "field_median_seconds": lap.field_median_seconds,
            "field_relative_residual_seconds": lap.field_relative_residual,
            "traffic_state": _classify(exposure, primary_threshold, min_close_fraction, min_ahead_coverage),
            "close_distance_threshold_m": primary_threshold,
            "close_seconds": exposure.close_seconds(primary_threshold),
            "valid_seconds": exposure.valid_seconds,
            "close_fraction": exposure.close_fraction(primary_threshold),
            "known_ahead_fraction": exposure.known_ahead_fraction,
            "sustained_close_seconds": exposure.sustained_close_seconds(primary_threshold),
            "current_position": lap.current_position,
            "previous_position": lap.previous_position,
            "position_delta": (
                lap.current_position - lap.previous_position
                if lap.current_position is not None and lap.previous_position is not None else None
            ),
        })
    return rows


def _audit_gate(summary_rows: list[dict[str, Any]], primary_threshold: float) -> str:
    overall = [r for r in summary_rows if r["scope"] == "overall"]
    primary = next((r for r in overall if abs(float(r["threshold_m"]) - primary_threshold) < 1e-9), None)
    if primary is None or not primary["minimum_sample_gate_pass"]:
        return "INSUFFICIENT_EVIDENCE"

    era_rows = [r for r in summary_rows if r["scope"] == "era"]
    if not era_rows or any(not r["minimum_sample_gate_pass"] for r in era_rows):
        return "INSUFFICIENT_EVIDENCE"

    primary_value = primary["mean_traffic_delta_seconds_race_balanced"]
    if primary_value is None or float(primary_value) == 0.0:
        return "INSUFFICIENT_EVIDENCE"
    primary_sign = 1 if float(primary_value) > 0 else -1

    for row in overall:
        value = row["mean_traffic_delta_seconds_race_balanced"]
        if value is None or float(value) == 0.0:
            return "INSUFFICIENT_EVIDENCE"
        if (1 if float(value) > 0 else -1) != primary_sign:
            return "INSUFFICIENT_EVIDENCE"
    for row in era_rows:
        value = row["mean_traffic_delta_seconds_race_balanced"]
        if value is None or float(value) == 0.0:
            return "INSUFFICIENT_EVIDENCE"
        if (1 if float(value) > 0 else -1) != primary_sign:
            return "INSUFFICIENT_EVIDENCE"
    return "PROCEED TO PREDICTIVE VALIDATION"


def parse_thresholds(value: str) -> tuple[float, ...]:
    parsed = []
    for token in value.split(","):
        token = token.strip()
        if not token:
            continue
        threshold = float(token)
        if threshold <= 0:
            raise ValueError("Thresholds must be positive")
        parsed.append(threshold)
    if not parsed:
        raise ValueError("At least one threshold is required")
    return tuple(sorted(set(parsed)))


def run_audit(
    *,
    start_year: int,
    end_year: int,
    cache_dir: str,
    close_distance_m: float = DEFAULT_CLOSE_DISTANCE_METERS,
    sensitivity_thresholds: tuple[float, ...] = DEFAULT_SENSITIVITY_THRESHOLDS,
    min_close_fraction: float = DEFAULT_MIN_CLOSE_FRACTION,
    min_ahead_coverage: float = DEFAULT_MIN_AHEAD_COVERAGE,
    max_gap_seconds: float = DEFAULT_MAX_TELEMETRY_GAP_SECONDS,
    max_match_lap_distance: int = DEFAULT_MAX_MATCH_LAP_DISTANCE,
    max_tyre_age_diff: int = DEFAULT_MAX_TYRE_AGE_DIFF,
    min_field_laps: int = DEFAULT_MIN_FIELD_LAPS,
    first_laps_to_exclude: int = DEFAULT_FIRST_LAPS_TO_EXCLUDE,
    max_clean_air_reuse: int = DEFAULT_MAX_CLEAN_AIR_REUSE,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], AuditCounters]:
    if start_year > end_year:
        raise ValueError("start_year cannot be greater than end_year")
    thresholds = tuple(sorted(set(float(v) for v in (*sensitivity_thresholds, close_distance_m))))
    if any(v <= 0 for v in thresholds):
        raise ValueError("All distance thresholds must be positive")
    if not 0.0 <= min_close_fraction <= 1.0:
        raise ValueError("min_close_fraction must be between 0 and 1")
    if not 0.0 <= min_ahead_coverage <= 1.0:
        raise ValueError("min_ahead_coverage must be between 0 and 1")

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set")
    db = create_engine(database_url).connect()
    try:
        races = _query_races(db, start_year, end_year)
    finally:
        db.close()

    counters = AuditCounters(races_seen=len(races))
    exposures: list[TrafficExposure] = []
    for meta in races:
        if meta.rainfall:
            counters.wet_races_skipped += 1
            print(f"SKIP WET {meta.season_year} R{meta.round_number}", flush=True)
            continue
        counters.dry_races += 1
        try:
            race_exposures = _process_race(
                meta,
                cache_dir=cache_dir,
                thresholds=thresholds,
                primary_threshold=close_distance_m,
                min_close_fraction=min_close_fraction,
                min_ahead_coverage=min_ahead_coverage,
                min_field_laps=min_field_laps,
                first_laps_to_exclude=first_laps_to_exclude,
                max_gap_seconds=max_gap_seconds,
                counters=counters,
            )
        except Exception as exc:
            counters.races_skipped += 1
            print(f"SKIP {meta.season_year} R{meta.round_number}: {exc}", flush=True)
            continue
        counters.races_loaded += 1
        exposures.extend(race_exposures)
        print(f"{meta.season_year} R{meta.round_number}: exposures={len(race_exposures)}", flush=True)

    exposure_rows = _exposure_rows(
        exposures,
        primary_threshold=close_distance_m,
        min_close_fraction=min_close_fraction,
        min_ahead_coverage=min_ahead_coverage,
    )

    summary_rows: list[dict[str, Any]] = []
    for threshold in thresholds:
        matches, unmatched, _close, _clear = _match_exposures(
            exposures,
            threshold=threshold,
            min_close_fraction=min_close_fraction,
            min_ahead_coverage=min_ahead_coverage,
            max_lap_distance=max_match_lap_distance,
            max_tyre_age_diff=max_tyre_age_diff,
            max_clean_air_reuse=max_clean_air_reuse,
        )
        summary_rows.extend(_race_balanced_summary(
            matches,
            threshold=threshold,
            unmatched_close=unmatched,
            min_races_for_gate=DEFAULT_MIN_RACES_FOR_GATE,
            min_pairs_for_gate=DEFAULT_MIN_PAIRS_FOR_GATE,
            min_era_races_for_gate=DEFAULT_MIN_ERA_RACES_FOR_GATE,
            min_era_pairs_for_gate=DEFAULT_MIN_ERA_PAIRS_FOR_GATE,
        ))
        if abs(threshold - close_distance_m) < 1e-9:
            counters.unmatched_close_laps = unmatched

    decision = _audit_gate(summary_rows, close_distance_m)
    for row in summary_rows:
        if row["scope"] == "overall" and abs(float(row["threshold_m"]) - close_distance_m) < 1e-9:
            row["decision"] = decision
            row["primary_close_laps"] = counters.close_laps
            row["primary_clear_laps"] = counters.clear_laps
        else:
            row["decision"] = "NOT_PRIMARY"
            row["primary_close_laps"] = None
            row["primary_clear_laps"] = None
    return exposure_rows, summary_rows, counters


def main() -> int:
    parser = argparse.ArgumentParser(description="Research-only sustained close-following traffic audit v1")
    parser.add_argument("--start-year", type=int, default=2018)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument("--cache-dir", default="~/projects/F1-Apex-Analytics/cache")
    parser.add_argument("--close-distance-m", type=float, default=DEFAULT_CLOSE_DISTANCE_METERS)
    parser.add_argument("--sensitivity-thresholds", default="100,150,200")
    parser.add_argument("--min-close-fraction", type=float, default=DEFAULT_MIN_CLOSE_FRACTION)
    parser.add_argument("--min-ahead-coverage", type=float, default=DEFAULT_MIN_AHEAD_COVERAGE)
    parser.add_argument("--max-telemetry-gap-seconds", type=float, default=DEFAULT_MAX_TELEMETRY_GAP_SECONDS)
    parser.add_argument("--max-match-lap-distance", type=int, default=DEFAULT_MAX_MATCH_LAP_DISTANCE)
    parser.add_argument("--max-tyre-age-diff", type=int, default=DEFAULT_MAX_TYRE_AGE_DIFF)
    parser.add_argument("--min-field-laps", type=int, default=DEFAULT_MIN_FIELD_LAPS)
    parser.add_argument("--first-laps-to-exclude", type=int, default=DEFAULT_FIRST_LAPS_TO_EXCLUDE)
    parser.add_argument("--max-clean-air-reuse", type=int, default=DEFAULT_MAX_CLEAN_AIR_REUSE)
    parser.add_argument("--csv", default="traffic_exposure_v1.csv")
    parser.add_argument("--summary-csv", default="traffic_effect_summary_v1.csv")
    args = parser.parse_args()
    load_dotenv()

    thresholds = parse_thresholds(args.sensitivity_thresholds)
    exposure_rows, summary_rows, counters = run_audit(
        start_year=args.start_year,
        end_year=args.end_year,
        cache_dir=args.cache_dir,
        close_distance_m=args.close_distance_m,
        sensitivity_thresholds=thresholds,
        min_close_fraction=args.min_close_fraction,
        min_ahead_coverage=args.min_ahead_coverage,
        max_gap_seconds=args.max_telemetry_gap_seconds,
        max_match_lap_distance=args.max_match_lap_distance,
        max_tyre_age_diff=args.max_tyre_age_diff,
        min_field_laps=args.min_field_laps,
        first_laps_to_exclude=args.first_laps_to_exclude,
        max_clean_air_reuse=args.max_clean_air_reuse,
    )

    exposure_fields = [
        "season_year", "round_number", "race_id", "regulation_era", "driver", "driver_key", "driver_ahead",
        "lap_number", "stint_number", "compound", "tyre_age", "lap_time_seconds", "field_median_seconds",
        "field_relative_residual_seconds", "traffic_state", "close_distance_threshold_m", "close_seconds",
        "valid_seconds", "close_fraction", "known_ahead_fraction", "sustained_close_seconds",
        "current_position", "previous_position", "position_delta",
    ]
    summary_fields = [
        "scope", "threshold_m", "regulation_era", "races", "stints", "matched_pairs",
        "mean_traffic_delta_seconds_race_balanced", "median_race_mean_delta_seconds", "std_race_mean_delta_seconds",
        "mean_pair_delta_seconds_diagnostic", "median_pair_delta_seconds_diagnostic", "positive_pair_delta_rate",
        "mean_close_fraction", "mean_sustained_close_seconds", "unmatched_close_laps", "minimum_sample_gate_pass",
        "decision", "primary_close_laps", "primary_clear_laps",
    ]
    _write_csv(args.csv, exposure_rows, exposure_fields)
    _write_csv(args.summary_csv, summary_rows, summary_fields)

    primary = next(
        (r for r in summary_rows if r["scope"] == "overall" and abs(float(r["threshold_m"]) - args.close_distance_m) < 1e-9),
        None,
    )
    print("\n=== TRAFFIC EXPOSURE AUDIT V1 ===")
    print(f"years={args.start_year}-{args.end_year}")
    print(f"primary_close_distance_m={args.close_distance_m}")
    print(f"sensitivity_thresholds={','.join(str(v) for v in thresholds)}")
    print(f"races_seen={counters.races_seen} dry={counters.dry_races} loaded={counters.races_loaded} skipped={counters.races_skipped}")
    print(f"wet_races_skipped={counters.wet_races_skipped}")
    print(f"lap_rows_seen={counters.lap_rows_seen} first_lap_excluded={counters.first_lap_excluded} pit_excluded={counters.pit_excluded} event_excluded={counters.event_excluded}")
    print(f"invalid_lap_excluded={counters.invalid_lap_excluded} insufficient_field_laps={counters.insufficient_field_laps}")
    print(f"telemetry_attempts={counters.telemetry_attempts} telemetry_errors={counters.telemetry_errors} telemetry_gap_excluded={counters.telemetry_gap_excluded}")
    print(f"telemetry_empty_excluded={counters.telemetry_empty_excluded} missing_driver_ahead_excluded={counters.missing_driver_ahead_excluded}")
    print(f"usable_exposures={counters.usable_exposures} primary_close_laps={counters.close_laps} primary_clear_laps={counters.clear_laps}")
    print(f"primary_unmatched_close_laps={counters.unmatched_close_laps}")
    if primary:
        print("\nPrimary interpretation:")
        print(f"race_balanced_mean_delta_seconds={primary['mean_traffic_delta_seconds_race_balanced']}")
        print(f"median_race_mean_delta_seconds={primary['median_race_mean_delta_seconds']}")
        print(f"matched_pairs={primary['matched_pairs']} races={primary['races']} stints={primary['stints']}")
        print(f"decision={primary['decision']}")
    print("\nInterpretation guardrails:")
    print("- Observational within-driver association only; not a causal traffic penalty.")
    print("- DriverAhead/DistanceToDriverAhead are reconstructed telemetry inputs and can contain GPS/integration or pit-lane artefacts.")
    print("- Position changes are descriptive and do not establish defending intent.")
    print("- Production simulator integration is out of scope until predictive walk-forward validation.")
    print(f"\nWrote {args.csv} and {args.summary_csv}")
    print("Audit only; database and production simulator were not modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
