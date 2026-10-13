"""Scheduled pipeline runner: load new data, check it, forecast -- unattended.

Run it every 10 minutes (cron, a scheduler container or `--loop`); it decides itself whether a
run is due, so the schedule is one line:

  due   a session of this race weekend has finished, its data should be out (FastF1's calendar:
        start + PUBLISHED_AFTER) and the database does not have it complete yet: every
        CATCH_UP_MINUTES, for up to CATCH_UP_HOURS (a cancelled session is not chased forever)
        race weekend (a race between 2 days ago and 3 days ahead): every RACE_WEEKEND_HOURS
        otherwise: every QUIET_HOURS
        "complete": laps or results stored; a race or sprint with every official point; a
        qualifying with lap times (a session loaded before F1 published everything is not done)
  lock  a PostgreSQL advisory lock, so two runners (two containers, cron overlap) never load
        at the same time
  log   every run is a row in pipeline_runs (status ok / failed, last output lines) and a log
        file in logs/; /health reports the latest run and goes 'degraded' after a failure

    python run_pipeline.py --if-due            # what a scheduler calls every 10 minutes
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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parent
LOCK_KEY = 21021                     # pg_advisory_lock key for the pipeline
RACE_WEEKEND_HOURS = 2
QUIET_HOURS = 24
CATCH_UP_MINUTES = 30
CATCH_UP_HOURS = 12
LOOP_SECONDS = 600
# minutes from a session's start until its data is normally out (length + F1 / FastF1 publishing)
PUBLISHED_AFTER = {"FP1": 100, "FP2": 100, "FP3": 100, "SQ": 85, "S": 100, "Q": 100, "R": 200}
SESSION_CODES = {"Practice 1": "FP1", "Practice 2": "FP2", "Practice 3": "FP3", "Sprint Qualifying": "SQ",
                 "Sprint Shootout": "SQ", "Sprint": "S", "Qualifying": "Q", "Race": "R"}
TAIL_LINES = 40


def ensure_table(db) -> None:
    db.execute(text((ROOT / "migration_pipeline_runs_v1.sql").read_text()))


def race_weekend(db, today: date) -> bool:
    return bool(db.execute(text("""
        SELECT 1 FROM races WHERE race_date BETWEEN :a AND :b LIMIT 1
    """), {"a": today - timedelta(days=2), "b": today + timedelta(days=3)}).scalar())


def schedule_sessions(season: int, rnd: int) -> list[tuple[str, datetime]]:
    """(session code, start in UTC) of a weekend from FastF1's calendar (cached on disk); [] when
    it cannot be read, so the schedule falls back to the race-weekend interval."""
    try:
        import fastf1

        from fastf1_cache import fastf1_cache_dir

        fastf1.Cache.enable_cache(fastf1_cache_dir())
        event = fastf1.get_event_schedule(season).get_event_by_round(rnd)
    except Exception:
        return []
    out = []
    for n in range(1, 6):
        name, start = event.get(f"Session{n}"), event.get(f"Session{n}DateUtc")
        if name in SESSION_CODES and start is not None and str(start) != "NaT":
            out.append((SESSION_CODES[name], start.to_pydatetime().replace(tzinfo=None)))
    return out


def session_complete(db, race_id: int, code: str) -> bool:
    row = db.execute(text("""
        SELECT EXISTS (SELECT 1 FROM laps l WHERE l.session_id = s.id) AS laps,
               (SELECT COUNT(*) FROM race_results rr WHERE rr.session_id = s.id) AS results,
               (SELECT COUNT(*) FROM race_results rr WHERE rr.session_id = s.id
                  AND (rr.points IS NULL OR rr.points = 'NaN')) AS no_points,
               (SELECT COUNT(*) FROM qualifying_results q WHERE q.session_id = s.id
                  AND COALESCE(q.q1_time, q.q2_time, q.q3_time) IS NOT NULL) AS timed
        FROM sessions s WHERE s.race_id = :r AND s.session_type = :t LIMIT 1
    """), {"r": race_id, "t": code}).mappings().first()
    if row is None:
        return False
    if code in ("R", "S"):
        return row["results"] > 0 and row["no_points"] == 0
    if code == "Q":
        return row["timed"] > 0
    return bool(row["laps"])


def waiting_sessions(db, now_utc: datetime, today: date, schedule=schedule_sessions) -> list[str]:
    """Sessions of this race weekend whose data should be out by now but is not complete in the
    database (within CATCH_UP_HOURS of when it should have appeared)."""
    out = []
    for race_id, season, rnd in db.execute(text("""
        SELECT id, season_year, round_number FROM races WHERE race_date BETWEEN :a AND :b ORDER BY race_date
    """), {"a": today - timedelta(days=2), "b": today + timedelta(days=3)}):
        for code, start in schedule(int(season), int(rnd)):
            ready = start + timedelta(minutes=PUBLISHED_AFTER.get(code, 120))
            if ready <= now_utc <= ready + timedelta(hours=CATCH_UP_HOURS) and not session_complete(db, race_id, code):
                out.append(f"{season} R{rnd} {code}")
    return out


def is_due(db, now: datetime, today: date, waiting: list[str] | tuple = ()) -> tuple[bool, str]:
    if waiting:
        last_try = db.execute(text("SELECT MAX(started_at) FROM pipeline_runs WHERE job = 'update'")).scalar()
        minutes = (now - last_try).total_seconds() / 60 if last_try else None
        why = f"waiting for {', '.join(waiting)}"
        if minutes is None or minutes >= CATCH_UP_MINUTES:
            return True, f"{why}; retry every {CATCH_UP_MINUTES} min"
        return False, f"{why}; last try {minutes:.0f} min ago, retry every {CATCH_UP_MINUTES} min"
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
                    waiting = waiting_sessions(db, datetime.now(timezone.utc).replace(tzinfo=None), date.today())
                    due, why = is_due(db, db.execute(text("SELECT now()::timestamp")).scalar(), date.today(), waiting)
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
    parser.add_argument("--loop", action="store_true", help="check every 10 minutes forever (built-in scheduler)")
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
