"""Scheduled pipeline runner: load new data, check it, forecast -- unattended.

Run it every hour (cron, a scheduler container or `--loop`); it decides itself whether a run
is due, so the schedule is one line:

  due   race weekend (a race between 2 days ago and 3 days ahead): every RACE_WEEKEND_HOURS
        otherwise: every QUIET_HOURS
  lock  a PostgreSQL advisory lock, so two runners (two containers, cron overlap) never load
        at the same time
  log   every run is a row in pipeline_runs (status ok / failed, last output lines) and a log
        file in logs/; /health reports the latest run and goes 'degraded' after a failure

    python run_pipeline.py --if-due            # what the scheduler calls every hour
    python run_pipeline.py                     # run now
    python run_pipeline.py --loop              # simple built-in scheduler (container)

Exit codes: 0 ok or not due, 1 failed, 2 another run holds the lock.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parent
LOCK_KEY = 21021                     # pg_advisory_lock key for the pipeline
RACE_WEEKEND_HOURS = 2
QUIET_HOURS = 24
LOOP_SECONDS = 3600
TAIL_LINES = 40


def ensure_table(db) -> None:
    db.execute(text((ROOT / "migration_pipeline_runs_v1.sql").read_text()))


def race_weekend(db, today: date) -> bool:
    return bool(db.execute(text("""
        SELECT 1 FROM races WHERE race_date BETWEEN :a AND :b LIMIT 1
    """), {"a": today - timedelta(days=2), "b": today + timedelta(days=3)}).scalar())


def is_due(db, now: datetime, today: date) -> tuple[bool, str]:
    last = db.execute(text("""
        SELECT started_at FROM pipeline_runs WHERE job = 'update' AND status = 'ok'
        ORDER BY started_at DESC LIMIT 1""")).scalar()
    every = RACE_WEEKEND_HOURS if race_weekend(db, today) else QUIET_HOURS
    if last is None:
        return True, "no successful run yet"
    age = (now - last).total_seconds() / 3600
    return (age >= every, f"last ok run {age:.1f} h ago, interval {every} h")


def run_update(season: int, log_path: Path) -> tuple[int, str]:
    """update_latest_races.py as a child process, output to the log file; (exit code, tail)."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as log:
        code = subprocess.call([sys.executable, "-u", str(ROOT / "update_latest_races.py"), "--season", str(season)],
                               stdout=log, stderr=subprocess.STDOUT, cwd=ROOT)
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    return code, "\n".join(lines[-TAIL_LINES:])


def run_once(engine, *, if_due: bool, season: int) -> int:
    with engine.connect() as lock_conn:
        if not lock_conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}).scalar():
            print("another pipeline run holds the lock; skipping")
            return 2
        try:
            with engine.begin() as db:
                ensure_table(db)
                if if_due:
                    due, why = is_due(db, db.execute(text("SELECT now()::timestamp")).scalar(), date.today())
                    if not due:
                        print(f"not due ({why})")
                        return 0
                    print(f"due ({why})")
                run_id = db.execute(text("INSERT INTO pipeline_runs (job) VALUES ('update') RETURNING id")).scalar()
            log_path = ROOT / "logs" / f"pipeline_{datetime.now():%Y%m%d_%H%M%S}.log"
            started = time.monotonic()
            try:
                code, tail = run_update(season, log_path)
            except Exception as exc:                 # could not even start the child
                code, tail = 1, f"{type(exc).__name__}: {exc}"
            status = "ok" if code == 0 else "failed"
            with engine.begin() as db:
                db.execute(text("""UPDATE pipeline_runs SET finished_at = now(), status = :s, detail = :d
                                   WHERE id = :i"""),
                           {"s": status, "d": f"exit {code}, {time.monotonic() - started:.0f}s, log {log_path.name}\n{tail}",
                            "i": run_id})
            print(f"pipeline {status} (exit {code}); log {log_path}")
            return 0 if code == 0 else 1
        finally:
            lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--if-due", action="store_true", help="run only if the schedule says it is time")
    parser.add_argument("--loop", action="store_true", help="check every hour forever (built-in scheduler)")
    parser.add_argument("--season", type=int, default=date.today().year)
    args = parser.parse_args()
    engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    if not args.loop:
        return run_once(engine, if_due=args.if_due, season=args.season)
    while True:
        try:
            run_once(engine, if_due=True, season=date.today().year)
        except Exception as exc:                     # keep the scheduler alive; the next tick retries
            print(f"scheduler tick failed: {type(exc).__name__}: {exc}", flush=True)
        time.sleep(LOOP_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
