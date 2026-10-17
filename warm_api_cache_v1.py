"""Compute the website's API answers on a fast machine and save them in the database, so a small
free API server (a tenth of a CPU) only reads them (app/response_cache.py).

It asks the API, in this process, for what the pages show: the race hub (next race), every
season's championship, results and races, the cars, tracks and records, and the race pages of
the latest season (all seasons with --all). Answers already saved for the current data version
are only read, so a second run is quick. Run it after new data is loaded (the pipeline workflow
does), or by hand:

    DATABASE_URL=postgresql://... python warm_api_cache_v1.py          # what visitors see first
    DATABASE_URL=postgresql://... python warm_api_cache_v1.py --all    # also every older race page
"""

from __future__ import annotations

import argparse
import os
import sys
import time

# the saved answers must be exactly what the production API sends
os.environ["APP_ENV"] = "production"
os.environ["RESPONSE_CACHE"] = "1"
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
    args = parser.parse_args()
    if not os.getenv("DATABASE_URL"):
        print("Set DATABASE_URL to the database the website's API uses.")
        return 1

    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    done: dict[str, object] = {}                     # path -> its answer (None without one)
    tally = {"saved before": 0, "computed": 0, "no answer": 0}
    started = time.time()

    def get(path: str):
        if path in done:
            return done[path]
        done[path] = None
        t = time.time()
        try:
            r = client.get(path)
        except Exception as exc:                      # one failing page never stops the rest
            tally["no answer"] += 1
            print(f"  error    {path}  {type(exc).__name__}: {exc}")
            return None
        hit = r.headers.get("x-apex21-cache") == "hit"
        if r.status_code != 200:                      # "not found" is saved too: no data for it
            tally["no answer"] += 1
            return None
        tally["saved before" if hit else "computed"] += 1
        if not hit:
            print(f"  computed {path}  ({time.time() - t:.1f}s)")
        done[path] = r.json()
        return done[path]

    seasons = sorted(_seasons(), reverse=True)
    # the race hub: the next race and what its page asks for
    nxt = get("/races/next/winner-prediction") or {}
    next_id = nxt.get("race_id")
    if next_id:
        for p in PER_RACE:
            get(p.format(id=next_id))
        race = get(f"/races/{next_id}") or {}
        if race.get("track_id") and nxt.get("race_date"):
            get(f"/strategy/predict?track_id={race['track_id']}&race_date={nxt['race_date']}")
    for path in ["/records/live-predictions", "/records/history", "/records/calibration", "/cars", "/tracks",
                 "/drivers/wet-weather-ranking"]:
        get(path)
    latest = seasons[0] if seasons else None
    for season in seasons:
        get(f"/championship/{season}")
        get(f"/championship/{season}/history")
        get(f"/seasons/{season}/teammates")
        results = get(f"/seasons/{season}/results") or {}
        races = get(f"/races?season={season}") or []
        if season == latest:
            roster = get(f"/drivers/roster/{season}") or {}
            for d in roster.get("drivers", []):
                get(f"/drivers/{d['id']}/profile")
        if season == latest or args.all:
            for t in results.get("teams", []):
                get(f"/teams/{t['id']}/season/{season}")
            for r in races:
                for p in PER_RACE:
                    get(p.format(id=r["id"]))
    for t in get("/tracks") or []:
        get(f"/tracks/{t['id']}/stats")
        get(f"/tracks/{t['id']}/overtaking-index")
    for car in (get("/cars") or {}).get("cars", []):
        get(f"/cars/{car['key']}")

    print(f"{len(done)} pages asked: {tally['computed']} computed now, {tally['saved before']} already saved, "
          f"{tally['no answer']} without an answer (no data for them) in {time.time() - started:.0f}s")
    return 0


def _seasons() -> list[int]:
    from sqlalchemy import text

    from app.database import engine
    with engine.connect() as conn:
        return [int(s) for (s,) in conn.execute(text("SELECT DISTINCT season_year FROM races ORDER BY 1"))]


if __name__ == "__main__":
    sys.exit(main())
