"""Merge drivers stored twice under different spellings ("Sergio Perez" / "Sergio Pérez").

Found by data_quality_v1: 2019 Suzuka had car 11 and car 27 each used by two drivers --
the same people under accented and plain names, created when FastF1 changed spelling.

A group = drivers whose names match ignoring accents, case and spacing
(driver_names.normalise_name). The lowest id is kept (the original spelling). For each
duplicate driver:

  race entry in a race where the kept driver has NO entry -> entry moved to the kept driver
  race entry in a race where the kept driver HAS an entry -> child rows (laps, results,
    qualifying, ...) moved to the kept entry for sessions where it has none of that table;
    rows it already has are kept and the duplicate's copies deleted; then the duplicate
    entry is deleted
  other tables pointing at drivers.id                     -> repointed, or deleted when the
                                                             kept driver already has that row
  the duplicate driver row                                -> deleted

Every referencing table/column is discovered from the database catalogue, so no table is
missed. Dry run by default; --apply runs everything in one transaction.

    python merge_duplicate_drivers_v1.py
    python merge_duplicate_drivers_v1.py --apply
"""

from __future__ import annotations

import argparse
import os
from collections import defaultdict
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from driver_names import normalise_name

REFERENCES_SQL = """
    SELECT c.conrelid::regclass::text AS table_name, a.attname AS column_name
    FROM pg_constraint c
    JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
    WHERE c.contype = 'f' AND c.confrelid = CAST(:target AS regclass)
    UNION
    SELECT table_name, column_name FROM information_schema.columns
    WHERE table_schema = current_schema() AND column_name = :column
"""


def duplicate_groups(db: Any) -> list[list[tuple[int, str]]]:
    by_key = defaultdict(list)
    for driver_id, name in db.execute(text("SELECT id, name FROM drivers ORDER BY id")):
        by_key[normalise_name(name)].append((int(driver_id), name))
    return [group for key, group in by_key.items() if key and len(group) > 1]


def referencing(db: Any, target: str, column: str) -> list[tuple[str, str]]:
    rows = db.execute(text(REFERENCES_SQL), {"target": target, "column": column}).all()
    return sorted({(t, c) for t, c in rows if t != target})


def has_column(db: Any, table: str, column: str) -> bool:
    return bool(db.execute(text("""
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = :t AND column_name = :c
    """), {"t": table, "c": column}).scalar())


def merge_entry(db: Any, dup_entry: int, keep_entry: int, entry_refs, log: list[str]) -> None:
    """Move the duplicate entry's child rows onto the kept entry, never duplicating."""
    for table, column in entry_refs:
        n = db.execute(text(f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" = :d'), {"d": dup_entry}).scalar()
        if not n:
            continue
        if has_column(db, table, "session_id"):
            moved = db.execute(text(f'''
                UPDATE "{table}" SET "{column}" = :k
                WHERE "{column}" = :d AND session_id NOT IN (
                    SELECT session_id FROM "{table}" WHERE "{column}" = :k AND session_id IS NOT NULL)
            '''), {"d": dup_entry, "k": keep_entry}).rowcount
        else:
            has_kept = db.execute(text(f'SELECT 1 FROM "{table}" WHERE "{column}" = :k LIMIT 1'),
                                  {"k": keep_entry}).scalar()
            moved = 0 if has_kept else db.execute(text(
                f'UPDATE "{table}" SET "{column}" = :k WHERE "{column}" = :d'), {"d": dup_entry, "k": keep_entry}).rowcount
        dropped = db.execute(text(f'DELETE FROM "{table}" WHERE "{column}" = :d'), {"d": dup_entry}).rowcount
        log.append(f"      {table}: {moved} moved, {dropped} duplicate rows dropped")
    db.execute(text("DELETE FROM race_entries WHERE id = :d"), {"d": dup_entry})


def merge_driver(db: Any, dup: int, keep: int, log: list[str]) -> None:
    entry_refs = referencing(db, "race_entries", "race_entry_id")
    for entry_id, race_id in db.execute(text("SELECT id, race_id FROM race_entries WHERE driver_id = :d"),
                                        {"d": dup}).all():
        kept = db.execute(text("SELECT id FROM race_entries WHERE race_id = :r AND driver_id = :k"),
                          {"r": race_id, "k": keep}).scalar()
        if kept is None:
            db.execute(text("UPDATE race_entries SET driver_id = :k WHERE id = :e"), {"k": keep, "e": entry_id})
            log.append(f"    race {race_id}: entry {entry_id} moved to driver {keep}")
        else:
            log.append(f"    race {race_id}: entry {entry_id} merged into entry {kept}")
            merge_entry(db, entry_id, kept, entry_refs, log)
    for table, column in referencing(db, "drivers", "driver_id"):
        if table == "race_entries":
            continue
        n = db.execute(text(f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" = :d'), {"d": dup}).scalar()
        if not n:
            continue
        try:
            with db.begin_nested():
                db.execute(text(f'UPDATE "{table}" SET "{column}" = :k WHERE "{column}" = :d'), {"k": keep, "d": dup})
            log.append(f"    {table}: {n} rows repointed")
        except Exception:                      # unique clash: the kept driver already has these rows
            db.execute(text(f'DELETE FROM "{table}" WHERE "{column}" = :d'), {"d": dup})
            log.append(f"    {table}: {n} rows dropped (kept driver already has them)")
    db.execute(text("DELETE FROM drivers WHERE id = :d"), {"d": dup})


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as db:
        tx = db.begin()
        groups = duplicate_groups(db)
        if not groups:
            print("no duplicate driver spellings")
            return 0
        for group in groups:
            (keep, keep_name), *dups = group
            for dup, dup_name in dups:
                races = db.execute(text("SELECT COUNT(*) FROM race_entries WHERE driver_id = :d"), {"d": dup}).scalar()
                log: list[str] = []
                merge_driver(db, dup, keep, log)
                print(f"driver {dup} {dup_name!r} ({races} race entries) -> {keep} {keep_name!r}")
                print("\n".join(log))
        if args.apply:
            tx.commit()
            print("applied")
        else:
            tx.rollback()
            print("dry run: nothing changed -- re-run with --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
