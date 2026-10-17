"""Compute the website's API answers on a fast machine and save them in the database, so a small
free API server (a tenth of a CPU) only reads them (app/response_cache.py).

It asks the API, in this process, for what the pages show: the race hub (next race), every
season's championship, results and races, the cars, tracks and records, and the race pages of
the latest season (all seasons with --all). Answers already saved for the current data version
are only read, so a second run is quick. Several pages are computed at once (--workers): most of
the time goes on round trips to the database, which can be on another continent. Run it after
new data is loaded (the pipeline workflow does), or by hand:

    DATABASE_URL=postgresql://... python warm_api_cache_v1.py          # what visitors see first
    DATABASE_URL=postgresql://... python warm_api_cache_v1.py --all    # also every older race page

Keep --workers small for a database with few connections (Supabase's session pooler allows 15,
shared with the API server); each worker uses one or two.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor

# the saved answers must be exactly what the production API sends
os.environ["APP_ENV"] = "production"
os.environ["RESPONSE_CACHE"] = "1"
os.environ["RESPONSE_CACHE_STALE"] = "0"       # compute the new answer, never send the last one
os.environ["SERVE_BRAND_ASSETS"] = "false"
os.environ["RATE_LIMIT_PER_MINUTE"] = "0"
os.environ["WARM_CACHES"] = "false"

PER_RACE = ["/races/{id}", "/races/{id}/sessions", "/races/{id}/forecast", "/races/{id}/winner-prediction",
            "/races/{id}/qualifying", "/races/{id}/long-runs", "/races/{id}/ideal-lap", "/races/{id}/strategy",
            "/races/{id}/position-battle", "/races/{id}/race-trace", "/races/{id}/overtakes", "/races/{id}/undercuts",
            "/strategy/winning?race_id={id}", "/strategy/winning/pre-start?race_id={id}"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--all", action="store_true", help="also every race page of earlier seasons")
    parser.add_argument("--workers", type=int, default=4, help="pages computed at once (default 4)")
    args = parser.parse_args()
    if not os.getenv("DATABASE_URL"):
        print("Set DATABASE_URL to the database the website's API uses.")
        return 1

    from fastapi.testclient import TestClient

    from app.main import app

    local = threading.local()                        # a client per thread
    lock = threading.Lock()
    pages: dict[str, Future] = {}                    # path -> its answer (None without one)
    tally = {"saved before": 0, "computed": 0, "no answer": 0}
    started = time.time()
    pool = ThreadPoolExecutor(max_workers=max(1, args.workers))

    def count(what: str) -> None:
        with lock:
            tally[what] += 1

    def fetch(path: str):
        if not hasattr(local, "client"):
            local.client = TestClient(app)
        t = time.time()
        try:
            r = local.client.get(path)
        except Exception as exc:                      # one failing page never stops the rest
            count("no answer")
            print(f"  error    {path}  {type(exc).__name__}: {exc}")
            return None
        hit = r.headers.get("x-apex21-cache") == "hit"
        if r.status_code != 200:                      # "not found" is saved too: no data for it
            count("no answer")
            return None
        count("saved before" if hit else "computed")
        if not hit:
            print(f"  computed {path}  ({time.time() - t:.1f}s)")
        return r.json()

    def ask(path: str) -> None:
        """Computed by the workers; nothing waits for it."""
        with lock:
            if path not in pages:
                pages[path] = pool.submit(fetch, path)

    def get(path: str):
        """The answer, needed to find further pages: asked here at once, not behind the queue."""
        with lock:
            future = pages.get(path)
            mine = future is None
            if mine:
                future = pages[path] = Future()
        if mine:
            future.set_result(fetch(path))
        return future.result()

    seasons = sorted(_seasons(), reverse=True)
    # the race hub: the next race and what its page asks for
    nxt = get("/races/next/winner-prediction") or {}
    next_id = nxt.get("race_id")
    if next_id:
        race = get(f"/races/{next_id}") or {}
        for p in PER_RACE:
            ask(p.format(id=next_id))
        if race.get("track_id") and nxt.get("race_date"):
            ask(f"/strategy/predict?track_id={race['track_id']}&race_date={nxt['race_date']}")
    for path in ["/records/live-predictions", "/records/history", "/records/calibration", "/cars", "/tracks",
                 "/drivers/wet-weather-ranking"]:
        ask(path)
    latest = seasons[0] if seasons else None
    for season in seasons:
        ask(f"/championship/{season}")
        ask(f"/championship/{season}/history")
        ask(f"/seasons/{season}/teammates")
        results = get(f"/seasons/{season}/results") or {}
        races = get(f"/races?season={season}") or []
        if season == latest:
            roster = get(f"/drivers/roster/{season}") or {}
            for d in roster.get("drivers", []):
                ask(f"/drivers/{d['id']}/profile")
        if season == latest or args.all:
            for t in results.get("teams", []):
                ask(f"/teams/{t['id']}/season/{season}")
            for r in races:
                for p in PER_RACE:
                    ask(p.format(id=r["id"]))
    for t in get("/tracks") or []:
        ask(f"/tracks/{t['id']}/stats")
        ask(f"/tracks/{t['id']}/overtaking-index")
    for car in (get("/cars") or {}).get("cars", []):
        ask(f"/cars/{car['key']}")
    pool.shutdown(wait=True)

    print(f"{len(pages)} pages asked: {tally['computed']} computed now, {tally['saved before']} already saved, "
          f"{tally['no answer']} without an answer (no data for them) in {time.time() - started:.0f}s")
    return 0


def _seasons() -> list[int]:
    from sqlalchemy import text

    from app.database import engine
    with engine.connect() as conn:
        return [int(s) for (s,) in conn.execute(text("SELECT DISTINCT season_year FROM races ORDER BY 1"))]


if __name__ == "__main__":
    sys.exit(main())
