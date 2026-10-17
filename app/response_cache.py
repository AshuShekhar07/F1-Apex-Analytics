"""Saved API answers, so a small free server stays fast.

Several answers are expensive to compute (the next race's win chances fit a model over every
race since 2018; the car tables and the strategy history read all laps). On a free host with a
tenth of a CPU that takes minutes, and every restart starts again. This keeps each successful
GET answer in the database, labelled with the data version, so:

  * it is computed once per data version, then read in milliseconds, also after a restart;
  * the same request arriving several times at once is computed once (the rest wait for it);
  * "not found" answers are kept too (no forecast for that race yet...): they depend on the
    data just the same, and finding out can be as expensive as an answer;
  * warm_api_cache_v1.py can compute the answers on a fast machine before visitors ask.

The data version changes when race results, qualifying, laps, races or sessions change, a
pipeline run finishes, or the (database) date changes, since the next race and the weather
outlook depend on today; answers of an older version are never served. Health checks and the
API's own pages are not cached. Off by default in development (RESPONSE_CACHE=1 turns it on).
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import OrderedDict
from urllib.parse import parse_qsl, urlencode

from anyio import to_thread
from sqlalchemy import text

log = logging.getLogger("apex21.cache")

SKIP_PATHS = frozenset({"/", "/health", "/about", "/version", "/openapi.json", "/docs", "/redoc"})
SKIP_PREFIXES = ("/static/", "/docs/")
VERSION_SECONDS = 30            # how long a process trusts the data version it read
MEMORY_BYTES = 24 * 1024 * 1024  # answers also kept in memory, up to this much
MAX_BODY = 4 * 1024 * 1024       # larger answers are not saved

SAVED_STATUSES = (200, 404)

CREATE = """
CREATE TABLE IF NOT EXISTS api_response_cache (
    cache_key    TEXT PRIMARY KEY,
    version      TEXT NOT NULL,
    status       INTEGER NOT NULL DEFAULT 200,
    content_type TEXT NOT NULL,
    body         BYTEA NOT NULL,
    computed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)"""

VERSION_SQL = """
SELECT (SELECT MAX(id) FROM race_results), (SELECT MAX(id) FROM qualifying_results),
       (SELECT MAX(id) FROM laps), (SELECT MAX(id) FROM races), (SELECT MAX(id) FROM sessions),
       CASE WHEN to_regclass('public.pipeline_runs') IS NULL THEN NULL
            ELSE (SELECT MAX(finished_at)::text FROM pipeline_runs) END,
       CURRENT_DATE"""


def cache_key(path: str, query: str) -> str:
    """The path with its query parameters in a fixed order."""
    params = sorted(parse_qsl(query, keep_blank_values=True))
    return f"{path}?{urlencode(params)}" if params else path


def cacheable(method: str, path: str) -> bool:
    return method == "GET" and path not in SKIP_PATHS and not path.startswith(SKIP_PREFIXES)


class _Memory:
    """Least recently used answers, bounded by their total size."""

    def __init__(self, budget: int):
        self.budget, self.used = budget, 0
        self.items: OrderedDict[str, tuple[str, int, str, bytes]] = OrderedDict()
        self.lock = threading.Lock()

    def get(self, key: str, version: str):
        with self.lock:
            item = self.items.get(key)
            if not item or item[0] != version:
                return None
            self.items.move_to_end(key)
            return item

    def put(self, key: str, version: str, status: int, content_type: str, body: bytes) -> None:
        with self.lock:
            old = self.items.pop(key, None)
            if old:
                self.used -= len(old[3])
            if len(body) > self.budget:
                return
            self.items[key] = (version, status, content_type, body)
            self.used += len(body)
            while self.used > self.budget:
                _, dropped = self.items.popitem(last=False)
                self.used -= len(dropped[3])


class ResponseCache:
    """ASGI middleware. Add it before the CORS middleware, so CORS headers are still set per
    request on saved answers."""

    def __init__(self, app, engine_getter, enabled):
        self.app = app
        self.engine_getter = engine_getter        # a callable, so the engine is made lazily
        self.enabled = enabled                    # a callable too (tests switch it)
        self.memory = _Memory(MEMORY_BYTES)
        self.locks: dict[tuple[int, str], asyncio.Lock] = {}
        self.version: tuple[float, str] | None = None
        self.table_ready = False
        self.stores = 0

    # --- database ---------------------------------------------------------------------------
    def _engine(self):
        return self.engine_getter()

    def current_version(self) -> str:
        now = time.monotonic()
        if self.version and now - self.version[0] < VERSION_SECONDS:
            return self.version[1]
        with self._engine().connect() as conn:
            row = conn.execute(text(VERSION_SQL)).one()
        value = "|".join("" if v is None else str(v) for v in row)
        self.version = (now, value)
        return value

    def _ensure_table(self, conn) -> None:
        if not self.table_ready:
            conn.execute(text(CREATE))
            conn.execute(text("ALTER TABLE api_response_cache ADD COLUMN IF NOT EXISTS status INTEGER NOT NULL DEFAULT 200"))
            self.table_ready = True

    def load(self, key: str, version: str):
        with self._engine().begin() as conn:
            self._ensure_table(conn)
            row = conn.execute(text("SELECT status, content_type, body FROM api_response_cache "
                                    "WHERE cache_key = :k AND version = :v"), {"k": key, "v": version}).first()
        return (version, int(row[0]), row[1], bytes(row[2])) if row else None

    def save(self, key: str, version: str, status: int, content_type: str, body: bytes) -> None:
        with self._engine().begin() as conn:
            self._ensure_table(conn)
            conn.execute(text("""
                INSERT INTO api_response_cache (cache_key, version, status, content_type, body, computed_at)
                VALUES (:k, :v, :s, :t, :b, now())
                ON CONFLICT (cache_key) DO UPDATE
                SET version = EXCLUDED.version, status = EXCLUDED.status, content_type = EXCLUDED.content_type,
                    body = EXCLUDED.body, computed_at = EXCLUDED.computed_at"""),
                         {"k": key, "v": version, "s": status, "t": content_type, "b": body})
            self.stores += 1
            if self.stores % 200 == 1:            # now and then, drop answers of older versions
                conn.execute(text("DELETE FROM api_response_cache WHERE version <> :v"), {"v": version})

    async def _lookup(self, key: str, version: str):
        hit = self.memory.get(key, version)
        if hit:
            return hit
        try:
            hit = await to_thread.run_sync(self.load, key, version)
        except Exception:
            log.exception("reading a saved answer failed; computing it")
            return None
        if hit:
            self.memory.put(key, *hit)
        return hit

    # --- ASGI ---------------------------------------------------------------------------------
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.enabled() or not cacheable(scope["method"], scope["path"]):
            await self.app(scope, receive, send)
            return
        key = cache_key(scope["path"], scope.get("query_string", b"").decode("latin-1"))
        try:
            version = await to_thread.run_sync(self.current_version)
        except Exception:
            log.exception("data version unavailable; answering without the cache")
            await self.app(scope, receive, send)
            return

        hit = await self._lookup(key, version)
        if not hit:
            # one lock per key and event loop (a server has one loop; a test client may have several)
            slot = (id(asyncio.get_running_loop()), key)
            lock = self.locks.setdefault(slot, asyncio.Lock())
            try:
                async with lock:                  # the same request at once: computed once
                    hit = await self._lookup(key, version)
                    if not hit:
                        await self._compute(scope, receive, send, key, version)
                        return
            finally:
                if not lock.locked():
                    self.locks.pop(slot, None)
        _, status, content_type, body = hit
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", content_type.encode("latin-1")),
                                (b"content-length", str(len(body)).encode()),
                                (b"x-apex21-cache", b"hit")]})
        await send({"type": "http.response.body", "body": body})

    async def _compute(self, scope, receive, send, key: str, version: str) -> None:
        start: dict = {}
        chunks: list[bytes] = []

        async def capture(message):
            if message["type"] == "http.response.start":
                start.update(message)
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))
            await send(message)

        await self.app(scope, receive, capture)
        status = start.get("status")
        if status not in SAVED_STATUSES:
            return
        body = b"".join(chunks)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in start.get("headers", [])}
        content_type = headers.get("content-type", "application/json")
        if len(body) > MAX_BODY or "json" not in content_type:
            return
        self.memory.put(key, version, status, content_type, body)
        try:
            await to_thread.run_sync(self.save, key, version, status, content_type, body)
        except Exception:
            log.exception("saving an answer failed (it is still kept in memory)")
