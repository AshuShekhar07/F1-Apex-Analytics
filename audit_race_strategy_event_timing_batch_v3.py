"""Batch event-timing validation for selected historical races.

Audit-only; no database writes. Uses the existing event extractor and checks
for SC, VSC, and red-flag windows without assuming that every race has all types.
"""
from __future__ import annotations

import argparse
from collections import Counter

from audit_race_strategy_event_timing_v1 import extract_event_windows

DEFAULT_CASES = ((2025, 1), (2021, 6), (2021, 16))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", nargs="*", default=None, help="YEAR:ROUND cases")
    args = parser.parse_args()
    cases = DEFAULT_CASES if not args.cases else tuple(
        tuple(map(int, item.split(":", 1))) for item in args.cases
    )
    totals = Counter()
    for year, round_number in cases:
        events = extract_event_windows(year, round_number)
        counts = Counter(event.event_type for event in events)
        totals.update(counts)
        print(f"{year} R{round_number}: total={len(events)} SC={counts['SC']} VSC={counts['VSC']} RED_FLAG={counts['RED_FLAG']}")
        for event in events:
            print(f"  {event.event_type}: {event.start_seconds:.3f}s -> {event.end_seconds:.3f}s lap {event.start_lap}->{event.end_lap}")
    print(f"Aggregate: SC={totals['SC']} VSC={totals['VSC']} RED_FLAG={totals['RED_FLAG']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
