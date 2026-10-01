"""Empirical race-event timing audit from FastF1 track_status.

Audit-only. Loads historical Race sessions through the exact event-window
extractor and summarizes SC/VSC/red-flag event starts by normalized race phase
plus observed duration distributions.

This is intentionally an empirical event-rate audit, not yet a production
hazard model. No database writes and no synthetic event timing.
"""
from __future__ import annotations

import argparse
import csv
import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from statistics import mean, median

import fastf1

from audit_race_strategy_event_timing_v1 import extract_session_events

EVENT_TYPES = ("SC", "VSC", "RED_FLAG")
PHASES = (
    ("early", 0.00, 0.25),
    ("mid_early", 0.25, 0.50),
    ("mid_late", 0.50, 0.75),
    ("late", 0.75, 1.00),
)


@dataclass(frozen=True)
class EventObservation:
    year: int
    round_number: int
    event_type: str
    start_lap: int | None
    total_laps: int
    phase: str | None
    duration_seconds: float | None


def phase_for_lap(lap: int | None, total_laps: int) -> str | None:
    if lap is None or total_laps <= 0:
        return None
    fraction = (lap - 1) / total_laps
    for name, lower, upper in PHASES:
        if lower <= fraction < upper:
            return name
    return PHASES[-1][0] if fraction <= 1.0 else None


def summarize(observations: list[EventObservation], race_total: int) -> dict:
    starts_by_type_phase: Counter[tuple[str, str]] = Counter()
    durations: defaultdict[str, list[float]] = defaultdict(list)
    for obs in observations:
        if obs.phase is not None:
            starts_by_type_phase[(obs.event_type, obs.phase)] += 1
        if obs.duration_seconds is not None and obs.duration_seconds >= 0:
            durations[obs.event_type].append(obs.duration_seconds)

    rows: list[dict] = []
    for event_type in EVENT_TYPES:
        for phase, _, _ in PHASES:
            starts = starts_by_type_phase[(event_type, phase)]
            # This is deliberately an event-start rate per race, not a claim of
            # a fully conditional per-lap hazard. A later model can add proper
            # time-at-risk exposure and competing-risk treatment.
            rows.append(
                {
                    "event_type": event_type,
                    "phase": phase,
                    "event_starts": starts,
                    "races": race_total,
                    "starts_per_race": starts / race_total if race_total else None,
                }
            )

    duration_rows: list[dict] = []
    for event_type in EVENT_TYPES:
        values = sorted(durations[event_type])
        if not values:
            continue
        duration_rows.append(
            {
                "event_type": event_type,
                "n": len(values),
                "mean_seconds": mean(values),
                "median_seconds": median(values),
                "min_seconds": values[0],
                "max_seconds": values[-1],
            }
        )

    return {"phase_rows": rows, "duration_rows": duration_rows}


def load_and_extract(year: int, round_number: int, cache_dir: str) -> list[EventObservation]:
    fastf1.Cache.enable_cache(os.path.expanduser(cache_dir))
    session = fastf1.get_session(year, round_number, "Race")
    session.load(laps=True, telemetry=False, weather=False, messages=False)
    events = extract_session_events(session)
    total_laps = 0
    if session.laps is not None and len(session.laps) > 0 and "LapNumber" in session.laps.columns:
        total_laps = int(session.laps["LapNumber"].max())

    observations: list[EventObservation] = []
    for event in events:
        observations.append(
            EventObservation(
                year=year,
                round_number=round_number,
                event_type=event.event_type,
                start_lap=event.start_lap,
                total_laps=total_laps,
                phase=phase_for_lap(event.start_lap, total_laps),
                duration_seconds=(
                    event.end_seconds - event.start_seconds
                    if event.end_seconds is not None
                    else None
                ),
            )
        )
    return observations


def write_csv(path: str, rows: list[dict]):
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-year", type=int, default=2018)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument("--csv", default="event_hazard_phase_v1.csv")
    parser.add_argument("--duration-csv", default="event_duration_summary_v1.csv")
    parser.add_argument("--cache-dir", default="~/projects/F1-Apex-Analytics/cache")
    args = parser.parse_args()

    all_observations: list[EventObservation] = []
    race_count = 0
    skipped = 0

    for year in range(args.start_year, args.end_year + 1):
        schedule = fastf1.get_event_schedule(year, include_testing=False)
        for _, event in schedule.iterrows():
            round_number = int(event["RoundNumber"])
            if round_number <= 0:
                continue
            race_count += 1
            try:
                observations = load_and_extract(year, round_number, args.cache_dir)
            except Exception as exc:
                skipped += 1
                print(f"SKIP {year} R{round_number}: {exc}")
                continue
            all_observations.extend(observations)
            counts = Counter(obs.event_type for obs in observations)
            print(
                f"{year} R{round_number}: events={len(observations)} "
                f"SC={counts['SC']} VSC={counts['VSC']} RED_FLAG={counts['RED_FLAG']}",
                flush=True,
            )

    summary = summarize(all_observations, max(0, race_count - skipped))
    write_csv(args.csv, summary["phase_rows"])
    write_csv(args.duration_csv, summary["duration_rows"])

    print("\n=== EVENT HAZARD AUDIT V1 ===")
    print(f"races_seen={race_count} races_loaded={race_count - skipped} skipped={skipped}")
    for event_type in EVENT_TYPES:
        total = sum(1 for obs in all_observations if obs.event_type == event_type)
        print(f"{event_type}: event_starts={total}")
    print("\nPhase start rates (event starts per loaded race):")
    for row in summary["phase_rows"]:
        print(
            f"{row['event_type']:8} {row['phase']:10} "
            f"starts={row['event_starts']:3} rate={row['starts_per_race']:.4f}"
        )
    print("\nDuration summary (seconds):")
    for row in summary["duration_rows"]:
        print(
            f"{row['event_type']:8} n={row['n']:3} "
            f"mean={row['mean_seconds']:.1f} median={row['median_seconds']:.1f} "
            f"min={row['min_seconds']:.1f} max={row['max_seconds']:.1f}"
        )
    print(f"\nWrote {args.csv} and {args.duration_csv}")
    print("Audit only; database and production simulator were not modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
