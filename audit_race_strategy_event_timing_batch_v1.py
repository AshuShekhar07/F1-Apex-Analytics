"""Batch validation harness for exact F1 race-event timing.

Audit-only. Loads selected historical races through the existing event-timing
extractor and reports whether each requested event type (SC/VSC/red flag) was
observed. It does not write to the database.

The intent is to validate coverage and parser behavior before event windows are
used by the production Monte Carlo simulator.
"""
from __future__ import annotations

import argparse
from collections import Counter

from audit_race_strategy_event_timing_v1 import load_event_timing

DEFAULT_CASES = (
    (2025, 1),   # Australian GP: multiple Safety Car periods
    (2021, 6),   # Azerbaijan GP: race red flag
    (2021, 10),  # British GP: Safety Car/VSC coverage check
    (2023, 8),   # Spanish GP: event-free control case if no coded intervention
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", nargs="*", default=None,
                        help="Race cases as YEAR:ROUND, e.g. 2025:1 2021:6")
    args = parser.parse_args()

    cases = DEFAULT_CASES
    if args.cases:
        parsed = []
        for raw in args.cases:
            try:
                year_text, round_text = raw.split(":", 1)
                parsed.append((int(year_text), int(round_text)))
            except ValueError as exc:
                raise SystemExit(f"Invalid case '{raw}'. Use YEAR:ROUND") from exc
        cases = tuple(parsed)

    totals = Counter()
    print("=== EVENT TIMING BATCH AUDIT V1 ===")
    print("Audit only; database and production simulator are not modified.\n")

    for year, round_number in cases:
        events = load_event_timing(year, round_number)
        counts = Counter(event.event_type for event in events)
        totals.update(counts)
        print(f"{year} R{round_number}: total={len(events)} "
              f"SC={counts.get('SC', 0)} "
              f"VSC={counts.get('VSC', 0)} "
              f"RED_FLAG={counts.get('RED_FLAG', 0)}")
        for event in events:
            print(
                f"  {event.event_type}: "
                f"start={event.start_seconds:.3f}s "
                f"end={event.end_seconds:.3f}s "
                f"lap={event.start_lap}->{event.end_lap}"
            )

    print("\nAggregate observed event windows:")
    print(f"SC={totals.get('SC', 0)} VSC={totals.get('VSC', 0)} "
          f"RED_FLAG={totals.get('RED_FLAG', 0)}")

    missing = [name for name in ("SC", "VSC", "RED_FLAG") if totals.get(name, 0) == 0]
    if missing:
        print("WARNING: no observed windows for: " + ", ".join(missing))
        print("Use --cases with a known example before promoting this extractor.")
    else:
        print("All three event classes were observed across the requested cases.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
