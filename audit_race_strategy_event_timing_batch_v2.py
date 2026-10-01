"""Batch event-timing audit wrapper.

Audit-only. Runs the existing single-race event extractor over selected cases
and reports which event classes are observed. No database writes.
"""
from __future__ import annotations

import argparse
from collections import Counter

from audit_race_strategy_event_timing_v1 import extract_event_windows

DEFAULT_CASES = ((2025, 1), (2021, 6), (2021, 16))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", nargs="*", default=None,
                        help="Race cases as YEAR:ROUND")
    args = parser.parse_args()
    cases = DEFAULT_CASES
    if args.cases:
        cases = tuple(
            (int(raw.split(":", 1)[0]), int(raw.split(":", 1)[1]))
            for raw in args.cases
        )

    totals = Counter()
    print("=== EVENT TIMING BATCH AUDIT V2 ===")
    for year, round_number in cases:
        events = extract_event_windows(year, round_number)
        counts = Counter(event.event_type for event in events)
        totals.update(counts)
        print(f"{year} R{round_number}: SC={counts.get('SC',0)} VSC={counts.get('VSC',0)} RED_FLAG={counts.get('RED_FLAG',0)}")
        for event in events:
            print(f"  {event.event_type}: {event.start_seconds:.3f}s -> {event.end_seconds:.3f}s, lap {event.start_lap}->{event.end_lap}")
    print(f"Aggregate: SC={totals.get('SC',0)} VSC={totals.get('VSC',0)} RED_FLAG={totals.get('RED_FLAG',0)}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
