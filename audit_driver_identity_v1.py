"""Audit (and optionally fix) which driver each race entry belongs to, against FastF1.

Found in 2026 race 185: car #3 stored as Daniel Ricciardo and car #5 as Sebastian
Vettel -- their OLD permanent numbers, now used by other drivers. The laps and
results on those entries are the right car's data; only the driver identity is wrong.

For every race in range, FastF1's official results for that event (race session,
else qualifying) give car number -> full name. Each DB entry is compared by car
number:

  ok        DB driver name == FastF1 name
  mismatch  different name -> fix: point the entry at the correct driver
            (existing drivers row by exact name; created only with --apply)
  conflict  the correct driver already has another entry in that race -> reported,
            never changed automatically
  missing   a FastF1 car number with no DB entry -> reported only

Dry run by default; --apply writes the fixes in one transaction per race. Only
race_entries.driver_id changes, so laps/results stay on the same entry. Stored
forecasts for fixed races should be recomputed afterwards.

    python audit_driver_identity_v1.py --start-year 2026 --end-year 2026
    python audit_driver_identity_v1.py --start-year 2026 --end-year 2026 --apply
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Iterable

from dotenv import load_dotenv
from sqlalchemy import create_engine, text


@dataclass(frozen=True)
class EntryCheck:
    race_id: int
    entry_id: int | None
    car_number: int
    db_driver: str | None
    fastf1_driver: str | None
    status: str                  # ok | mismatch | conflict | missing | unknown_number


def _norm(name: str | None) -> str:
    return " ".join((name or "").split()).casefold()


def compare(race_id: int, db_entries: Iterable[tuple[int, int, str]], official: dict[int, str]) -> list[EntryCheck]:
    """db_entries: (entry_id, car_number, driver name); official: car number -> FastF1 full name."""
    db_entries = list(db_entries)
    names_in_race = {_norm(name) for _, _, name in db_entries}
    checks = []
    for entry_id, number, name in sorted(db_entries, key=lambda e: e[1]):
        truth = official.get(number)
        if truth is None:
            status = "unknown_number"
        elif _norm(truth) == _norm(name):
            status = "ok"
        elif _norm(truth) in names_in_race:
            status = "conflict"
        else:
            status = "mismatch"
        checks.append(EntryCheck(race_id, entry_id, number, name, truth, status))
    db_numbers = {number for _, number, _ in db_entries}
    for number, truth in sorted(official.items()):
        if number not in db_numbers:
            checks.append(EntryCheck(race_id, None, number, None, truth, "missing"))
    return checks


def official_drivers(year: int, round_number: int, race_date: date) -> dict[int, str]:
    import fastf1

    from fastf1_cache import fastf1_cache_dir

    fastf1.Cache.enable_cache(fastf1_cache_dir())
    for identifier in ("R", "Q"):
        try:
            session = fastf1.get_session(year, round_number, identifier)
            event_date = session.event["EventDate"].date()
            if abs(event_date - race_date) > timedelta(days=3):
                raise RuntimeError(f"FastF1 round {round_number} is {event_date}, DB race date is {race_date}")
            session.load(laps=False, telemetry=False, weather=False, messages=False)
        except RuntimeError:
            raise
        except Exception:
            continue
        results = session.results
        if results is None or results.empty:
            continue
        out = {}
        for _, row in results.iterrows():
            try:
                number = int(row["DriverNumber"])
            except (TypeError, ValueError):
                continue
            name = str(row.get("FullName") or "").strip()
            if name and name.lower() not in ("nan", "none", "nan nan", "none none"):
                out[number] = name
        if out:
            return out
    return {}


def apply_fixes(db: Any, checks: Iterable[EntryCheck]) -> int:
    fixed = 0
    for c in checks:
        if c.status != "mismatch":
            continue
        driver_id = db.execute(text("SELECT id FROM drivers WHERE name = :n"), {"n": c.fastf1_driver}).scalar()
        if driver_id is None:
            driver_id = db.execute(text(
                "INSERT INTO drivers (name, permanent_number) VALUES (:n, :num) RETURNING id"
            ), {"n": c.fastf1_driver, "num": c.car_number}).scalar()
        db.execute(text("UPDATE race_entries SET driver_id = :d WHERE id = :e"), {"d": driver_id, "e": c.entry_id})
        fixed += 1
    return fixed


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start-year", type=int, default=2026)
    parser.add_argument("--end-year", type=int, default=2026)
    parser.add_argument("--race-id", type=int)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as db:
        races = db.execute(text("""
            SELECT id, season_year, round_number, race_date FROM races
            WHERE season_year BETWEEN :a AND :b AND (CAST(:r AS INTEGER) IS NULL OR id = :r)
              AND EXISTS (SELECT 1 FROM race_entries re WHERE re.race_id = races.id)
            ORDER BY race_date
        """), {"a": args.start_year, "b": args.end_year, "r": args.race_id}).mappings().all()

    totals = {"ok": 0, "mismatch": 0, "conflict": 0, "missing": 0, "unknown_number": 0}
    fixed_races = []
    for race in races:
        try:
            official = official_drivers(race["season_year"], race["round_number"], race["race_date"])
        except RuntimeError as exc:
            print(f"race {race['id']}: SKIPPED ({exc})")
            continue
        if not official:
            print(f"race {race['id']}: SKIPPED (no FastF1 results)")
            continue
        with engine.begin() as db:
            entries = db.execute(text("""
                SELECT re.id, re.car_number, d.name FROM race_entries re JOIN drivers d ON d.id = re.driver_id
                WHERE re.race_id = :r AND re.car_number IS NOT NULL
            """), {"r": race["id"]}).all()
            checks = compare(race["id"], entries, official)
            for c in checks:
                totals[c.status] += 1
                if c.status != "ok":
                    print(f"race {c.race_id} #{c.car_number:<3} {c.status:15} DB: {c.db_driver or '-':22} "
                          f"FastF1: {c.fastf1_driver or '-'}")
            if args.apply and any(c.status == "mismatch" for c in checks):
                n = apply_fixes(db, checks)
                fixed_races.append(race["id"])
                print(f"race {race['id']}: fixed {n} entries")
        print(f"race {race['id']} ({race['race_date']}): checked {len(checks)} cars", flush=True)

    print("\nSUMMARY", totals)
    if not args.apply and totals["mismatch"]:
        print("Dry run: nothing changed. Re-run with --apply to fix the mismatches.")
    if fixed_races:
        print("Recompute forecasts for:", " ".join(map(str, fixed_races)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
