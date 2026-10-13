from datetime import date, datetime, timedelta

from sqlalchemy import text

import run_pipeline
from test_race_strategy_backtest_v2 import synthetic_db  # noqa: F401  (fixture)


def test_schedule_runs_more_often_on_race_weekends(synthetic_db):
    with synthetic_db.begin() as db:
        run_pipeline.ensure_table(db)
        db.execute(text("DELETE FROM pipeline_runs"))
        now = datetime(2024, 3, 5, 12, 0)                     # synthetic races are 2024-04-01 and 2024-05-01
        assert run_pipeline.is_due(db, now, now.date()) == (True, "no successful run yet")
        db.execute(text("INSERT INTO pipeline_runs (job, started_at, status) VALUES ('update', :t, 'ok')"),
                   {"t": now - timedelta(hours=5)})
        due, why = run_pipeline.is_due(db, now, now.date())                  # quiet week: 24 h interval
        assert not due and "interval 24 h" in why
        weekend = datetime(2024, 3, 31, 12, 0)                               # race on 2024-04-01
        db.execute(text("UPDATE pipeline_runs SET started_at = :t"), {"t": weekend - timedelta(hours=3)})
        due, why = run_pipeline.is_due(db, weekend, weekend.date())
        assert due and "interval 2 h" in why
        db.execute(text("DELETE FROM pipeline_runs"))


def test_runs_are_recorded_and_failures_are_visible(synthetic_db, monkeypatch, tmp_path):
    outcomes = iter([(0, "all good"), (1, "Traceback: boom")])
    monkeypatch.setattr(run_pipeline, "run_update", lambda season, log_path: next(outcomes))
    assert run_pipeline.run_once(synthetic_db, if_due=False, season=2024) == 0
    assert run_pipeline.run_once(synthetic_db, if_due=False, season=2024) == 1
    with synthetic_db.begin() as db:
        rows = db.execute(text("SELECT status, detail FROM pipeline_runs ORDER BY id")).all()
        assert [r[0] for r in rows] == ["ok", "failed"] and "boom" in rows[1][1]
        db.execute(text("DELETE FROM pipeline_runs"))


def test_a_second_runner_does_not_overlap(synthetic_db, monkeypatch):
    monkeypatch.setattr(run_pipeline, "run_update", lambda season, log_path: (0, ""))
    with synthetic_db.connect() as other:
        assert other.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": run_pipeline.LOCK_KEY}).scalar()
        assert run_pipeline.run_once(synthetic_db, if_due=False, season=2024) == 2
        other.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": run_pipeline.LOCK_KEY})


def test_catch_up_while_a_finished_session_has_no_complete_data(fixture_db_url):
    """Race 1 of the fixture (2021-03-28): the pipeline retries every 30 minutes once a session
    should have been published but is not complete in the database, for up to 12 hours."""
    from sqlalchemy import create_engine

    def schedule(season, rnd):
        return [("FP1", datetime(2021, 3, 26, 10)), ("Q", datetime(2021, 3, 27, 14)), ("R", datetime(2021, 3, 28, 15))] \
            if (season, rnd) == (2021, 1) else []

    engine = create_engine(fixture_db_url)
    sunday = date(2021, 3, 28)
    with engine.connect() as db:
        tx = db.begin()
        try:
            run_pipeline.ensure_table(db)
            db.execute(text("DELETE FROM pipeline_runs"))
            # the race finished at about 17:00; its data is normally out by 18:20
            assert run_pipeline.waiting_sessions(db, datetime(2021, 3, 28, 18, 0), sunday, schedule) == []
            # loaded and complete (results with every point): nothing to wait for
            assert run_pipeline.waiting_sessions(db, datetime(2021, 3, 28, 19, 0), sunday, schedule) == []
            # one point missing (official points not out yet): keep catching up
            db.execute(text("""UPDATE race_results SET points = NULL WHERE id = (
                SELECT rr.id FROM race_results rr JOIN sessions s ON s.id = rr.session_id
                WHERE s.race_id = 1 AND s.session_type = 'R' ORDER BY rr.id LIMIT 1)"""))
            waiting = run_pipeline.waiting_sessions(db, datetime(2021, 3, 28, 19, 0), sunday, schedule)
            assert waiting == ["2021 R1 R"]
            # ... but not forever: 12 hours after it should have appeared, back to the normal schedule
            assert run_pipeline.waiting_sessions(db, datetime(2021, 3, 29, 7, 0), sunday, schedule) == []
            # qualifying with no lap times is not complete either (inside its window)
            db.execute(text("""UPDATE qualifying_results SET q1_time = NULL, q2_time = NULL, q3_time = NULL
                               WHERE session_id = (SELECT id FROM sessions WHERE race_id = 1 AND session_type = 'Q')"""))
            assert run_pipeline.waiting_sessions(db, datetime(2021, 3, 27, 16, 0), sunday, schedule) == ["2021 R1 Q"]

            now = datetime(2021, 3, 28, 19, 0)
            assert run_pipeline.is_due(db, now, sunday, waiting)[0] is True              # never tried
            db.execute(text("INSERT INTO pipeline_runs (job, started_at, status) VALUES ('update', :t, 'failed')"),
                       {"t": now - timedelta(minutes=10)})
            due, why = run_pipeline.is_due(db, now, sunday, waiting)
            assert not due and "waiting for 2021 R1 R" in why and "retry every 30 min" in why
            db.execute(text("UPDATE pipeline_runs SET started_at = :t"), {"t": now - timedelta(minutes=31)})
            assert run_pipeline.is_due(db, now, sunday, waiting)[0] is True
            # the calendar cannot be read: no waiting list, the race-weekend interval applies
            assert run_pipeline.waiting_sessions(db, now, sunday, lambda s, r: []) == []
        finally:
            tx.rollback()
    engine.dispose()
