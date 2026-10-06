"""Ordered DDL that builds an Apex21 database from an empty PostgreSQL schema.

Used by the fixture-database tests and by audit_schema_drift_v1.py. Keep this
list in sync whenever a new migration_*.sql file is added.
"""

from pathlib import Path

from sqlalchemy import text

REPO_ROOT = Path(__file__).resolve().parent

SCHEMA_FILES = (
    "schema.sql",
    "migration_manual_fields.sql",
    "migration_weekend_format.sql",
    "migration_repo_baseline_gaps_v1.sql",
    "migration_race_strategy_pit_stops_v1.sql",
    "migration_fastf1_enrichment_v1.sql",
    "migration_race_entries_unique_v1.sql",
    "migration_race_forecasts_v1.sql",
    "migration_provisional_grid_v1.sql",
    "migration_race_forecast_live_v1.sql",
    "migration_pipeline_runs_v1.sql",
    "migration_championship_projections_v1.sql",
    "migration_winner_prediction_live_v1.sql",
    "migration_winning_strategy_live_v1.sql",
)


def apply_schema(connection) -> None:
    """Apply every schema file in order on an open SQLAlchemy connection.

    Uses the raw DBAPI cursor so multi-statement files (including the view in
    migration_weekend_format.sql) execute exactly as psql would run them.
    """
    raw = connection.connection.dbapi_connection
    with raw.cursor() as cursor:
        for name in SCHEMA_FILES:
            cursor.execute((REPO_ROOT / name).read_text())
    connection.execute(text("SELECT 1"))


if __name__ == "__main__":
    import os

    from dotenv import load_dotenv
    from sqlalchemy import create_engine

    load_dotenv()
    with create_engine(os.environ["DATABASE_URL"]).begin() as conn:
        apply_schema(conn)
    print(f"Applied {len(SCHEMA_FILES)} schema files.")
