"""Remove orphan duplicate race_entries rows (same race_id + driver_id).

Background: tests/real_db found race 38 (2019 Suzuka) with two entries each for
Sergio Perez (772 real, 788 empty) and Nico Hulkenberg (778 real, 789 empty).
Empty duplicates inflate driver race counts and add blank history rows.

Safety rules:
  * every table/column that can reference race_entries is discovered from the
    catalog (foreign keys AND any column named race_entry_id), so no table is missed
  * an entry is deleted only if NOTHING references it
  * a group is repaired only if exactly one entry in it has data; anything
    else is reported and left alone for manual review
  * dry run by default; --apply deletes inside one transaction

    python fix_duplicate_race_entries_v1.py            # report only
    python fix_duplicate_race_entries_v1.py --apply    # delete orphans

After a clean apply, run migration_race_entries_unique_v1.sql to stop
duplicates from being created again.
"""

from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

REFERENCING_COLUMNS_SQL = """
    SELECT c.conrelid::regclass::text AS table_name, a.attname AS column_name
    FROM pg_constraint c
    JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
    WHERE c.contype = 'f' AND c.confrelid = 'race_entries'::regclass
    UNION
    SELECT table_name, column_name
    FROM information_schema.columns
    WHERE table_schema = current_schema() AND column_name = 'race_entry_id'
"""

DUPLICATE_GROUPS_SQL = """
    SELECT race_id, driver_id, array_agg(id ORDER BY id) AS entry_ids
    FROM race_entries
    GROUP BY race_id, driver_id
    HAVING COUNT(*) > 1
    ORDER BY race_id, driver_id
"""


def referencing_columns(conn) -> list[tuple[str, str]]:
    return sorted({(r.table_name, r.column_name) for r in conn.execute(text(REFERENCING_COLUMNS_SQL))})


def reference_counts(conn, entry_id: int, columns) -> dict[str, int]:
    counts = {}
    for table, column in columns:
        n = conn.execute(text(f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" = :e'), {"e": entry_id}).scalar()
        if n:
            counts[f"{table}.{column}"] = n
    return counts


def plan_repairs(conn) -> tuple[list[dict], list[dict]]:
    """Return (deletable orphan entries, groups needing manual review)."""
    columns = referencing_columns(conn)
    deletable, manual = [], []
    for group in conn.execute(text(DUPLICATE_GROUPS_SQL)).mappings():
        refs = {eid: reference_counts(conn, eid, columns) for eid in group["entry_ids"]}
        used = [eid for eid, r in refs.items() if r]
        record = {"race_id": group["race_id"], "driver_id": group["driver_id"], "references": refs}
        if len(used) == 1:
            deletable += [{**record, "entry_id": eid, "keep": used[0]} for eid in refs if eid not in used]
        else:
            manual.append(record)
    return deletable, manual


def apply_repairs(conn, deletable: list[dict]) -> int:
    ids = [d["entry_id"] for d in deletable]
    if not ids:
        return 0
    return conn.execute(text("DELETE FROM race_entries WHERE id = ANY(:ids)"), {"ids": ids}).rowcount


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="delete orphan duplicates (default: dry run)")
    args = parser.parse_args()

    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.begin() as conn:
        deletable, manual = plan_repairs(conn)
        for d in deletable:
            print(f"orphan: race {d['race_id']} driver {d['driver_id']} entry {d['entry_id']} "
                  f"(no references; keeping entry {d['keep']}: {d['references'][d['keep']]})")
        for m in manual:
            print(f"MANUAL REVIEW: race {m['race_id']} driver {m['driver_id']} references {m['references']}")
        if not deletable and not manual:
            print("No duplicate race_entries found.")
        if args.apply:
            print(f"Deleted {apply_repairs(conn, deletable)} orphan entries.")
        elif deletable:
            print("Dry run -- re-run with --apply to delete the orphans above.")
    return 1 if manual else 0


if __name__ == "__main__":
    sys.exit(main())
