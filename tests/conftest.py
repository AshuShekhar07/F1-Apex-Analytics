import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# tests/real_db/ runs against the real Apex21 database (DATABASE_URL) and checks
# known F1 facts. Opt in explicitly so a plain `pytest` never needs production.
if not os.getenv("APEX21_REAL_DB_TESTS"):
    collect_ignore_glob = ["real_db/*"]


@pytest.fixture(scope="session")
def fixture_db_url():
    """Throwaway database built from the repo DDL and seeded with synthetic rows.

    Needs APEX21_TEST_DATABASE_URL pointing at any PostgreSQL server/database
    whose user can CREATE DATABASE, e.g. postgresql://postgres@localhost:5432/postgres.
    The database is created fresh and dropped after the session.
    """
    server_url = os.getenv("APEX21_TEST_DATABASE_URL")
    if not server_url:
        pytest.skip("APEX21_TEST_DATABASE_URL not set; skipping fixture-database API tests")

    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    from schema_migrations import apply_schema
    import fixture_data

    name = f"apex21_test_{uuid.uuid4().hex[:8]}"
    admin = create_engine(server_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = make_url(server_url).set(database=name)
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            apply_schema(conn)
            fixture_data.seed(conn)
        yield url.render_as_string(hide_password=False)
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture(scope="session")
def api_client(fixture_db_url):
    """FastAPI TestClient whose get_db dependency is bound to the fixture database."""
    # app.database and strategy_production_v6 build engines at import time;
    # give them the fixture URL if nothing else is configured.
    os.environ.setdefault("DATABASE_URL", fixture_db_url)

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database import get_db
    from app.main import app

    engine = create_engine(fixture_db_url)
    Session = sessionmaker(bind=engine, autoflush=False)

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()
