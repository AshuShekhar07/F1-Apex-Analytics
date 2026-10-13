"""API sweep: call every route of the API with the database's own ids and with bad ones, and
report any server error. Used by tests/test_api_sweep.py (fixture database), by
api_check_v1.py --deep (the real database) and before a deployment.

Every path and query parameter gets the real values from the database (every race, driver,
team, circuit, season) plus values that must fail cleanly: an id that does not exist, an
out-of-range season, an unknown session or a malformed date. A response is a failure when it
is a 5xx, when the route crashes, or when a 2xx/4xx body is not JSON. Slow responses are listed.
"""

from __future__ import annotations

import itertools
import json
import time
import traceback
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text

MISSING_ID = 999999
SLOW_SECONDS = 5.0
MAX_PER_ROUTE = 400


@dataclass
class Result:
    method: str
    url: str
    status: int | str
    seconds: float
    error: str = ""


@dataclass
class Report:
    results: list[Result] = field(default_factory=list)

    @property
    def failures(self) -> list[Result]:
        return [r for r in self.results if r.error]

    @property
    def slow(self) -> list[Result]:
        return [r for r in self.results if not r.error and r.seconds > SLOW_SECONDS]


def candidates(db: Any, *, all_races: bool = True) -> dict[str, list]:
    """Values to try for each parameter name, real ones first."""
    ids = lambda sql: [int(x) for (x,) in db.execute(text(sql))]  # noqa: E731
    races = ids("SELECT id FROM races ORDER BY race_date")
    if not all_races and len(races) > 12:
        # the latest 8 and one per earlier season
        per_season = ids("SELECT MAX(id) FROM races GROUP BY season_year")
        races = sorted(set(races[-8:]) | set(per_season))
    seasons = ids("SELECT DISTINCT season_year FROM races ORDER BY 1")
    drivers = ids("SELECT DISTINCT driver_id FROM race_entries ORDER BY 1")
    rounds = ids("SELECT DISTINCT round_number FROM races ORDER BY 1")[:3]
    dates = [str(d) for (d,) in db.execute(text("SELECT race_date FROM races ORDER BY race_date DESC LIMIT 2"))]
    return {
        "race_id": races + [MISSING_ID],
        "driver_id": drivers + [MISSING_ID],
        "driver1_id": drivers[:3] + [MISSING_ID],
        "driver2_id": drivers[1:4] + [MISSING_ID],
        "team_id": ids("SELECT id FROM teams ORDER BY 1") + [MISSING_ID],
        "track_id": ids("SELECT id FROM tracks ORDER BY 1") + [MISSING_ID],
        "season": seasons + [2018, 2100, 2017, 2101],
        "season_year": seasons + [2019, 2017],
        "round_number": rounds + [99],
        "session_type": ["FP1", "FP2", "FP3", "Q", "SQ", "S", "R", "XX"],
        "session": [None, "FP1", "FP2", "FP3", "SQ", "Q", "XX"],
        "prediction_type": ["drivers", "constructors", "champion", "next_race", "xx"],
        "race_date": dates + ["2030-01-01", "not-a-date"],
        "grid": [None, 1, 20],
        "start_compound": [None, "SOFT", "WET"],
        "limit": [None, 5],
        "offset": [None, 0],
        "teammates_only": [None, True],
        "era": [None, "era2_18inch_groundeffect", "xx"],
        "min_wet_races": [None, 1],
        "min_dry_races": [None],
        "include_qualifying": [None, True],
    }


def _fill(path: str, params: dict) -> str:
    for k, v in params.items():
        path = path.replace("{" + k + "}", str(v))
    return path


def _combos(op: dict, path_params: list[str], cands: dict[str, list]) -> list[tuple[dict, dict]]:
    """(path values, query values) combinations: every path value, each optional query param on
    its own (with the first real value of the others), required query params in full."""
    query = [p for p in op.get("parameters", []) if p["in"] == "query"]
    required = [p["name"] for p in query if p.get("required")]
    optional = [p["name"] for p in query if not p.get("required")]
    path_values = [dict(zip(path_params, combo)) for combo in
                   itertools.product(*[cands.get(p, [1]) for p in path_params])] if path_params else [{}]
    req_values = [dict(zip(required, combo)) for combo in
                  itertools.product(*[[v for v in cands.get(p, [1]) if v is not None] for p in required])] if required else [{}]
    out = []
    for pv in path_values:
        for rv in req_values:
            out.append((pv, rv))
            for name in optional:
                for v in cands.get(name, [None]):
                    if v is not None:
                        out.append((pv, {**rv, name: v}))
    # spread the sample over the whole list when it is long
    if len(out) > MAX_PER_ROUTE:
        step = len(out) / MAX_PER_ROUTE
        out = [out[int(i * step)] for i in range(MAX_PER_ROUTE)]
    return out


def sweep(client: Any, app: Any, db: Any, *, all_races: bool = True, what_if: bool = True,
          deprecated: bool = True, log=None) -> Report:
    report = Report()
    cands = candidates(db, all_races=all_races)
    spec = app.openapi()

    def call(method: str, url: str, **kwargs) -> None:
        started = time.perf_counter()
        try:
            resp = client.request(method, url, **kwargs)
            seconds = time.perf_counter() - started
            error = ""
            if resp.status_code >= 500:
                error = f"HTTP {resp.status_code}: {resp.text[:200]}"
            else:
                try:
                    json.loads(resp.text)
                except ValueError:
                    error = f"not JSON: {resp.text[:120]!r}"
            report.results.append(Result(method, url, resp.status_code, seconds, error))
        except Exception:
            tb = traceback.format_exc().strip().splitlines()
            report.results.append(Result(method, url, "CRASH", time.perf_counter() - started,
                                         " | ".join(tb[-6:])))
        if log and report.results[-1].error:
            log(f"  FAIL {method} {url}: {report.results[-1].error}")

    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            if method != "get" or (op.get("deprecated") and not deprecated):
                continue
            path_params = [p["name"] for p in op.get("parameters", []) if p["in"] == "path"]
            for pv, qv in _combos(op, path_params, cands):
                call("GET", _fill(path, pv), params={k: v for k, v in qv.items() if v is not None})
    # bad path values the type checks must reject cleanly
    for url in ("/races/abc", "/races/-1", "/races/0/race-trace", "/drivers/abc/profile", "/seasons/abcd/results",
                "/championship/99999", "/tracks/0/stats", "/races/1/practice/%00", "/nope"):
        call("GET", url)
    if what_if:
        races = [r for r in cands["race_id"] if r != MISSING_ID]
        forecast = db.execute(text("SELECT race_id FROM race_forecasts ORDER BY race_id DESC LIMIT 1")).scalar()
        for rid in [forecast, races[0], MISSING_ID] if forecast else [races[0], MISSING_ID]:
            call("POST", f"/races/{rid}/what-if", json={"safety_car_laps": [10, 11], "simulations": 100})
            call("POST", f"/races/{rid}/what-if", json={"vsc_laps": [5], "strategies": {}, "simulations": 100})
            call("POST", f"/races/{rid}/what-if", json={"simulations": 1})
            call("POST", f"/races/{rid}/what-if", content="not json", headers={"Content-Type": "application/json"})
    return report


def print_report(report: Report, out=print) -> None:
    by_status: dict[str, int] = {}
    for r in report.results:
        by_status[str(r.status)] = by_status.get(str(r.status), 0) + 1
    total = sum(r.seconds for r in report.results)
    out(f"  {len(report.results)} calls in {total:.0f}s; by status: "
        + ", ".join(f"{k}: {v}" for k, v in sorted(by_status.items())))
    for r in report.failures:
        out(f"  FAIL {r.method} {r.url} -> {r.status}: {r.error}")
    for r in sorted(report.slow, key=lambda r: -r.seconds)[:15]:
        out(f"  slow {r.seconds:5.1f}s {r.method} {r.url}")
