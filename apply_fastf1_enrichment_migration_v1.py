"""Apply the FastF1 enrichment schema using the project's DATABASE_URL."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text


MIGRATION_PATH = Path(__file__).with_name("migration_fastf1_enrichment_v1.sql")


def main() -> None:
    load_dotenv()
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is not set")

    sql = MIGRATION_PATH.read_text(encoding="utf-8")
    engine = create_engine(database_url)
    with engine.begin() as conn:
        for statement in sql.split(";"):
            statement = statement.strip()
            if statement:
                conn.execute(text(statement))

    print("Applied FastF1 enrichment migration successfully")


if __name__ == "__main__":
    main()
