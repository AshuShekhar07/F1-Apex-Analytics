"""Every route of the API, with the fixture's ids and with bad ones: no server errors, every
answer JSON, and no NaN that had to be sent as null."""

import logging

from sqlalchemy import create_engine


def test_every_route_answers_cleanly(api_client, fixture_db_url, caplog):
    from api_sweep_v1 import sweep
    from app.main import app

    engine = create_engine(fixture_db_url)
    with caplog.at_level(logging.WARNING, logger="apex21"), engine.connect() as db:
        # deprecated routes are not used by the website and open their own database connection
        # (another test module points that at SQLite); the deployment sweep covers them
        report = sweep(api_client, app, db, all_races=True, deprecated=False)
    engine.dispose()
    assert len(report.results) > 500
    assert [f"{r.method} {r.url} -> {r.status}: {r.error}" for r in report.failures] == []
    assert not [m for m in caplog.messages if "NaN" in m]
    statuses = {r.status for r in report.results}
    assert 200 in statuses and 404 in statuses and 422 in statuses
