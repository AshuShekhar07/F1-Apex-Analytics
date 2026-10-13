"""Fill in race and sprint points that are missing (NULL or NaN) with the official points.

FastF1 gives no points (NaN) when a race or sprint is loaded before the official results
service (Jolpica, the Ergast successor) has published them, and the loaders stored that NaN
(2026 R17 Marina Bay sprint). A NaN poisons every sum it touches -- the website's points
tables and the title simulation's current points (COALESCE does not catch NaN) -- so:

- each missing value is filled from Jolpica's classification of that race or sprint,
  matched by car number; points are never computed or guessed;
- while Jolpica has nothing yet, a NaN becomes NULL (missing), which the sums and the title
  simulation read as 0, and the next pipeline run tries again.

Never fails the pipeline: when Jolpica cannot be reached it says so and exits 0.
Dry run unless --apply.

    python repair_missing_points_v1.py --season 2026
    python repair_missing_points_v1.py --season 2026 --apply
"""

from __future__ import annotations

import argparse
import os
import time
from typing import Any, Callable

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

JOLPICA = "https://api.jolpi.ca/ergast/f1"

MISSING_SQL = """
    SELECT rr.id, s.session_type, r.round_number, t.name AS track, re.car_number, d.name AS driver,
           rr.finishing_position, rr.points = 'NaN' AS is_nan
    FROM race_results rr
    JOIN sessions s ON s.id = rr.session_id AND s.session_type IN ('R', 'S')
    JOIN races r ON r.id = s.race_id
    JOIN tracks t ON t.id = r.track_id
    JOIN race_entries re ON re.id = rr.race_entry_id
    JOIN drivers d ON d.id = re.driver_id
    WHERE r.season_year = :y AND (rr.points IS NULL OR rr.points = 'NaN')
    ORDER BY r.round_number, s.session_type, rr.finishing_position NULLS LAST
"""


def missing_points(db: Any, season: int) -> list[dict]:
    """Race and sprint results of the season whose points are NULL or NaN."""
    return [dict(r) for r in db.execute(text(MISSING_SQL), {"y": season}).mappings()]


def parse_points(payload: dict, sprint: bool) -> dict[str, float] | None:
    """car number -> official points from a Jolpica results/sprint response; None when the
    classification is not published yet."""
    races = payload.get("MRData", {}).get("RaceTable", {}).get("Races", [])
    if not races:
        return None
    rows = races[0].get("SprintResults" if sprint else "Results") or []
    points = {str(row["number"]): float(row["points"]) for row in rows
              if row.get("number") is not None and row.get("points") not in (None, "")}
    return points or None


def _get(url: str) -> dict:
    import requests

    for attempt in range(3):
        response = requests.get(url, timeout=20)
        if response.status_code == 429 and attempt < 2:        # Jolpica's rate limit: wait and retry
            time.sleep(5 * (attempt + 1))
            continue
        response.raise_for_status()
        return response.json()
    raise RuntimeError("rate limited")


def official_points(season: int, rnd: int, session_type: str,
                    fetch: Callable[[str], dict] = _get) -> dict[str, float] | None:
    sprint = session_type == "S"
    url = f"{JOLPICA}/{season}/{rnd}/{'sprint' if sprint else 'results'}.json?limit=100"
    return parse_points(fetch(url), sprint)


def plan(rows: list[dict], season: int, fetch: Callable[[str], dict] = _get) -> tuple[list[dict], list[str]]:
    """The updates to make, as {id, pts} (pts None = NaN cleared to NULL), and a report."""
    updates, report, cache = [], [], {}
    for row in rows:
        key = (row["round_number"], row["session_type"])
        if key not in cache:
            try:
                cache[key] = official_points(season, *key, fetch=fetch)
            except Exception as exc:                            # offline, Jolpica down
                cache[key] = exc
            time.sleep(0.3 if fetch is _get else 0)             # Jolpica allows a few calls a second
        source = cache[key]
        kind = "sprint" if row["session_type"] == "S" else "race"
        label = f"R{row['round_number']} {row['track']} {kind}: {row['driver']} (#{row['car_number']}, P{row['finishing_position']})"
        found = None if isinstance(source, Exception) or source is None else source.get(str(row["car_number"]))
        if found is not None:
            updates.append({"id": row["id"], "pts": found})
            report.append(f"  {label}: {found:g} pts (official)")
            continue
        why = (f"Jolpica unreachable ({type(source).__name__})" if isinstance(source, Exception)
               else "not published by Jolpica yet" if source is None else "not in Jolpica's classification")
        if row["is_nan"]:
            updates.append({"id": row["id"], "pts": None})
        report.append(f"  {label}: {why}; {'NaN cleared to missing' if row['is_nan'] else 'left missing'}, "
                      "tried again next run")
    return updates, report


def apply(db: Any, updates: list[dict]) -> None:
    for u in updates:
        db.execute(text("UPDATE race_results SET points = :pts WHERE id = :id"), u)


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as db:
        rows = missing_points(db, args.season)
    if not rows:
        print(f"{args.season}: every race and sprint result has its points")
        return 0
    updates, report = plan(rows, args.season)
    print("\n".join(report))
    filled = sum(u["pts"] is not None for u in updates)
    if args.apply and updates:
        with engine.begin() as db:
            apply(db, updates)
        print(f"{filled} point value(s) filled, {len(updates) - filled} NaN cleared to missing")
    elif updates:
        print(f"dry run: {filled} to fill, {len(updates) - filled} NaN to clear (add --apply)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
