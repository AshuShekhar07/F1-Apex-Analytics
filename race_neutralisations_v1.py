"""Exact SC / VSC / red-flag windows for a race, built from stored FastF1 data.

Sources (both written by backfill_fastf1_enrichment_v1.py, no network needed):
  session_track_status_intervals  -- status intervals on the session clock
  laps.lap_start_time_seconds     -- lap starts on the same session clock

Windows keep the FastF1 timestamps as the source of truth. They are mapped to
laps on the RACE LEADER's clock: lap N ends when the first car starts lap N+1.
(Mapping against every car's lap boundaries, as audit_race_strategy_event_timing_v1
does, lets a backmarker's boundary decide the lap.)

Status codes: 4 SC, 5 red flag, 6 VSC deployed, 7 VSC ending. A 6 followed
directly by 7 is one VSC. Nothing else is merged or inferred.

    python race_neutralisations_v1.py --start-year 2018 --end-year 2026 --csv neutralisations_v1.csv

The audit is read-only. It also compares event counts with
races.safety_car_periods / vsc_periods / red_flags, which backfill_safety_car.py
filled by counting status SAMPLES rather than periods.
"""

from __future__ import annotations

import argparse
import csv
import os
from bisect import bisect_left
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

EVENT_CODES = {"4": "SC", "5": "RED_FLAG", "6": "VSC"}
VSC_ENDING = "7"


@dataclass(frozen=True)
class StatusInterval:
    start_seconds: float
    end_seconds: float | None
    status_code: str


@dataclass(frozen=True)
class NeutralisationEvent:
    event_type: str
    start_seconds: float
    end_seconds: float | None
    start_lap: int | None = None
    end_lap: int | None = None

    @property
    def laps_affected(self) -> int | None:
        if self.start_lap is None or self.end_lap is None:
            return None
        return self.end_lap - self.start_lap + 1


@dataclass(frozen=True)
class RaceNeutralisations:
    race_id: int
    coverage: str  # ok | no_race_session | no_status_intervals | no_lap_timing
    events: tuple[NeutralisationEvent, ...]
    total_laps: int | None = None

    def count(self, event_type: str) -> int:
        return sum(1 for e in self.events if e.event_type == event_type)

    def neutralised_laps(self, event_types: Iterable[str] = ("SC", "VSC")) -> frozenset[int]:
        wanted = set(event_types)
        laps: set[int] = set()
        for event in self.events:
            if event.event_type in wanted and event.start_lap is not None and event.end_lap is not None:
                laps.update(range(event.start_lap, event.end_lap + 1))
        return frozenset(laps)


def build_events(intervals: Iterable[StatusInterval]) -> list[NeutralisationEvent]:
    """Turn status intervals into SC / VSC / red-flag windows."""
    ordered = sorted(intervals, key=lambda i: i.start_seconds)
    events: list[NeutralisationEvent] = []
    for index, interval in enumerate(ordered):
        code = str(interval.status_code)
        if code == VSC_ENDING:
            continue  # absorbed into the preceding VSC below
        event_type = EVENT_CODES.get(code)
        if event_type is None:
            continue
        end = interval.end_seconds
        if event_type == "VSC" and index + 1 < len(ordered):
            following = ordered[index + 1]
            if str(following.status_code) == VSC_ENDING and following.start_seconds == end:
                end = following.end_seconds
        previous = events[-1] if events else None
        if previous and previous.event_type == event_type and previous.end_seconds == interval.start_seconds:
            events[-1] = NeutralisationEvent(event_type, previous.start_seconds, end)
        else:
            events.append(NeutralisationEvent(event_type, interval.start_seconds, end))
    return events


def leader_lap_ends(lap_rows: Iterable[tuple[int, float | None, float | None]]) -> dict[int, float]:
    """Session time at which the race leader completed each lap.

    lap_rows: (lap_number, lap_start_time_seconds, lap_time_seconds) for every car.
    Lap N ends at the earliest start of lap N+1; the final lap falls back to the
    earliest start + lap time.
    """
    earliest_start: dict[int, float] = {}
    earliest_finish: dict[int, float] = {}
    for lap_number, start, lap_time in lap_rows:
        if start is None:
            continue
        n = int(lap_number)
        start = float(start)
        earliest_start[n] = min(start, earliest_start.get(n, start))
        if lap_time is not None:
            finish = start + float(lap_time)
            earliest_finish[n] = min(finish, earliest_finish.get(n, finish))

    ends: dict[int, float] = {}
    for n in sorted(earliest_start):
        if n + 1 in earliest_start:
            ends[n] = earliest_start[n + 1]
        elif n in earliest_finish:
            ends[n] = earliest_finish[n]
    return ends


def map_to_leader_laps(events: Iterable[NeutralisationEvent], lap_ends: dict[int, float]) -> list[NeutralisationEvent]:
    laps = sorted(lap_ends)
    end_times = [lap_ends[n] for n in laps]

    def lap_at(timestamp: float) -> int | None:
        if not laps:
            return None
        index = bisect_left(end_times, timestamp)
        return laps[min(index, len(laps) - 1)]

    mapped = []
    for event in events:
        end_lap = lap_at(event.end_seconds) if event.end_seconds is not None else (laps[-1] if laps else None)
        mapped.append(NeutralisationEvent(
            event.event_type, event.start_seconds, event.end_seconds, lap_at(event.start_seconds), end_lap,
        ))
    return mapped


def load_race_neutralisations(db: Any, race_id: int) -> RaceNeutralisations:
    session_id = db.execute(
        text("SELECT id FROM sessions WHERE race_id = :r AND session_type = 'R'"), {"r": race_id}
    ).scalar()
    if session_id is None:
        return RaceNeutralisations(race_id, "no_race_session", ())

    intervals = [
        StatusInterval(float(r.start_time_seconds),
                       float(r.end_time_seconds) if r.end_time_seconds is not None else None,
                       str(r.status_code))
        for r in db.execute(text("""
            SELECT start_time_seconds, end_time_seconds, status_code
            FROM session_track_status_intervals
            WHERE session_id = :s AND source = 'fastf1'
            ORDER BY start_time_seconds
        """), {"s": session_id})
    ]
    lap_rows = [
        (r.lap_number, r.lap_start_time_seconds, r.lap_time)
        for r in db.execute(text("""
            SELECT lap_number, lap_start_time_seconds, lap_time
            FROM laps WHERE session_id = :s
        """), {"s": session_id})
    ]
    total_laps = max((int(r[0]) for r in lap_rows), default=None)
    if not intervals:
        return RaceNeutralisations(race_id, "no_status_intervals", (), total_laps)

    events = build_events(intervals)
    lap_ends = leader_lap_ends(lap_rows)
    if not lap_ends:
        return RaceNeutralisations(race_id, "no_lap_timing", tuple(events), total_laps)
    return RaceNeutralisations(race_id, "ok", tuple(map_to_leader_laps(events, lap_ends)), total_laps)


def run_audit(db: Any, *, start_year: int, end_year: int) -> tuple[dict, list[dict]]:
    races = db.execute(text("""
        SELECT r.id, r.season_year, r.round_number, t.name AS track_name,
               r.safety_car_periods, r.vsc_periods, r.red_flags
        FROM races r
        JOIN tracks t ON t.id = r.track_id
        JOIN sessions s ON s.race_id = r.id AND s.session_type = 'R'
        WHERE r.season_year BETWEEN :a AND :b
        ORDER BY r.season_year, r.round_number
    """), {"a": start_year, "b": end_year}).mappings().all()

    by_season: dict[int, dict[str, int]] = {}
    event_rows: list[dict] = []
    for race in races:
        result = load_race_neutralisations(db, race["id"])
        season = by_season.setdefault(race["season_year"], {
            "races": 0, "ok": 0, "no_status_intervals": 0, "no_lap_timing": 0,
            "SC": 0, "VSC": 0, "RED_FLAG": 0, "stored_count_mismatch": 0,
        })
        season["races"] += 1
        season[result.coverage] = season.get(result.coverage, 0) + 1
        if result.coverage == "no_status_intervals":
            continue
        for event_type in ("SC", "VSC", "RED_FLAG"):
            season[event_type] += result.count(event_type)
        stored = (race["safety_car_periods"], race["vsc_periods"], race["red_flags"])
        derived = (result.count("SC"), result.count("VSC"), result.count("RED_FLAG"))
        mismatch = None not in stored and tuple(int(x) for x in stored) != derived
        season["stored_count_mismatch"] += int(mismatch)
        for event in result.events:
            event_rows.append({
                "race_id": race["id"], "season_year": race["season_year"], "round_number": race["round_number"],
                "track_name": race["track_name"], "coverage": result.coverage,
                **asdict(event), "laps_affected": event.laps_affected,
                "stored_sc_vsc_red": "/".join(str(x) for x in stored),
                "derived_sc_vsc_red": "/".join(str(x) for x in derived),
            })
    return by_season, event_rows


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start-year", type=int, default=2018)
    parser.add_argument("--end-year", type=int, default=2026)
    parser.add_argument("--csv", default="")
    args = parser.parse_args()

    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as db:
        by_season, event_rows = run_audit(db, start_year=args.start_year, end_year=args.end_year)

    print("season races ok no_intervals no_lap_timing   SC  VSC  RED  stored_count_mismatch")
    for season, s in sorted(by_season.items()):
        print(f"{season}   {s['races']:5} {s['ok']:3} {s['no_status_intervals']:12} {s['no_lap_timing']:13}"
              f" {s['SC']:4} {s['VSC']:4} {s['RED_FLAG']:4}  {s['stored_count_mismatch']:5}")
    if args.csv:
        fields = list(event_rows[0].keys()) if event_rows else ["race_id"]
        with open(args.csv, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(event_rows)
        print(f"wrote {len(event_rows)} events to {args.csv}")
    print("Read-only audit; database unchanged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
