"""Refill missing Q1/Q2/Q3 times from FastF1 for races whose qualifying rows have none.

Found by the forecast batch: 2025 Miami (race 155) and Imola (race 156) have 20
qualifying rows each but no lap times, so no forecast or backtest can use them.

Safety:
  * only NULL time columns are filled; existing times and positions are never changed
  * drivers are matched by car number within the race (race_entries.car_number)
  * dry run by default; --apply writes inside one transaction

    python fix_missing_quali_times_v1.py                    # report every race with rows but no times
    python fix_missing_quali_times_v1.py --race-id 155 --race-id 156 --apply
"""

from __future__ import annotations

import argparse
import os
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from fastf1_cache import fastf1_cache_dir

MISSING_SQL = """
    SELECT r.id, r.season_year, r.round_number, t.name AS track_name,
           COUNT(q.id) AS rows, COUNT(COALESCE(q.q1_time, q.q2_time, q.q3_time)) AS timed
    FROM races r
    JOIN tracks t ON t.id = r.track_id
    JOIN sessions s ON s.race_id = r.id AND s.session_type = 'Q'
    JOIN qualifying_results q ON q.session_id = s.id
    GROUP BY r.id, t.name
    HAVING COUNT(q.id) > 0 AND COUNT(COALESCE(q.q1_time, q.q2_time, q.q3_time)) = 0
    ORDER BY r.season_year, r.round_number
"""


def _seconds(value: Any) -> float | None:
    try:
        if value is None or value != value:     # None or NaN/NaT
            return None
        return round(value.total_seconds(), 3) if hasattr(value, "total_seconds") else float(value)
    except (TypeError, ValueError):
        return None


def planned_updates(results: list[dict], entries: dict[int, int]) -> tuple[list[dict], list[int]]:
    """(updates keyed by race_entry_id, car numbers FastF1 had that the race does not)."""
    updates, unmatched = [], []
    for row in results:
        try:
            number = int(float(row.get("DriverNumber")))
        except (TypeError, ValueError):
            continue
        entry = entries.get(number)
        if entry is None:
            unmatched.append(number)
            continue
        times = {k: _seconds(row.get(k.upper())) for k in ("q1", "q2", "q3")}
        if any(v is not None for v in times.values()):
            updates.append({"entry": entry, **times})
    return updates, unmatched


def apply_updates(conn, session_id: int, updates: list[dict]) -> int:
    changed = 0
    for u in updates:
        changed += conn.execute(text("""
            UPDATE qualifying_results
            SET q1_time = COALESCE(q1_time, :q1),
                q2_time = COALESCE(q2_time, :q2),
                q3_time = COALESCE(q3_time, :q3)
            WHERE session_id = :s AND race_entry_id = :entry
              AND (q1_time IS NULL OR q2_time IS NULL OR q3_time IS NULL)
        """), {"s": session_id, **u}).rowcount
    return changed


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--race-id", type=int, action="append", default=[])
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as conn:
        missing = conn.execute(text(MISSING_SQL)).mappings().all()
    print(f"races with qualifying rows but no times: {len(missing)}")
    for m in missing:
        print(f"  race {m['id']}: {m['season_year']} R{m['round_number']} {m['track_name']} ({m['rows']} rows)")
    targets = [m for m in missing if m["id"] in args.race_id] if args.race_id else []
    if not targets:
        print("Pass --race-id for the races to repair (and --apply to write).")
        return 0

    import fastf1
    fastf1.Cache.enable_cache(fastf1_cache_dir())
    from backfill_fastf1_enrichment_v1 import entry_map

    with engine.begin() as conn:
        for m in targets:
            session_id = conn.execute(text(
                "SELECT id FROM sessions WHERE race_id = :r AND session_type = 'Q'"), {"r": m["id"]}).scalar()
            session = fastf1.get_session(m["season_year"], m["round_number"], "Qualifying")
            session.load(laps=False, telemetry=False, weather=False, messages=False)
            updates, unmatched = planned_updates(session.results.to_dict("records"), entry_map(conn, m["id"]))
            print(f"race {m['id']} {m['track_name']}: FastF1 times for {len(updates)} drivers"
                  + (f"; car numbers not in this race: {unmatched}" if unmatched else ""))
            if args.apply:
                print(f"  updated {apply_updates(conn, session_id, updates)} rows")
        if not args.apply:
            conn.rollback()
            print("Dry run -- re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
