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
outlook depend on today. When it has changed and an answer is not computed again yet, the
answer saved last (up to STALE_SECONDS old) is sent at once, marked "stale", and computed again
in the background, one answer at a time; the next visitor gets the new one. So nobody waits
minutes after midnight or a pipeline run (RESPONSE_CACHE_STALE=0 waits for the new answer
instead; the warm-up script does). Health checks and the API's own pages are not cached. Off by
default in development (RESPONSE_CACHE=1 turns it on).
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
STALE_SECONDS = 7 * 24 * 3600    # an answer of an older data version is sent while new, up to this age

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
    """Least recently used answers, bounded by their total size. Each item is (data version,
    status, content type, body, computed at as a Unix time)."""

    def __init__(self, budget: int):
        self.budget, self.used = budget, 0
        self.items: OrderedDict[str, tuple[str, int, str, bytes, float]] = OrderedDict()
        self.lock = threading.Lock()

    def get(self, key: str):
        with self.lock:
            item = self.items.get(key)
            if item:
                self.items.move_to_end(key)
            return item

    def put(self, key: str, version: str, status: int, content_type: str, body: bytes,
            computed_at: float | None = None) -> None:
        with self.lock:
            old = self.items.pop(key, None)
            if old:
                self.used -= len(old[3])
            if len(body) > self.budget:
                return
            self.items[key] = (version, status, content_type, body, time.time() if computed_at is None else computed_at)
            self.used += len(body)
            while self.used > self.budget:
                _, dropped = self.items.popitem(last=False)
                self.used -= len(dropped[3])


class ResponseCache:
    """ASGI middleware. Add it before the CORS middleware, so CORS headers are still set per
    request on saved answers."""

    def __init__(self, app, engine_getter, enabled, serve_stale=lambda: True):
        self.app = app
        self.engine_getter = engine_getter        # a callable, so the engine is made lazily
        self.enabled = enabled                    # a callable too (tests switch it)
        self.serve_stale = serve_stale
        self.memory = _Memory(MEMORY_BYTES)
        self.locks: dict[tuple[int, str], asyncio.Lock] = {}
        self.slots: dict[int, asyncio.Semaphore] = {}   # per event loop: one background refresh at a time
        self.refreshing: set[str] = set()
        self.tasks: set[asyncio.Task] = set()
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

    def load(self, key: str):
        """The answer saved last for this key, of any data version."""
        with self._engine().begin() as conn:
            self._ensure_table(conn)
            row = conn.execute(text("SELECT version, status, content_type, body, EXTRACT(EPOCH FROM computed_at) "
                                    "FROM api_response_cache WHERE cache_key = :k"), {"k": key}).first()
        return (row[0], int(row[1]), row[2], bytes(row[3]), float(row[4])) if row else None

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
            if self.stores % 200 == 1:            # now and then, drop answers too old to be sent
                conn.execute(text("DELETE FROM api_response_cache WHERE version <> :v "
                                  "AND computed_at < now() - make_interval(secs => :age)"),
                             {"v": version, "age": STALE_SECONDS})

    async def _lookup(self, key: str, version: str, use_memory: bool = True):
        """The answer saved last for this key (memory, else the database), of any data version."""
        item = self.memory.get(key) if use_memory else None
        if item and item[0] == version:
            return item
        try:                                      # another process may have saved a newer one
            row = await to_thread.run_sync(self.load, key)
        except Exception:
            log.exception("reading a saved answer failed")
            return item
        if row and (not item or row[0] == version or row[4] >= item[4]):
            self.memory.put(key, *row)
            return row
        return item

    @staticmethod
    def _sendable(item, version: str) -> bool:
        return item[0] == version or time.time() - item[4] <= STALE_SECONDS

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
        if hit and hit[0] != version and self.serve_stale() and self._sendable(hit, version):
            self._refresh_later(scope, key, version)
            await self._send_saved(send, hit, b"stale")
            return
        if not hit or hit[0] != version:
            # one lock per key and event loop (a server has one loop; a test client may have several)
            slot = (id(asyncio.get_running_loop()), key)
            lock = self.locks.setdefault(slot, asyncio.Lock())
            try:
                async with lock:                  # the same request at once: computed once
                    hit = await self._lookup(key, version)
                    if not hit or hit[0] != version:
                        await self._compute(scope, receive, send, key, version)
                        return
            finally:
                if not lock.locked():
                    self.locks.pop(slot, None)
        await self._send_saved(send, hit, b"hit")

    @staticmethod
    async def _send_saved(send, item, label: bytes) -> None:
        _, status, content_type, body, _ = item
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", content_type.encode("latin-1")),
                                (b"content-length", str(len(body)).encode()),
                                (b"x-apex21-cache", label)]})
        await send({"type": "http.response.body", "body": body})

    # --- background refresh of a stale answer -------------------------------------------------
    def _refresh_later(self, scope, key: str, version: str) -> None:
        if key in self.refreshing:
            return
        self.refreshing.add(key)
        request = {**scope, "state": dict(scope.get("state") or {})}
        task = asyncio.get_running_loop().create_task(self._refresh(request, key, version))
        self.tasks.add(task)                      # keep a reference until it is done
        task.add_done_callback(self.tasks.discard)

    async def _refresh(self, scope, key: str, version: str) -> None:
        loop_id = id(asyncio.get_running_loop())
        slot = self.slots.setdefault(loop_id, asyncio.Semaphore(1))
        try:
            async with slot:                      # one at a time: a small server keeps answering
                version = await to_thread.run_sync(self.current_version)   # it may have moved on while queued
                hit = await self._lookup(key, version, use_memory=False)
                if hit and hit[0] == version:     # computed meanwhile (e.g. by the warm-up script)
                    return
                sent = False

                async def receive():              # a GET without a body, from a client that stays
                    nonlocal sent
                    if not sent:
                        sent = True
                        return {"type": "http.request", "body": b"", "more_body": False}
                    await asyncio.Event().wait()

                async def discard(message):
                    pass

                await self._compute(scope, receive, discard, key, version)
        except Exception:
            log.exception("refreshing a saved answer failed: %s", key)
        finally:
            self.refreshing.discard(key)

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
