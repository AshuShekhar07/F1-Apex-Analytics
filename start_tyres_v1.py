"""Starting tyres before the start, for the pre-start winning strategy.

Teams fit the starting tyres on the grid before the formation lap. Two sources:
  * OpenF1 /stints: each car's first stint (stint_number 1) of the Grand Prix session;
  * by hand: --set "1=MEDIUM,4=HARD" (car numbers), e.g. from the TV grid graphic.

Nothing is trusted blindly:
  * --check SEASON compares OpenF1's first stint with the compound of each car's lap 1 in our
    database for races already run (no writes), so the source is verified on real data;
  * a fetched set is stored only if it covers >= MIN_COVERAGE of the race field.
NOT YET VERIFIED: whether OpenF1 publishes the first stint BEFORE lights out (it may appear
only once the race starts). Try --race-id on race day before the start; if it is empty, use --set.

    python start_tyres_v1.py --check 2025                 # verify the source (no writes)
    python start_tyres_v1.py --race-id 190                # fetch and store from OpenF1
    python start_tyres_v1.py --race-id 190 --set "1=MEDIUM,4=MEDIUM,16=HARD"
"""

from __future__ import annotations

import argparse
import os
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

TYRES = ("SOFT", "MEDIUM", "HARD", "INTERMEDIATE", "WET")
MIN_COVERAGE = 0.85


def normalise(compound: Any) -> str | None:
    value = str(compound or "").strip().upper()
    return value if value in TYRES else None


def parse_start_tyres(rows: Iterable[dict], entries_by_number: dict[int, int]) -> dict[int, str]:
    """race_entry_id -> starting compound, from OpenF1 stint rows (first stint per car)."""
    out: dict[int, str] = {}
    for row in sorted(rows, key=lambda r: (r.get("stint_number") or 99)):
        try:
            number = int(row.get("driver_number"))
        except (TypeError, ValueError):
            continue
        entry, compound = entries_by_number.get(number), normalise(row.get("compound"))
        if entry is not None and compound and row.get("stint_number") == 1 and entry not in out:
            out[entry] = compound
    return out


def parse_manual(spec: str, entries_by_number: dict[int, int]) -> tuple[dict[int, str], list[str]]:
    """'1=MEDIUM,4=HARD' -> (race_entry_id -> compound, problems)."""
    out, problems = {}, []
    for part in filter(None, (p.strip() for p in spec.split(","))):
        number, _, compound = part.partition("=")
        try:
            entry = entries_by_number.get(int(number))
        except ValueError:
            entry = None
        tyre = normalise(compound)
        if entry is None or tyre is None:
            problems.append(f"cannot read {part!r} (car number in this race = SOFT/MEDIUM/HARD/INTERMEDIATE/WET)")
        else:
            out[entry] = tyre
    return out, problems


def compare(fetched: dict[int, str], actual: dict[int, str]) -> tuple[int, int, int]:
    """(cars in both, cars matching, cars the source is missing)."""
    both = [e for e in actual if e in fetched]
    return len(both), sum(fetched[e] == actual[e] for e in both), len(actual) - len(both)


# --- OpenF1 ------------------------------------------------------------------------------

def fetch_start_tyres(race_date: date, entries_by_number: dict[int, int]) -> tuple[dict[int, str], list[str]]:
    from fetch_starting_grid_v1 import _get, race_session, season_sessions

    race = race_session(season_sessions(race_date.year), race_date)
    if race is None:
        return {}, ["no OpenF1 race session within 3 days of the race date"]
    rows = _get("stints", session_key=race.get("session_key"))
    if not rows:
        return {}, ["OpenF1 has no stints for this race yet"]
    return parse_start_tyres(rows, entries_by_number), []


# --- database ----------------------------------------------------------------------------

def ensure_table(db: Any) -> None:
    db.execute(text((Path(__file__).resolve().parent / "migration_race_start_tyres_v1.sql").read_text()))


def race_entries_by_number(db: Any, race_id: int) -> dict[int, int]:
    return {int(n): int(e) for n, e in db.execute(text("""
        SELECT car_number, id FROM race_entries WHERE race_id = :r AND car_number IS NOT NULL"""), {"r": race_id})}


def lap1_tyres(db: Any, race_id: int) -> dict[int, str]:
    """race_entry_id -> compound on lap 1 of the race (the truth for --check)."""
    out = {}
    for entry, compound in db.execute(text("""
            SELECT l.race_entry_id, l.tire_compound FROM laps l
            JOIN sessions s ON s.id = l.session_id AND s.session_type = 'R'
            WHERE s.race_id = :r AND l.lap_number = 1"""), {"r": race_id}):
        if (tyre := normalise(compound)) is not None:
            out[int(entry)] = tyre
    return out


def load_start_tyres(db: Any, race_id: int) -> dict[int, str]:
    ensure_table(db)
    return {int(e): c for e, c in db.execute(text(
        "SELECT race_entry_id, compound FROM race_start_tyres WHERE race_id = :r"), {"r": race_id})}


def store(db: Any, race_id: int, tyres: dict[int, str], source: str) -> None:
    ensure_table(db)
    for entry, compound in tyres.items():
        db.execute(text("""
            INSERT INTO race_start_tyres (race_id, race_entry_id, compound, source)
            VALUES (:r, :e, :c, :s)
            ON CONFLICT (race_id, race_entry_id) DO UPDATE
            SET compound = EXCLUDED.compound, source = EXCLUDED.source, fetched_at = now()"""),
            {"r": race_id, "e": entry, "c": compound, "s": source})


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--race-id", type=int)
    parser.add_argument("--set", help='by hand: "1=MEDIUM,4=HARD" (car numbers)')
    parser.add_argument("--check", type=int, metavar="SEASON", help="compare OpenF1 with lap 1, no writes")
    args = parser.parse_args()
    engine = create_engine(os.environ["DATABASE_URL"])
    if args.check:
        with engine.connect() as db:
            races = db.execute(text("""
                SELECT r.id, r.round_number, r.race_date FROM races r WHERE r.season_year = :s
                  AND EXISTS (SELECT 1 FROM sessions s JOIN laps l ON l.session_id = s.id
                              WHERE s.race_id = r.id AND s.session_type = 'R')
                ORDER BY r.round_number"""), {"s": args.check}).mappings().all()
            totals = [0, 0, 0]
            for race in races:
                entries = race_entries_by_number(db, race["id"])
                fetched, problems = fetch_start_tyres(race["race_date"], entries)
                both, same, missing = compare(fetched, lap1_tyres(db, race["id"]))
                totals = [totals[0] + both, totals[1] + same, totals[2] + missing]
                note = f" ({'; '.join(problems)})" if problems else ""
                print(f"  R{race['round_number']:<2} race {race['id']}: {same}/{both} match, {missing} missing{note}")
        print(f"\nOpenF1 first stint vs lap-1 tyre, {args.check}: {totals[1]}/{totals[0]} match, "
              f"{totals[2]} cars missing from OpenF1")
        return 0
    if not args.race_id:
        parser.error("give --race-id (with or without --set) or --check")
    with engine.begin() as db:
        race = db.execute(text("SELECT id, race_date FROM races WHERE id = :r"), {"r": args.race_id}).mappings().first()
        if race is None:
            parser.error(f"race {args.race_id} not found")
        entries = race_entries_by_number(db, args.race_id)
        if args.set:
            tyres, problems = parse_manual(args.set, entries)
            source = "manual"
        else:
            tyres, problems = fetch_start_tyres(race["race_date"], entries)
            source = "openf1"
            if entries and len(tyres) < MIN_COVERAGE * len(entries):
                problems.append(f"OpenF1 covers {len(tyres)} of {len(entries)} cars: not stored")
                tyres = {}
        for p in problems:
            print(f"  {p}")
        if tyres:
            store(db, args.race_id, tyres, source)
        print(f"race {args.race_id}: stored {len(tyres)} starting tyres ({source})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
