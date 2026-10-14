import logging
import math
import threading
import time
from collections import defaultdict, deque

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.routers import (cars, championship, compare, drivers, forecast, ops, practice, predict_and_compete, predictions, race_analysis, races,
                         records, seasons, strategy, teams, tracks, wet_weather)
from app.settings import scrub_brand_assets, settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("apex21")


def _finite(value):
    """NaN and infinity (not valid JSON) as null, anywhere in a JSON-like structure."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _finite(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_finite(v) for v in value]
    return value


class AppJSONResponse(JSONResponse):
    """Default response: drops logo/photo URLs unless SERVE_BRAND_ASSETS is on. A NaN or
    infinity in the data is sent as null (and logged) instead of failing the whole response."""

    def render(self, content) -> bytes:
        content = content if settings.serve_brand_assets else scrub_brand_assets(content)
        try:
            return super().render(content)
        except ValueError:
            log.warning("response contained NaN or infinity; sent as null")
            return super().render(_finite(content))


app = FastAPI(
    title="Apex21 API",
    description="F1 analytics and prediction platform backend. Independent fan project, not affiliated with "
                "Formula 1, the FIA or any team.",
    version="0.2.0",
    default_response_class=AppJSONResponse,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

if settings.serve_brand_assets:
    app.mount("/static", StaticFiles(directory="app/static"), name="static")


_hits: dict[str, deque] = defaultdict(deque)
_hits_lock = threading.Lock()


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    """Per-IP limit over a sliding minute (single process; behind a proxy the first
    X-Forwarded-For address is the client). /health is never limited."""
    limit = settings.rate_limit_per_minute
    if limit > 0 and request.url.path != "/health":
        forwarded = request.headers.get("x-forwarded-for", "")
        client = forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")
        now = time.monotonic()
        with _hits_lock:
            q = _hits[client]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= limit:
                return JSONResponse(status_code=429, content={"detail": "Too many requests, try again in a minute"},
                                    headers={"Retry-After": "60"})
            q.append(now)
    started = time.monotonic()
    response = await call_next(request)
    if settings.production:
        log.info("%s %s %s %.0fms", request.method, request.url.path, response.status_code,
                 (time.monotonic() - started) * 1000)
    return response


@app.on_event("startup")
def warm_caches():
    """Load the strategy history (every race's real stints) in the background so the first
    /strategy/predict request is not the slow one."""
    if not settings.warm_caches:
        return

    def load():
        try:
            import strategy_service_v7
            from app.database import SessionLocal
            with SessionLocal() as db:
                strategy_service_v7._history(db)
            log.info("strategy history warmed")
        except Exception:
            log.exception("warming the strategy history failed (it loads on first request instead)")
    threading.Thread(target=load, daemon=True).start()


app.include_router(races.router)
app.include_router(drivers.router)
app.include_router(tracks.router)
app.include_router(practice.router)
app.include_router(predictions.router)
app.include_router(compare.router)
app.include_router(wet_weather.router)
app.include_router(predict_and_compete.router)
app.include_router(records.router)
app.include_router(strategy.router)
app.include_router(teams.router)
app.include_router(forecast.router)
app.include_router(championship.router)
app.include_router(seasons.router)
app.include_router(race_analysis.router)
app.include_router(ops.router)
app.include_router(cars.router)


@app.get("/")
def root():
    return {"status": "Apex21 API is running"}
