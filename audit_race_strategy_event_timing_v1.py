"""Extract exact SC/VSC/red-flag event windows from FastF1 track_status.

Audit-only. This script preserves event timestamps from FastF1 as the source of
truth and optionally maps those timestamps to approximate race laps using lap
completion times. It does not write to the database or production simulator.

FastF1 status codes used here:
  4 = Safety Car deployed
  5 = Red flag
  6 = Virtual Safety Car deployed
  7 = VSC ending marker
"""
from __future__ import annotations

import argparse
import csv
import os
from bisect import bisect_left
from dataclasses import dataclass

import fastf1
from dotenv import load_dotenv

EVENT_CODES = {"4": "SC", "5": "RED_FLAG", "6": "VSC"}


@dataclass(frozen=True)
class StatusPoint:
    time_seconds: float
    status: str


@dataclass(frozen=True)
class LapBoundary:
    lap_number: int
    end_seconds: float


@dataclass(frozen=True)
class EventWindow:
    event_type: str
    start_seconds: float
    end_seconds: float | None
    start_lap: int | None
    end_lap: int | None


def _seconds(value) -> float:
    if value is None:
        raise ValueError("Timestamp cannot be None")
    if hasattr(value, "total_seconds"):
        return float(value.total_seconds())
    return float(value)


def normalize_status_points(status_points) -> list[StatusPoint]:
    """Sort status changes and collapse duplicate consecutive states."""
    ordered = sorted(
        (StatusPoint(_seconds(p.time_seconds), str(p.status)) for p in status_points),
        key=lambda p: p.time_seconds,
    )
    normalized: list[StatusPoint] = []
    for point in ordered:
        if normalized and point.status == normalized[-1].status:
            continue
        normalized.append(point)
    return normalized


def extract_event_windows(status_points) -> list[tuple[str, float, float | None]]:
    """Turn status transitions into event intervals without inventing timing."""
    points = normalize_status_points(status_points)
    windows: list[tuple[str, float, float | None]] = []
    for index, point in enumerate(points):
        event_type = EVENT_CODES.get(point.status)
        if event_type is None:
            continue
        end = points[index + 1].time_seconds if index + 1 < len(points) else None
        windows.append((event_type, point.time_seconds, end))
    return windows


def _lap_for_time(end_times: list[float], lap_numbers: list[int], timestamp: float) -> int | None:
    if not end_times:
        return None
    index = bisect_left(end_times, timestamp)
    if index >= len(end_times):
        return lap_numbers[-1]
    return lap_numbers[index]


def map_windows_to_laps(windows, lap_boundaries: list[LapBoundary]) -> list[EventWindow]:
    ordered = sorted(lap_boundaries, key=lambda x: x.end_seconds)
    end_times = [lap.end_seconds for lap in ordered]
    lap_numbers = [lap.lap_number for lap in ordered]
    result: list[EventWindow] = []
    for event_type, start, end in windows:
        result.append(
            EventWindow(
                event_type=event_type,
                start_seconds=start,
                end_seconds=end,
                start_lap=_lap_for_time(end_times, lap_numbers, start),
                end_lap=_lap_for_time(end_times, lap_numbers, end) if end is not None else None,
            )
        )
    return result


def extract_session_events(session) -> list[EventWindow]:
    status = getattr(session, "track_status", None)
    laps = getattr(session, "laps", None)
    if status is None or len(status) == 0:
        return []

    status_points = [
        StatusPoint(_seconds(row["Time"]), str(row["Status"]))
        for _, row in status.iterrows()
    ]
    windows = extract_event_windows(status_points)

    boundaries: list[LapBoundary] = []
    if laps is not None and len(laps) > 0 and "Time" in laps.columns and "LapNumber" in laps.columns:
        for _, row in laps[["LapNumber", "Time"]].dropna().iterrows():
            boundaries.append(LapBoundary(int(row["LapNumber"]), _seconds(row["Time"])))

    return map_windows_to_laps(windows, boundaries)


def load_race(year: int, round_number: int, cache_dir: str) -> list[EventWindow]:
    fastf1.Cache.enable_cache(os.path.expanduser(cache_dir))
    session = fastf1.get_session(year, round_number, "Race")
    session.load(laps=True, telemetry=False, weather=False, messages=False)
    return extract_session_events(session)


def write_csv(path: str, year: int, round_number: int, windows: list[EventWindow]):
    rows = []
    for index, event in enumerate(windows, start=1):
        rows.append(
            {
                "season_year": year,
                "round_number": round_number,
                "event_index": index,
                "event_type": event.event_type,
                "start_seconds": event.start_seconds,
                "end_seconds": event.end_seconds,
                "duration_seconds": (
                    event.end_seconds - event.start_seconds
                    if event.end_seconds is not None
                    else None
                ),
                "start_lap_by_lap_end": event.start_lap,
                "end_lap_by_lap_end": event.end_lap,
            }
        )
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(rows[0].keys()) if rows else [
                "season_year", "round_number", "event_index", "event_type",
                "start_seconds", "end_seconds", "duration_seconds",
                "start_lap_by_lap_end", "end_lap_by_lap_end",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--round", dest="round_number", type=int, required=True)
    parser.add_argument("--csv", default=None)
    parser.add_argument("--cache-dir", default="~/projects/F1-Apex-Analytics/cache")
    args = parser.parse_args()

    load_dotenv()
    output = args.csv or f"event_timing_{args.year}_r{args.round_number}.csv"
    windows = load_race(args.year, args.round_number, args.cache_dir)
    write_csv(output, args.year, args.round_number, windows)

    print(f"=== EVENT TIMING AUDIT {args.year} R{args.round_number} ===")
    print(f"events={len(windows)}")
    for index, event in enumerate(windows, start=1):
        print(
            f"{index}: {event.event_type} "
            f"start={event.start_seconds:.3f}s end={event.end_seconds if event.end_seconds is not None else None} "
            f"lap={event.start_lap}->{event.end_lap}"
        )
    print(f"Wrote {output}")
    print("Audit only; database and production simulator were not modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
