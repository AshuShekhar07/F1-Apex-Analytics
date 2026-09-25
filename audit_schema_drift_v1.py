"""Compare the live database schema against the schema the repo can rebuild.

Read-only against DATABASE_URL. Builds a throwaway reference database from
schema_migrations.SCHEMA_FILES on a server you point it at, diffs
information_schema.columns, then drops the reference database.

    python audit_schema_drift_v1.py --reference-server-url postgresql://USER@localhost:5432/postgres

The reference server can be the same server as production; the user needs
CREATEDB. Output sections:
  live_only  -- exists in production but no repo DDL creates it (add a migration)
  repo_only  -- repo DDL creates it but production lacks it (apply the migration)
  type_diff  -- both exist with different types (fix the reconstructed DDL)
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from schema_migrations import apply_schema

COLUMNS_SQL = """
    SELECT c.table_name, c.column_name,
           CASE
               WHEN c.data_type = 'character varying' THEN 'varchar(' || COALESCE(c.character_maximum_length::text, '') || ')'
               WHEN c.data_type = 'numeric' AND c.numeric_precision IS NOT NULL
                   THEN 'numeric(' || c.numeric_precision || ',' || c.numeric_scale || ')'
               ELSE c.data_type
           END AS column_type
    FROM information_schema.columns c
    JOIN information_schema.tables t
      ON t.table_schema = c.table_schema AND t.table_name = c.table_name
    WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE'
"""


def read_columns(engine) -> dict[tuple[str, str], str]:
    with engine.connect() as conn:
        rows = conn.execute(text(COLUMNS_SQL)).all()
    return {(r.table_name, r.column_name): r.column_type for r in rows}


def diff_columns(live: dict, repo: dict) -> dict[str, list]:
    return {
        "live_only": sorted(k for k in live if k not in repo),
        "repo_only": sorted(k for k in repo if k not in live),
        "type_diff": sorted(
            (k, live[k], repo[k]) for k in live.keys() & repo.keys() if live[k] != repo[k]
        ),
    }


def build_reference(server_url: str) -> dict:
    name = f"apex21_schema_ref_{uuid.uuid4().hex[:8]}"
    admin = create_engine(server_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    ref_engine = create_engine(make_url(server_url).set(database=name))
    try:
        with ref_engine.begin() as conn:
            apply_schema(conn)
        return read_columns(ref_engine)
    finally:
        ref_engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reference-server-url", required=True)
    args = parser.parse_args()

    live = read_columns(create_engine(os.environ["DATABASE_URL"]))
    repo = build_reference(args.reference_server_url)
    result = diff_columns(live, repo)

    for section, items in result.items():
        print(f"\n{section}: {len(items)}")
        for item in items:
            print(f"  {item}")
    return 1 if any(result.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
