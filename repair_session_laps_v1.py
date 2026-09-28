"""Fill in laps for drivers that have results but no laps in a race session.

Found on 2026 R12 (Zandvoort): results for 22 drivers, race laps for only 4 -- the
session was loaded while FastF1 was incomplete, and loaders skip sessions that exist.

For every race session of the season where fewer than all classified-or-retired drivers
(did-not-start excluded) have laps, FastF1's laps are matched to race entries by car
number and inserted ONLY for entries that have no laps in that session, so re-running
never duplicates. Dry run unless --apply.

    python repair_session_laps_v1.py --season 2026
    python repair_session_laps_v1.py --season 2026 --apply
"""

from __future__ import annotations

import argparse
import os
from typing import Any

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

NOT_STARTED = ("did not start", "withdrew", "did not qualify", "excluded")


def started(status: str | None) -> bool:
    return (status or "").strip().lower() not in NOT_STARTED


def _seconds(value) -> float | None:
    return value.total_seconds() if value is not None and pd.notna(value) else None


def incomplete_sessions(db: Any, season: int) -> list[dict]:
    rows = db.execute(text("""
        SELECT s.id AS session_id, r.id AS race_id, r.round_number, r.race_date, t.name AS track,
               COUNT(DISTINCT rr.id) FILTER (WHERE NOT (lower(COALESCE(rr.status, '')) = ANY(:ns))) AS starters,
               COUNT(DISTINCT l.race_entry_id) AS with_laps
        FROM races r
        JOIN tracks t ON t.id = r.track_id
        JOIN sessions s ON s.race_id = r.id AND s.session_type = 'R'
        JOIN race_results rr ON rr.session_id = s.id
        LEFT JOIN laps l ON l.session_id = s.id AND l.race_entry_id = rr.race_entry_id
        WHERE r.season_year = :y
        GROUP BY s.id, r.id, r.round_number, r.race_date, t.name
        ORDER BY r.round_number
    """), {"y": season, "ns": list(NOT_STARTED)}).mappings().all()
    return [dict(r) for r in rows if r["with_laps"] < r["starters"]]


def missing_entries(db: Any, session_id: int, race_id: int) -> dict[int, int]:
    """car number -> race_entry_id for entries with a result but no laps in the session."""
    return {int(n): int(e) for n, e in db.execute(text("""
        SELECT re.car_number, re.id FROM race_entries re
        JOIN race_results rr ON rr.race_entry_id = re.id AND rr.session_id = :s
        WHERE re.race_id = :r AND re.car_number IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM laps l WHERE l.session_id = :s AND l.race_entry_id = re.id)
    """), {"s": session_id, "r": race_id})}


def lap_rows(laps: pd.DataFrame, entries: dict[int, int], session_id: int) -> list[dict]:
    """FastF1 laps -> rows for the entries we are filling (matched by car number)."""
    out = []
    for _, lap in laps.iterrows():
        try:
            number = int(lap["DriverNumber"])
        except (TypeError, ValueError):
            continue
        if number not in entries or pd.isna(lap.get("LapNumber")):
            continue
        out.append({"s": session_id, "e": entries[number], "n": int(lap["LapNumber"]),
                    "lt": _seconds(lap.get("LapTime")), "s1": _seconds(lap.get("Sector1Time")),
                    "s2": _seconds(lap.get("Sector2Time")), "s3": _seconds(lap.get("Sector3Time")),
                    "c": lap.get("Compound") if pd.notna(lap.get("Compound")) else None})
    return out


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    import fastf1

    from fastf1_cache import fastf1_cache_dir

    fastf1.Cache.enable_cache(fastf1_cache_dir())
    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as db:
        sessions = incomplete_sessions(db, args.season)
    if not sessions:
        print(f"{args.season}: every race session has laps for every starter")
        return 0
    for s in sessions:
        print(f"R{s['round_number']} {s['track']}: laps for {s['with_laps']}/{s['starters']} starters")
        with engine.connect() as db:
            entries = missing_entries(db, s["session_id"], s["race_id"])
        try:
            session = fastf1.get_session(args.season, s["round_number"], "R")
            session.load(telemetry=False, weather=False, messages=False)
            rows = lap_rows(session.laps, entries, s["session_id"])
        except Exception as exc:
            print(f"  FastF1 unavailable: {exc}")
            continue
        found = {r["e"] for r in rows}
        print(f"  {len(rows)} laps for {len(found)}/{len(entries)} drivers without laps"
              + ("" if args.apply else "  (dry run)"))
        if args.apply and rows:
            with engine.begin() as db:
                db.execute(text("""
                    INSERT INTO laps (session_id, race_entry_id, lap_number, lap_time,
                                      sector_1_time, sector_2_time, sector_3_time, tire_compound)
                    VALUES (:s, :e, :n, :lt, :s1, :s2, :s3, :c)
                """), rows)
            print("  inserted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
