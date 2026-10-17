"""Saved API answers (app/response_cache.py): computed once per data version, read afterwards
(also by a new process), never for errors, health checks or other methods. After new data the
last answer is sent at once and computed again in the background."""

from __future__ import annotations

import time

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.response_cache import ResponseCache, _Memory, cache_key, cacheable


def test_cache_key_orders_query_parameters():
    assert cache_key("/strategy/predict", "track_id=5&race_date=2026-10-25") == \
        cache_key("/strategy/predict", "race_date=2026-10-25&track_id=5")
    assert cache_key("/races", "") == "/races"
    assert cache_key("/races", "season=2026") != cache_key("/races", "season=2025")


def test_what_is_cached():
    assert cacheable("GET", "/races/next/winner-prediction")
    assert not cacheable("POST", "/races/1/what-if")
    for path in ["/", "/health", "/docs", "/openapi.json", "/static/team_logos/1.png"]:
        assert not cacheable("GET", path)


def test_memory_is_bounded_by_size():
    m = _Memory(budget=10)
    m.put("a", "v1", 200, "application/json", b"12345")
    m.put("b", "v1", 200, "application/json", b"12345")
    m.put("c", "v1", 200, "application/json", b"12345")   # over budget: the oldest goes
    assert m.get("a") is None and m.get("c")[0] == "v1"
    m.put("big", "v1", 200, "application/json", b"x" * 11)  # larger than the budget: not kept
    assert m.get("big") is None


@pytest.fixture()
def cached_app(fixture_db_url):
    from sqlalchemy import create_engine, text

    engine = create_engine(fixture_db_url)
    calls = {"n": 0}

    async def expensive(request):
        calls["n"] += 1
        return JSONResponse({"answer": 42, "season": request.query_params.get("season")})

    async def counted(request):                  # a different answer each time it is computed
        calls["n"] += 1
        return JSONResponse({"computed": calls["n"]})

    async def missing(request):
        calls["n"] += 1
        return JSONResponse({"detail": "no"}, status_code=404)

    async def broken(request):
        calls["n"] += 1
        return JSONResponse({"detail": "boom"}, status_code=500)

    inner = Starlette(routes=[Route("/expensive", expensive), Route("/missing", missing), Route("/broken", broken),
                              Route("/health", expensive), Route("/counted", counted)])

    def make(stale=True):
        return ResponseCache(inner, engine_getter=lambda: engine, enabled=lambda: True, serve_stale=lambda: stale)

    yield make, calls, engine
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS api_response_cache"))
    engine.dispose()


def test_computed_once_then_read_also_after_a_restart(cached_app):
    make, calls, _ = cached_app
    client = TestClient(make())
    first = client.get("/expensive?season=2026")
    second = client.get("/expensive?season=2026")
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json() == {"answer": 42, "season": "2026"}
    assert first.headers.get("x-apex21-cache") is None and second.headers.get("x-apex21-cache") == "hit"
    assert calls["n"] == 1
    restarted = TestClient(make())                         # a new process: read from the database
    assert restarted.get("/expensive?season=2026").headers.get("x-apex21-cache") == "hit"
    assert calls["n"] == 1
    client.get("/expensive?season=2025")                  # other parameters: their own answer
    assert calls["n"] == 2


def _newer_data(cache):
    cache.version = (time.monotonic(), cache.version[1] + " and newer")   # as if new results were loaded


def _wait_for_background(cache):
    deadline = time.time() + 10
    while cache.tasks and time.time() < deadline:
        time.sleep(0.02)
    assert not cache.tasks


def test_new_data_version_computes_again_without_stale_answers(cached_app):
    make, calls, _ = cached_app
    cache = make(stale=False)
    client = TestClient(cache)
    client.get("/expensive")
    _newer_data(cache)
    assert client.get("/expensive").headers.get("x-apex21-cache") is None
    assert calls["n"] == 2


def test_after_new_data_the_last_answer_is_sent_and_computed_again_in_the_background(cached_app):
    make, calls, _ = cached_app
    cache = make()
    with TestClient(cache) as client:             # one event loop, so the background task can run
        assert client.get("/counted").json() == {"computed": 1}
        _newer_data(cache)
        stale = client.get("/counted")
        assert stale.headers.get("x-apex21-cache") == "stale" and stale.json() == {"computed": 1}
        client.get("/counted")                    # asked again meanwhile: refreshed only once
        _wait_for_background(cache)
        fresh = client.get("/counted")
        assert fresh.headers.get("x-apex21-cache") == "hit" and fresh.json() == {"computed": 2}
    assert calls["n"] == 2
    restarted = TestClient(make())                # the new answer was saved in the database
    assert restarted.get("/counted").json() == {"computed": 2}


def test_a_newer_answer_saved_by_another_process_is_used(cached_app):
    make, calls, _ = cached_app
    server, warmer = make(), make(stale=False)    # the API server and warm_api_cache_v1.py
    server_client, warmer_client = TestClient(server), TestClient(warmer)
    server_client.get("/counted")                 # computed once, kept in the server's memory
    _newer_data(server)
    warmer.version = server.version
    assert warmer_client.get("/counted").json() == {"computed": 2}
    hit = server_client.get("/counted")
    assert hit.headers.get("x-apex21-cache") == "hit" and hit.json() == {"computed": 2}
    assert calls["n"] == 2


def test_an_answer_older_than_the_stale_limit_is_not_sent(cached_app):
    from sqlalchemy import text

    make, calls, engine = cached_app
    TestClient(make()).get("/counted")
    with engine.begin() as conn:
        conn.execute(text("UPDATE api_response_cache SET computed_at = now() - interval '8 days'"))
    cache = make()
    client = TestClient(cache)
    client.get("/expensive")                      # reads the data version
    _newer_data(cache)
    answer = client.get("/counted")
    assert answer.headers.get("x-apex21-cache") is None and answer.json() == {"computed": 3}


def test_not_found_is_saved_with_its_status_health_never(cached_app):
    make, calls, _ = cached_app
    client = TestClient(make())
    first, second = client.get("/missing"), client.get("/missing")
    assert first.status_code == second.status_code == 404
    assert second.headers.get("x-apex21-cache") == "hit" and second.json() == {"detail": "no"}
    for _ in range(2):
        assert client.get("/health").headers.get("x-apex21-cache") is None
    assert calls["n"] == 3


def test_server_errors_are_not_saved(cached_app):
    make, calls, _ = cached_app
    client = TestClient(make(), raise_server_exceptions=False)
    for _ in range(2):
        assert client.get("/broken").status_code == 500
    assert calls["n"] == 2
