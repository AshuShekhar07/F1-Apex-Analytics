"""Fill in race and sprint results missing for drivers who took part in the weekend.

Found by production_check_v1: 2025 R2 (Shanghai) has no race result for Kimi Antonelli (he
finished 6th), so his and Mercedes' points were 8 short and the title simulation ran
Mercedes on one car. Loaders skip a session that already has results, so a driver missed
on the first load stays missing.

For every race/sprint session of the season, a race driver who qualified (or has a result
in another session of the weekend) but has no result in it is looked up in FastF1's
classification by car number and inserted -- only when FastF1 lists them, so a real
withdrawal (2025 R9 Stroll) is reported, not invented. Never touches existing results.
Dry run unless --apply.

    python repair_missing_race_results_v1.py --season 2025
    python repair_missing_race_results_v1.py --season 2025 --apply
"""

from __future__ import annotations

import argparse
import os
from typing import Any

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

STATUS_LENGTH = 20          # race_results.status is VARCHAR(20)


def missing_results(db: Any, season: int) -> list[dict]:
    """(session, entry) pairs: a race driver with a part in the weekend but no result in a
    race or sprint session that exists."""
    return [dict(r) for r in db.execute(text("""
        SELECT s.id AS session_id, s.session_type, r.id AS race_id, r.round_number, t.name AS track,
               re.id AS entry_id, re.car_number, d.name AS driver
        FROM sessions s
        JOIN races r ON r.id = s.race_id
        JOIN tracks t ON t.id = r.track_id
        JOIN race_entries re ON re.race_id = r.id AND re.role = 'race_driver'
        JOIN drivers d ON d.id = re.driver_id
        WHERE r.season_year = :y AND s.session_type IN ('R', 'S')
          AND EXISTS (SELECT 1 FROM race_results x WHERE x.session_id = s.id)          -- session was loaded
          AND NOT EXISTS (SELECT 1 FROM race_results x WHERE x.session_id = s.id AND x.race_entry_id = re.id)
          AND (EXISTS (SELECT 1 FROM qualifying_results q WHERE q.race_entry_id = re.id)
               OR EXISTS (SELECT 1 FROM race_results x WHERE x.race_entry_id = re.id))
        ORDER BY r.round_number, s.session_type, d.name"""), {"y": season}).mappings()]


def _seconds(value) -> float | None:
    return None if pd.isna(value) else float(value.total_seconds())


def result_row(results: pd.DataFrame, car_number: int | None, session_id: int, entry_id: int) -> dict | None:
    """The FastF1 classification row for this car as a race_results insert, or None."""
    if car_number is None:
        return None
    match = results[results["DriverNumber"].astype(str) == str(car_number)]
    if match.empty:
        return None
    row = match.iloc[0]
    position = int(row["Position"]) if pd.notna(row.get("Position")) else None
    gap = 0.0 if position == 1 else _seconds(row.get("Time"))
    status = str(row.get("Status")) if pd.notna(row.get("Status")) else None
    grid = int(row["GridPosition"]) if pd.notna(row.get("GridPosition")) else None
    return {"s": session_id, "e": entry_id, "p": position,
            "pts": float(row["Points"]) if pd.notna(row.get("Points")) else 0.0,
            "st": status[:STATUS_LENGTH] if status else None,
            "g": grid, "gs": gap,
            "gd": ("Winner" if position == 1 else (f"+{gap:.3f}s" if gap is not None else status) or "")[:STATUS_LENGTH]
            or None}


INSERT_SQL = """
    INSERT INTO race_results (session_id, race_entry_id, finishing_position, points, status,
                              starting_grid_position, gap_to_winner_seconds, gap_to_winner_display)
    SELECT :s, :e, :p, :pts, :st, :g, :gs, :gd
    WHERE NOT EXISTS (SELECT 1 FROM race_results WHERE session_id = :s AND race_entry_id = :e)
"""


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
        gaps = missing_results(db, args.season)
    if not gaps:
        print(f"{args.season}: every race driver who took part has a result in every race and sprint")
        return 0
    loaded: dict[tuple[int, str], pd.DataFrame | None] = {}
    rows = []
    for g in gaps:
        key = (g["round_number"], g["session_type"])
        if key not in loaded:
            try:
                session = fastf1.get_session(args.season, g["round_number"], g["session_type"])
                session.load(laps=False, telemetry=False, weather=False, messages=False)
                loaded[key] = session.results
            except Exception as exc:
                print(f"  R{key[0]} {key[1]}: FastF1 unavailable ({exc})")
                loaded[key] = None
        kind = "sprint" if g["session_type"] == "S" else "race"
        label = f"R{g['round_number']} {g['track']} {kind}: {g['driver']} (#{g['car_number']})"
        row = result_row(loaded[key], g["car_number"], g["session_id"], g["entry_id"]) \
            if loaded[key] is not None else None
        if row is None:
            print(f"  {label}: not in FastF1's classification -- left as is (withdrew / did not take part?)")
            continue
        rows.append(row)
        print(f"  {label}: P{row['p']} from grid {row['g']}, {row['pts']:g} pts, {row['st']}"
              + ("" if args.apply else "  (dry run)"))
    if args.apply and rows:
        with engine.begin() as db:
            for row in rows:
                db.execute(text(INSERT_SQL), row)
        print(f"inserted {len(rows)} result(s). Next: python repair_session_laps_v1.py --season {args.season} --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
