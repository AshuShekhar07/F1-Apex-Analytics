"""Saved API answers (app/response_cache.py): computed once per data version, read afterwards
(also by a new process), never for errors, health checks or other methods."""

from __future__ import annotations

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
    assert m.get("a", "v1") is None and m.get("c", "v1") is not None
    assert m.get("c", "v2") is None                          # another data version is a miss
    m.put("big", "v1", 200, "application/json", b"x" * 11)  # larger than the budget: not kept
    assert m.get("big", "v1") is None


@pytest.fixture()
def cached_app(fixture_db_url):
    from sqlalchemy import create_engine, text

    engine = create_engine(fixture_db_url)
    calls = {"n": 0}

    async def expensive(request):
        calls["n"] += 1
        return JSONResponse({"answer": 42, "season": request.query_params.get("season")})

    async def missing(request):
        calls["n"] += 1
        return JSONResponse({"detail": "no"}, status_code=404)

    async def broken(request):
        calls["n"] += 1
        return JSONResponse({"detail": "boom"}, status_code=500)

    inner = Starlette(routes=[Route("/expensive", expensive), Route("/missing", missing), Route("/broken", broken),
                              Route("/health", expensive)])

    def make():
        return ResponseCache(inner, engine_getter=lambda: engine, enabled=lambda: True)

    yield make, calls
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS api_response_cache"))
    engine.dispose()


def test_computed_once_then_read_also_after_a_restart(cached_app):
    make, calls = cached_app
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


def test_new_data_version_computes_again(cached_app):
    make, calls = cached_app
    cache = make()
    client = TestClient(cache)
    client.get("/expensive")
    cache.version = (cache.version[0], "a newer data version")   # as if new results were loaded
    assert client.get("/expensive").headers.get("x-apex21-cache") is None
    assert calls["n"] == 2


def test_not_found_is_saved_with_its_status_health_never(cached_app):
    make, calls = cached_app
    client = TestClient(make())
    first, second = client.get("/missing"), client.get("/missing")
    assert first.status_code == second.status_code == 404
    assert second.headers.get("x-apex21-cache") == "hit" and second.json() == {"detail": "no"}
    for _ in range(2):
        assert client.get("/health").headers.get("x-apex21-cache") is None
    assert calls["n"] == 3


def test_server_errors_are_not_saved(cached_app):
    make, calls = cached_app
    client = TestClient(make(), raise_server_exceptions=False)
    for _ in range(2):
        assert client.get("/broken").status_code == 500
    assert calls["n"] == 2
