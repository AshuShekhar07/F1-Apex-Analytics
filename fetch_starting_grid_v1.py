"""Provisional starting grid before the race (grid penalties applied), from OpenF1.

The official grid (race_results.starting_grid_position) only arrives with the race results,
so a forecast made on race day used the qualifying order and missed every grid penalty and
pit-lane start (12-45 per season). OpenF1 publishes the starting grid before the race.

Nothing here is trusted blindly:
  * the OpenF1 race session is matched by date (within 3 days of the DB race date);
  * grid rows are matched to race entries by car number; a grid is rejected unless it covers
    >= MIN_COVERAGE of the qualified field with unique positions;
  * --check compares OpenF1's grid with the OFFICIAL grid for races already run, so the source
    is verified on real data before any forecast uses it.

    python fetch_starting_grid_v1.py --check 2025          # verify the source (no writes)
    python fetch_starting_grid_v1.py --season 2026         # store grids for upcoming races
    python fetch_starting_grid_v1.py --race-id 189 --dry-run
"""

from __future__ import annotations

import argparse
import os
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

BASE_URL = "https://api.openf1.org/v1"
MIN_COVERAGE = 0.85      # share of the qualified field the grid must place
DATE_TOLERANCE_DAYS = 3


# --- pure helpers (tested) -------------------------------------------------------

def _session_date(s: dict) -> date | None:
    raw = s.get("date_start")
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).date() if raw else None
    except ValueError:
        return None


def race_session(sessions: Iterable[dict], race_date: date) -> dict | None:
    """The OpenF1 Grand Prix session (not the sprint) closest to race_date, within tolerance."""
    races = [s for s in sessions if str(s.get("session_name", "")).lower() == "race"
             and (d := _session_date(s)) is not None and abs((d - race_date).days) <= DATE_TOLERANCE_DAYS]
    return min(races, key=lambda s: abs((_session_date(s) - race_date).days)) if races else None


def pick_grid_rows(rows: Iterable[dict], sessions: Iterable[dict], race_key: Any) -> list[dict]:
    """starting_grid rows for the Grand Prix: those keyed to the race session, else to the
    (non-sprint) qualifying session that set it; never the sprint's grid."""
    by_session = defaultdict(list)
    for r in rows:
        by_session[r.get("session_key")].append(r)
    if race_key in by_session:
        return by_session[race_key]
    names = {s.get("session_key"): str(s.get("session_name", "")).lower() for s in sessions}
    quali = [k for k in by_session if names.get(k) == "qualifying"]
    return by_session[quali[0]] if len(quali) == 1 else []


def parse_grid(rows: Iterable[dict], entries_by_number: dict[int, int], field_size: int) -> tuple[dict[int, int], list[str]]:
    """(race_entry_id -> grid position, issues). Empty dict when the grid is not usable."""
    grid, issues, positions = {}, [], set()
    for r in rows:
        try:
            number, position = int(r["driver_number"]), int(r["position"])
        except (KeyError, TypeError, ValueError):
            continue
        entry = entries_by_number.get(number)
        if entry is None:
            issues.append(f"car #{number} not entered in this race")
            continue
        if position in positions or position < 1:
            return {}, [f"position {position} listed twice or invalid"]
        positions.add(position)
        grid[entry] = position
    if field_size and len(grid) < MIN_COVERAGE * field_size:
        return {}, issues + [f"grid places only {len(grid)}/{field_size} qualified drivers"]
    return grid, issues


# --- OpenF1 ------------------------------------------------------------------------

def _get(path: str, **params) -> list[dict]:
    import requests

    for attempt in range(3):
        try:
            resp = requests.get(f"{BASE_URL}/{path}", params=params, timeout=20)
            if resp.status_code == 404:
                return []
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else []
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
    return []


def fetch_grid(race_date: date, entries_by_number: dict[int, int], field_size: int) -> tuple[dict[int, int], list[str]]:
    sessions = _get("sessions", year=race_date.year)
    race = race_session(sessions, race_date)
    if race is None:
        return {}, ["no OpenF1 race session within 3 days of the race date"]
    meeting = [s for s in sessions if s.get("meeting_key") == race.get("meeting_key")]
    rows = _get("starting_grid", meeting_key=race.get("meeting_key"))
    rows = pick_grid_rows(rows, meeting, race.get("session_key"))
    if not rows:
        return {}, ["OpenF1 has no starting grid for this race yet"]
    return parse_grid(rows, entries_by_number, field_size)


# --- database ------------------------------------------------------------------------

def race_field(db: Any, race_id: int) -> tuple[dict[int, int], int]:
    """(car number -> race_entry_id for qualified race drivers, number qualified)."""
    rows = db.execute(text("""
        SELECT re.car_number, re.id FROM qualifying_results q
        JOIN sessions s ON s.id = q.session_id AND s.session_type = 'Q'
        JOIN race_entries re ON re.id = q.race_entry_id
        WHERE s.race_id = :r AND re.car_number IS NOT NULL
    """), {"r": race_id}).all()
    return {int(n): int(e) for n, e in rows}, len(rows)


def official_grid(db: Any, race_id: int) -> dict[int, int]:
    return {int(e): int(g) for e, g in db.execute(text("""
        SELECT rr.race_entry_id, rr.starting_grid_position FROM race_results rr
        JOIN sessions s ON s.id = rr.session_id AND s.session_type = 'R'
        WHERE s.race_id = :r AND rr.starting_grid_position > 0
    """), {"r": race_id})}


def store(db: Any, race_id: int, grid: dict[int, int]) -> None:
    db.execute(text("DELETE FROM race_provisional_grid WHERE race_id = :r"), {"r": race_id})
    for entry, position in grid.items():
        db.execute(text("""INSERT INTO race_provisional_grid (race_id, race_entry_id, grid_position)
                           VALUES (:r, :e, :p)"""), {"r": race_id, "e": entry, "p": position})


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--season", type=int, help="store grids for this season's upcoming races")
    parser.add_argument("--race-id", type=int)
    parser.add_argument("--check", type=int, metavar="SEASON", help="compare with the official grid, no writes")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.begin() as db:
        db.execute(text(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          "migration_provisional_grid_v1.sql")).read()))

    with engine.connect() as db:
        if args.check:
            races = db.execute(text("""
                SELECT r.id, r.round_number, r.race_date FROM races r WHERE r.season_year = :s
                  AND EXISTS (SELECT 1 FROM race_results rr JOIN sessions s ON s.id = rr.session_id
                              WHERE s.race_id = r.id AND s.session_type = 'R' AND rr.starting_grid_position > 0)
                ORDER BY r.round_number"""), {"s": args.check}).mappings().all()
        elif args.race_id:
            races = db.execute(text("SELECT id, round_number, race_date FROM races WHERE id = :r"),
                               {"r": args.race_id}).mappings().all()
        elif args.season:
            today = date.today()
            races = db.execute(text("""
                SELECT r.id, r.round_number, r.race_date FROM races r WHERE r.season_year = :s
                  AND r.race_date BETWEEN :a AND :b
                  AND EXISTS (SELECT 1 FROM qualifying_results q JOIN sessions s ON s.id = q.session_id
                              WHERE s.race_id = r.id AND s.session_type = 'Q')
                  AND NOT EXISTS (SELECT 1 FROM race_results rr JOIN sessions s ON s.id = rr.session_id
                                  WHERE s.race_id = r.id AND s.session_type = 'R')
                ORDER BY r.round_number"""),
                {"s": args.season, "a": today - timedelta(days=1), "b": today + timedelta(days=7)}).mappings().all()
        else:
            parser.error("give --season, --race-id or --check")

    checked = exact = drivers = 0
    for race in races:
        with engine.connect() as db:
            entries, field = race_field(db, race["id"])
            official = official_grid(db, race["id"]) if args.check else {}
        try:
            grid, issues = fetch_grid(race["race_date"], entries, field)
        except Exception as exc:
            print(f"R{race['round_number']} (race {race['id']}): OpenF1 unavailable ({type(exc).__name__}: {exc})")
            continue
        if not grid:
            print(f"R{race['round_number']} (race {race['id']}): not used -- {'; '.join(issues)}")
            continue
        if args.check:
            same = sum(1 for e, p in grid.items() if official.get(e) == p)
            both = sum(1 for e in grid if e in official)
            checked += 1
            exact += int(same == both == len(official))
            drivers += both
            print(f"R{race['round_number']} (race {race['id']}): {same}/{both} positions match the official grid"
                  + ("" if same == both else "  <- differs"))
            continue
        extra = f" ({'; '.join(issues)})" if issues else ""
        if args.dry_run:
            print(f"R{race['round_number']} (race {race['id']}): {len(grid)} grid slots{extra} (dry run)")
            continue
        with engine.begin() as db:
            store(db, race["id"], grid)
        print(f"R{race['round_number']} (race {race['id']}): stored provisional grid, {len(grid)} slots{extra}")
    if args.check:
        print(f"\nCHECK {args.check}: {exact}/{checked} races matched the official grid exactly "
              f"({drivers} drivers compared, {len(races)} races with an official grid)")
    elif not races:
        print("no upcoming race with qualifying and without results in the next week")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
