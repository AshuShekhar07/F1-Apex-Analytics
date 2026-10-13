"""Why is the API or the website not working? One command, one report.

Checks, in order, and ends with a PROBLEMS list:
  1. setup      Python, this folder's git branch and commit, .env, package versions
  2. database   connection, the next race and its sessions, the last pipeline runs,
                queries that are stuck or waiting on a lock
  3. server     whether an API answers on :8000, whether it is this folder's code (the
                website needs routes that only feature/frontend has), and whether the
                website's dev server (:5173) reaches it
  3b. data     how much of the extra data (speed traps, tyre age, telemetry summaries,
                race control, weather samples, corners) each season has
  4. endpoints  every API call the website makes, run inside this script against the real
                database, with the time each took and the full error when one fails
  4b. sweep     with --deep: every API route (not only the website's), called with the
                database's own ids and with bad ones (api_sweep_v1.py); any 5xx, crash or
                non-JSON answer is a problem. Latest 8 races plus one per season, or every
                race with --all-races (slow: a few minutes per season)
  5. FastF1     for the next race's sessions that have run but have no data here: has F1
                published them, and is FastF1's saved copy older than what F1 has now

Read-only: it changes nothing in the database or in FastF1's saved copies.

    python api_check_v1.py > api_check.txt 2>&1
    python api_check_v1.py --skip-fastf1
    python api_check_v1.py --deep              # before a deployment
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# routes the website calls that only the feature/frontend API has: missing ones mean the
# server on :8000 runs older code
WEBSITE_ROUTES = (
    "/seasons/{season}/results", "/seasons/{season}/teammates", "/races/{race_id}/sessions",
    "/races/{race_id}/long-runs", "/records/calibration", "/drivers/roster/{season}", "/tracks",
)
SLOW_SECONDS = 15
SESSION_CODES = {"Practice 1": "FP1", "Practice 2": "FP2", "Practice 3": "FP3", "Qualifying": "Q",
                 "Sprint Qualifying": "SQ", "Sprint Shootout": "SQ", "Sprint": "S", "Race": "R"}

problems: list[str] = []


def section(title: str) -> None:
    print(f"\n=== {title} {'=' * max(0, 74 - len(title))}", flush=True)


def problem(message: str) -> None:
    problems.append(message)
    print(f"  PROBLEM: {message}", flush=True)


def git(*args: str) -> str:
    try:
        out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=30,
                             env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
        return (out.stdout or out.stderr).strip()
    except Exception as exc:                              # git missing or hung
        return f"? ({type(exc).__name__})"


def setup() -> str | None:
    section("1. setup")
    print(f"  python    {sys.version.split()[0]}  ({sys.executable})")
    print(f"  system    {platform.platform()}")
    print(f"  folder    {ROOT}")
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    print(f"  branch    {branch}")
    print(f"  commit    {git('log', '-1', '--format=%h %cd %s', '--date=format:%Y-%m-%d %H:%M')}")
    git("fetch", "--quiet", "origin", branch)
    behind = git("rev-list", "--count", f"HEAD..origin/{branch}")
    if behind.isdigit() and int(behind) > 0:
        problem(f"this folder is {behind} commit(s) behind GitHub: run `git pull` here")
    if branch != "feature/frontend":
        print("  note      the website's API lives on feature/frontend; this folder is on another branch")

    from importlib.metadata import PackageNotFoundError, version
    found = []
    for pkg in ("fastapi", "starlette", "uvicorn", "sqlalchemy", "psycopg2-binary", "psycopg2", "httpx",
                "fastf1", "pandas", "numpy", "requests-cache"):
        try:
            found.append(f"{pkg} {version(pkg)}")
        except PackageNotFoundError:
            pass
    print(f"  packages  {', '.join(found)}")

    env_file = ROOT / ".env"
    from_environment = bool(os.getenv("DATABASE_URL"))      # e.g. inside the Docker containers
    print(f"  .env      {'found' if env_file.exists() else 'not used (DATABASE_URL is set)' if from_environment else 'MISSING'}")
    if not env_file.exists() and not from_environment:
        problem(f"no .env in {ROOT}: copy it from the main folder (cp ../F1-Apex-Analytics/.env .)")
    from dotenv import load_dotenv
    load_dotenv(env_file)
    url = os.getenv("DATABASE_URL")
    if not url:
        problem("DATABASE_URL is not set, so the API cannot start")
        return None
    from sqlalchemy.engine import make_url
    print(f"  database  {make_url(url).render_as_string(hide_password=True)}")
    return url


def database(url: str) -> dict | None:
    """Connects and prints the state of the data; returns the next race (or None)."""
    section("2. database")
    from sqlalchemy import create_engine, text

    engine = create_engine(url, connect_args={"options": "-c statement_timeout=60000"})
    try:
        with engine.connect() as db:
            info = db.execute(text("SELECT version(), now(), current_setting('TimeZone')")).one()
    except Exception as exc:
        problem(f"cannot connect to the database: {type(exc).__name__}: {str(exc).splitlines()[0]}")
        return None
    print(f"  server    {info[0].split(',')[0]}")
    print(f"  db time   {info[1]}  ({info[2]})   this computer (UTC): {datetime.now(timezone.utc):%Y-%m-%d %H:%M}")

    with engine.connect() as db:
        import winner_prediction_v1 as wp

        race = None
        try:
            race = wp.race_row(db, wp.next_race_id(db))
            race["weekend_format"] = db.execute(text("SELECT weekend_format FROM races WHERE id = :r"),
                                                {"r": race["id"]}).scalar()
            race["track_id"] = db.execute(text("SELECT track_id FROM races WHERE id = :r"), {"r": race["id"]}).scalar()
            print(f"  next race {race['season_year']} R{race['round_number']} {race['track']} on {race['race_date']}"
                  f" (race id {race['id']}, {race['weekend_format'] or 'format unknown'})")
        except wp.PredictionError as exc:
            print(f"  next race none ({exc})")
        if race:
            rows = db.execute(text("""
                SELECT s.session_type, s.start_time,
                       (SELECT COUNT(*) FROM laps l WHERE l.session_id = s.id) AS laps,
                       (SELECT COUNT(*) FROM race_results rr WHERE rr.session_id = s.id) AS race_results,
                       (SELECT COUNT(*) FROM qualifying_results q WHERE q.session_id = s.id) AS quali_results
                FROM sessions s WHERE s.race_id = :r ORDER BY s.start_time NULLS LAST, s.id
            """), {"r": race["id"]}).mappings().all()
            print("  its sessions in the database:" if rows else "  its sessions in the database: none yet")
            for r in rows:
                print(f"    {r['session_type']:<4} start {r['start_time']}  laps {r['laps']:>5}  "
                      f"race results {r['race_results']:>3}  quali results {r['quali_results']:>3}")
            race["sessions_in_db"] = {r["session_type"]: dict(r) for r in rows}

        season = race["season_year"] if race else db.execute(text("SELECT MAX(season_year) FROM races")).scalar()
        bad = db.execute(text("""
            SELECT r.round_number, s.session_type, COUNT(*) FILTER (WHERE rr.points = 'NaN') AS nan,
                   COUNT(*) FILTER (WHERE rr.points IS NULL) AS missing
            FROM race_results rr JOIN sessions s ON s.id = rr.session_id AND s.session_type IN ('R', 'S')
            JOIN races r ON r.id = s.race_id
            WHERE r.season_year = :y AND (rr.points IS NULL OR rr.points = 'NaN')
            GROUP BY 1, 2 ORDER BY 1, 2"""), {"y": season}).mappings().all()
        for b in bad:
            print(f"  points    {season} R{b['round_number']} {b['session_type']}: {b['nan']} NaN, {b['missing']} missing")
        if any(b["nan"] for b in bad):
            problem("some race/sprint points are NaN (loaded before the official points were out): run "
                    f"`python repair_missing_points_v1.py --season {season} --apply` in the main folder")
        elif bad:
            print("  (missing points are filled by the pipeline once the official results are published)")

        if db.execute(text("SELECT to_regclass('pipeline_runs') IS NOT NULL")).scalar():
            runs = db.execute(text("""
                SELECT id, job, started_at, finished_at, status, LEFT(COALESCE(detail, ''), 300) AS detail
                FROM pipeline_runs ORDER BY started_at DESC LIMIT 8""")).mappings().all()
            print("  last pipeline runs:")
            for r in runs:
                print(f"    #{r['id']} {r['job']:<14} {r['status']:<8} started {r['started_at']:%Y-%m-%d %H:%M}"
                      f"  finished {r['finished_at'] or '-'}  {r['detail'].replace(chr(10), ' | ')}")
            if runs and runs[0]["status"] == "failed":
                problem("the last pipeline run failed (see its detail above)")
        else:
            print("  pipeline_runs table: missing")

        states = db.execute(text("""
            SELECT COALESCE(state, '?') AS state, COUNT(*) AS n FROM pg_stat_activity
            WHERE datname = current_database() GROUP BY 1 ORDER BY 2 DESC""")).all()
        limit = db.execute(text("SHOW max_connections")).scalar()
        print(f"  connections {', '.join(f'{s}: {n}' for s, n in states)}  (server limit {limit})")
        stuck = db.execute(text("""
            SELECT pid, state, wait_event_type, application_name,
                   EXTRACT(EPOCH FROM now() - COALESCE(query_start, xact_start))::int AS seconds,
                   LEFT(REGEXP_REPLACE(query, '\\s+', ' ', 'g'), 160) AS query
            FROM pg_stat_activity
            WHERE datname = current_database() AND pid <> pg_backend_pid()
              AND (state = 'active' OR state LIKE 'idle in transaction%')
              AND now() - COALESCE(query_start, xact_start) > interval '30 seconds'
            ORDER BY seconds DESC LIMIT 10""")).mappings().all()
        for s in stuck:
            print(f"    pid {s['pid']} {s['state']} {s['seconds']}s wait={s['wait_event_type'] or '-'}"
                  f" app={s['application_name'] or '-'}: {s['query']}")
        if any(s["wait_event_type"] == "Lock" for s in stuck):
            problem("queries are waiting on a database lock (listed above)")
        if any(s["state"].startswith("idle in transaction") and s["seconds"] > 300 for s in stuck):
            problem("a connection has held a transaction open for over 5 minutes (listed above)")
    engine.dispose()
    return race


def fan_data(url: str) -> None:
    """Read-only coverage of the data beyond results and lap times, per season."""
    section("3b. extra data in the database, per season")
    from sqlalchemy import create_engine, text

    engine = create_engine(url, connect_args={"options": "-c statement_timeout=180000"})
    per_lap = {
        "laps": "COUNT(*)",
        "speed_trap": "COUNT(l.speed_st_kmh)",
        "tyre_age": "COUNT(l.tyre_life_laps)",
        "position": "COUNT(l.position)",
        "pit_in": "COUNT(l.pit_in_time_seconds)",
        "deleted": "COUNT(*) FILTER (WHERE l.is_deleted)",
    }
    per_session = {
        "telemetry": "lap_telemetry_summary",
        "race_ctrl": "session_race_control_messages",
        "weather_s": "session_weather_samples",
    }
    with engine.connect() as db:
        exists = {t: db.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": t}).scalar()
                  for t in [*per_session.values(), "track_corners", "race_strategy_pit_stops"]}
        cols = [c for c in ("speed_st_kmh", "tyre_life_laps", "pit_in_time_seconds", "is_deleted")
                if db.execute(text("SELECT 1 FROM information_schema.columns WHERE table_name = 'laps' "
                                   "AND column_name = :c"), {"c": c}).scalar()]
        lap_exprs = {k: v for k, v in per_lap.items()
                     if not any(c in v for c in ("speed_st_kmh", "tyre_life_laps", "pit_in_time_seconds", "is_deleted"))
                     or any(c in v for c in cols)}
        try:
            rows = db.execute(text(f"""
                SELECT r.season_year, {', '.join(f'{v} AS {k}' for k, v in lap_exprs.items())}
                FROM laps l JOIN sessions s ON s.id = l.session_id JOIN races r ON r.id = s.race_id
                GROUP BY r.season_year ORDER BY r.season_year""")).mappings().all()
        except Exception as exc:
            print(f"  laps: {type(exc).__name__}: {str(exc).splitlines()[0]}")
            rows = []
        extra = {}
        for key, table in per_session.items():
            if not exists[table]:
                continue
            for season, n in db.execute(text(f"""
                SELECT r.season_year, COUNT(*) FROM {table} x JOIN sessions s ON s.id = x.session_id
                JOIN races r ON r.id = s.race_id GROUP BY r.season_year""")):
                extra.setdefault(season, {})[key] = n
        if exists["race_strategy_pit_stops"]:
            for season, n in db.execute(text("""
                SELECT r.season_year, COUNT(*) FROM race_strategy_pit_stops p JOIN races r ON r.id = p.race_id
                GROUP BY r.season_year""")):
                extra.setdefault(season, {})["pit_stops"] = n
        keys = [*lap_exprs, *[k for k in per_session if exists[per_session[k]]], "pit_stops"]
        print("  season " + " ".join(f"{k:>10}" for k in keys))
        for r in rows:
            values = [r[k] for k in lap_exprs] + [extra.get(r["season_year"], {}).get(k, 0) for k in keys[len(lap_exprs):]]
            print(f"  {r['season_year']:>6} " + " ".join(f"{v:>10}" for v in values))
        if exists["track_corners"]:
            n = db.execute(text("SELECT COUNT(DISTINCT track_id), COUNT(*) FROM track_corners")).one()
            print(f"  corner maps: {n[0]} tracks, {n[1]} corners")
    engine.dispose()


def fetch(url: str, timeout: float = 30) -> tuple[int | str, float, bytes]:
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status, time.perf_counter() - started, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, time.perf_counter() - started, exc.read()
    except Exception as exc:                              # refused, timed out, DNS
        return f"{type(exc).__name__}: {getattr(exc, 'reason', exc)}", time.perf_counter() - started, b""


def server() -> None:
    section("3. the running API (:8000) and website (:5173)")
    status, seconds, body = fetch("http://localhost:8000/health")
    print(f"  GET :8000/health -> {status} in {seconds:.1f}s {body[:200].decode(errors='replace')}")
    if not isinstance(status, int):
        problem("nothing answers on port 8000: the backend is not running (start uvicorn, see the README)")
    else:
        status, _, body = fetch("http://localhost:8000/openapi.json")
        try:
            paths = set(json.loads(body)["paths"]) if status == 200 else set()
        except ValueError:
            paths = set()
        missing = [p for p in WEBSITE_ROUTES if p not in paths]
        if missing:
            problem("the API on port 8000 is older code without " + ", ".join(missing) + ": stop it (Ctrl+C in its "
                    "terminal, or `pkill -f uvicorn`) and start it again from ~/projects/apex21-frontend")
        else:
            print(f"  it has every route the website needs ({len(paths)} routes)")
    status, seconds, body = fetch("http://localhost:5173/api/health")
    print(f"  GET :5173/api/health -> {status} in {seconds:.1f}s")
    if isinstance(status, int) and status >= 500:
        problem("the website's dev server is running but cannot reach the API on port 8000")
    elif not isinstance(status, int):
        print("  (the website's dev server is not running; that is fine for this check)")


def endpoints(url: str, race: dict | None) -> None:
    section("4. every API call the website makes, run here against the real database")
    try:
        from fastapi.testclient import TestClient
    except Exception as exc:                              # httpx missing
        print(f"  skipped: {exc}")
        return
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    os.environ.setdefault("DATABASE_URL", url)
    try:
        import logging

        from app.database import get_db
        from app.main import app
        logging.getLogger("httpx").setLevel(logging.WARNING)       # one line per request otherwise
        logging.getLogger("apex21").setLevel(logging.WARNING)      # same, with APP_ENV=production
    except Exception:
        problem("the API code does not even import (full error below): the server cannot start")
        print(traceback.format_exc())
        return
    engine = create_engine(url, connect_args={"options": "-c statement_timeout=120000"})
    Session = sessionmaker(bind=engine, autoflush=False)

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app, raise_server_exceptions=True)

    with engine.connect() as db:
        season = race["season_year"] if race else db.execute(text("SELECT MAX(season_year) FROM races")).scalar()
        last = db.execute(text("""
            SELECT r.id FROM races r JOIN sessions s ON s.race_id = r.id AND s.session_type = 'R'
            WHERE EXISTS (SELECT 1 FROM race_results rr WHERE rr.session_id = s.id)
            ORDER BY r.race_date DESC LIMIT 1""")).scalar()
        team = db.execute(text("""
            SELECT re.team_id FROM race_entries re JOIN races r ON r.id = re.race_id
            WHERE r.season_year = :s AND re.team_id IS NOT NULL LIMIT 1"""), {"s": season}).scalar()
        driver = db.execute(text("""
            SELECT re.driver_id FROM race_entries re JOIN races r ON r.id = re.race_id
            WHERE r.season_year = :s AND re.role = 'race_driver' LIMIT 1"""), {"s": season}).scalar()
        track = race["track_id"] if race else None
    nxt = race["id"] if race else None
    print(f"  using season {season}, next race {nxt}, last finished race {last}, track {track}, "
          f"team {team}, driver {driver}")

    calls = [("every page (header)", "/races/next/winner-prediction"), ("every page (header)", "/health")]
    if nxt:
        calls += [("every page (header)", f"/races/{nxt}/sessions")]
        calls += [("Race", p) for p in (f"/races/{nxt}/forecast", f"/strategy/winning?race_id={nxt}",
                                        f"/races/{nxt}/qualifying", f"/races/{nxt}/long-runs",
                                        f"/strategy/winning/pre-start?race_id={nxt}")]
    calls += [("Race", "/records/live-predictions")]
    calls += [("Championship", p) for p in (f"/championship/{season}", f"/championship/{season}/history")]
    calls += [("Results / Teams / Championship", f"/seasons/{season}/results"), ("Results", "/records/calibration")]
    calls += [("Drivers", p) for p in (f"/drivers/roster/{season}", f"/seasons/{season}/teammates",
                                       "/drivers/wet-weather-ranking")]
    if driver:
        calls += [("Drivers", f"/drivers/{driver}/profile")]
    if team:
        calls += [("Teams", f"/teams/{team}/season/{season}")]
    calls += [("Tracks", "/tracks")]
    if track:
        calls += [("Tracks", f"/tracks/{track}/stats"), ("Tracks", f"/tracks/{track}/overtaking-index")]
    if last:
        calls += [("Past race", p) for p in (f"/races/{last}", f"/races/{last}/winner-prediction",
                                             f"/races/{last}/position-battle", f"/races/{last}/strategy",
                                             f"/races/{last}/qualifying", f"/races/{last}/long-runs",
                                             f"/strategy/winning?race_id={last}",
                                             f"/races/{last}/race-trace", f"/races/{last}/ideal-lap",
                                             f"/races/{last}/overtakes", f"/races/{last}/undercuts")]
    calls += [("Records", "/records/history")]

    for page, path in calls:
        started = time.perf_counter()
        try:
            response = client.get(path)
        except Exception:
            seconds = time.perf_counter() - started
            tb = traceback.format_exc()
            print(f"  CRASH {seconds:6.1f}s  {path}  [{page}]")
            print("      " + "\n      ".join(tb.strip().splitlines()[-30:]))
            problem(f"{path} crashes ({tb.strip().splitlines()[-1][:160]}); breaks: {page}")
            continue
        seconds = time.perf_counter() - started
        note = "" if response.status_code == 200 else response.text[:180].replace("\n", " ")
        print(f"  {response.status_code:>5} {seconds:6.1f}s  {path}  {note}", flush=True)
        if response.status_code >= 500:
            problem(f"{path} answers {response.status_code}; breaks: {page}")
        elif seconds > SLOW_SECONDS:
            problem(f"{path} is slow ({seconds:.0f}s); {page} waits for it")
    print("  (404 with a reason is normal: it means 'no data for this yet', and the website shows that)")
    engine.dispose()


def deep_sweep(url: str, all_races: bool) -> None:
    section("4b. every API route, real and bad ids (api_sweep_v1)")
    try:
        from fastapi.testclient import TestClient
    except Exception as exc:                              # httpx missing
        print(f"  skipped: {exc}")
        return
    import logging

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import api_sweep_v1
    from app.database import get_db
    from app.main import app

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apex21").setLevel(logging.WARNING)
    engine = create_engine(url, connect_args={"options": "-c statement_timeout=120000"})
    Session = sessionmaker(bind=engine, autoflush=False)

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app, raise_server_exceptions=False)
    print(f"  {'every race' if all_races else 'the latest 8 races and one per season'}; "
          "progress every 200 calls, failures as they happen", flush=True)
    calls = {"n": 0}

    class Counting:
        def request(self, *a, **kw):
            calls["n"] += 1
            if calls["n"] % 200 == 0:
                print(f"  ... {calls['n']} calls", flush=True)
            return client.request(*a, **kw)

    with engine.connect() as db:
        report = api_sweep_v1.sweep(Counting(), app, db, all_races=all_races, log=print)
    api_sweep_v1.print_report(report)
    for r in report.failures:
        problem(f"{r.method} {r.url} -> {r.status}: {r.error[:160]}")
    if report.slow:
        print(f"  ({len(report.slow)} answer(s) took over {api_sweep_v1.SLOW_SECONDS:.0f}s; listed above)")
    app.dependency_overrides.pop(get_db, None)
    engine.dispose()


def _counts(session) -> tuple[int, int]:
    try:
        results = len(session.results) if session.results is not None else 0
    except Exception:
        results = 0
    try:
        laps = len(session.laps)
    except Exception:
        laps = 0
    return results, laps


def _cache_dir() -> str:
    """The pipeline's FastF1 cache: FASTF1_CACHE_DIR, else this folder's cache/, else the main
    folder's (the website copy of the project usually has none of its own)."""
    if os.getenv("FASTF1_CACHE_DIR"):
        return str(Path(os.environ["FASTF1_CACHE_DIR"]).expanduser())
    own, main = ROOT / "cache", ROOT.parent / "F1-Apex-Analytics" / "cache"
    return str(main if not own.exists() and main.exists() else own)


def fastf1_check(race: dict | None) -> None:
    section("5. FastF1: is the next race's data published, and is it in the database?")
    if not race:
        print("  no next race")
        return
    import logging

    import fastf1

    logging.getLogger("fastf1").setLevel(logging.ERROR)
    cache = _cache_dir()
    print(f"  FastF1 {fastf1.__version__}, saved copies in {cache}")
    if not Path(cache).exists():
        print("  (no FastF1 cache folder there, so nothing is saved yet)")
    else:
        fastf1.Cache.enable_cache(cache)
    try:
        event = fastf1.get_event_schedule(race["season_year"]).get_event_by_round(race["round_number"])
    except Exception as exc:
        problem(f"FastF1 cannot load the {race['season_year']} calendar: {type(exc).__name__}: {exc}")
        return
    reachable = True
    for host, url in (("F1 live timing", f"https://livetiming.formula1.com/static/{race['season_year']}/Index.json"),
                      ("Jolpica (results)", f"https://api.jolpi.ca/ergast/f1/{race['season_year']}.json")):
        status, seconds, _ = fetch(url, timeout=20)
        print(f"  {host:<18} -> {status} in {seconds:.1f}s")
        if not isinstance(status, int) or status >= 400:
            reachable = False
            problem(f"this computer cannot reach {host} ({url.split('/')[2]}): {status}")
    if not reachable:
        print("  skipping the session check: FastF1 needs both")
        return
    now = datetime.now(timezone.utc)
    in_db = race.get("sessions_in_db", {})
    season, rnd = race["season_year"], race["round_number"]
    print("  per session: what the database has | what F1 publishes now | FastF1's saved copy")
    for n in range(1, 6):
        name, start = event.get(f"Session{n}"), event.get(f"Session{n}DateUtc")
        if not name or start is None or str(start) == "NaT":
            continue
        code = SESSION_CODES.get(name, name)
        start = start.to_pydatetime().replace(tzinfo=timezone.utc)
        label = f"{code:<4} {start:%a %H:%M} UTC"
        if start > now:
            print(f"  {label}  not run yet")
            continue
        row = in_db.get(code) or {}
        db_laps, db_results = row.get("laps", 0), row.get("race_results", 0) + row.get("quali_results", 0)
        fresh, error = _load(fastf1, season, rnd, name, "fresh")
        saved, saved_dir = (None, None)
        if Path(cache).exists():
            saved, _ = _load(fastf1, season, rnd, name, "saved")
            saved_dir = _saved_dir(cache, fastf1, season, rnd, name)
        saved_text = (f"{saved[0]} drivers {saved[1]:>5} laps" if saved and saved_dir else "none")
        fresh_text = f"{fresh[0]} drivers {fresh[1]:>5} laps" if fresh else f"error {error}"
        print(f"  {label}  db {db_results:>2} results {db_laps:>5} laps | F1 {fresh_text} | saved {saved_text}")
        if not fresh or not fresh[1]:
            if not db_laps:
                problem(f"{code}: F1 has not published usable data for it yet ({error or 'FastF1 got no laps'})")
        elif db_laps >= 0.9 * fresh[1] and (db_results or code in ("FP1", "FP2", "FP3", "SQ")):   # no results kept
            continue
        elif saved_dir and saved and saved[1] < 0.9 * fresh[1]:
            problem(f"{code}: FastF1's saved copy ({saved[1]} laps) is older than what F1 has now ({fresh[1]}), "
                    f"and the pipeline keeps reading that old copy (folder {saved_dir})")
        else:
            problem(f"{code}: F1 has it ({fresh[1]} laps) but the database has {db_laps} laps / {db_results} "
                    "results: the pipeline has not loaded it yet, or its last run failed")


def _load(fastf1, season: int, rnd: int, name: str, mode: str) -> tuple[tuple[int, int] | None, str | None]:
    """(drivers, laps) of one session: "fresh" from F1 now (FastF1's cache bypassed), "saved"
    from FastF1's saved copy only (nothing downloaded)."""
    try:
        if mode == "fresh":
            with fastf1.Cache.disabled():
                session = fastf1.get_session(season, rnd, name)
                session.load(laps=True, telemetry=False, weather=False, messages=False)
        else:
            fastf1.Cache.offline_mode(True)
            try:
                session = fastf1.get_session(season, rnd, name)
                session.load(laps=True, telemetry=False, weather=False, messages=False)
            finally:
                fastf1.Cache.offline_mode(False)
        return _counts(session), None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {str(exc)[:120]}"


def _saved_dir(cache: str, fastf1, season: int, rnd: int, name: str) -> str | None:
    """FastF1's folder of parsed data for this session, if it has one."""
    try:
        folder = Path(cache) / fastf1.get_session(season, rnd, name).api_path[len("/static/"):]
    except Exception:
        return None
    return str(folder) if folder.is_dir() and any(folder.iterdir()) else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skip-fastf1", action="store_true", help="skip section 5 (no internet needed)")
    parser.add_argument("--deep", action="store_true", help="also call every API route (section 4b)")
    parser.add_argument("--all-races", action="store_true", help="with --deep: every race, not a sample")
    args = parser.parse_args()
    # sections 4 and 4b call the API inside this script: no rate limit for them (the running
    # server keeps its own)
    os.environ["RATE_LIMIT_PER_MINUTE"] = "0"
    print(f"Apex21 API check, {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC")
    url = setup()
    race = None
    if url:
        race = database(url)
    server()
    if url:
        try:
            fan_data(url)
        except Exception:
            print(f"  (could not count: {traceback.format_exc().strip().splitlines()[-1]})")
        endpoints(url, race)
        if args.deep:
            try:
                deep_sweep(url, args.all_races)
            except Exception:
                problem("the API sweep itself failed (error below)")
                print(traceback.format_exc())
    if not args.skip_fastf1:
        try:
            fastf1_check(race)
        except Exception:
            problem("the FastF1 check itself failed (error below)")
            print(traceback.format_exc())
    section("PROBLEMS")
    for p in problems or ["none found"]:
        print(f"  - {p}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
