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
