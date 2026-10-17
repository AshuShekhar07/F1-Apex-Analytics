"""Runtime settings, all from environment variables (see .env.example).

Development keeps the old behaviour (local Vite origins, logos served). Production is
explicit: set APP_ENV=production and list the site's origin(s) in CORS_ORIGINS.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

DEV_ORIGINS = ("http://localhost:5173", "http://localhost:3000")


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    return default if raw is None or raw == "" else raw.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return default if raw is None or raw == "" else int(raw)


@dataclass(frozen=True)
class Settings:
    env: str
    cors_origins: tuple[str, ...]
    # Team logos and driver photos belong to F1, the teams or photographers. Off by default in
    # production: responses drop logo/photo URLs and /static is not served.
    serve_brand_assets: bool
    rate_limit_per_minute: int          # per client IP; 0 = off
    what_if_max_sims: int
    warm_caches: bool                   # load the strategy history at startup instead of on first request
    response_cache: bool                # keep GET answers in the database per data version (app/response_cache.py)
    response_cache_stale: bool          # after new data, send the last saved answer while it is computed again

    @property
    def production(self) -> bool:
        return self.env == "production"


def load_settings() -> Settings:
    env = os.getenv("APP_ENV", "development").strip().lower()
    production = env == "production"
    # an origin never ends in "/": "https://site.example/" would match no browser request
    origins = tuple(o.strip().rstrip("/") for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip().rstrip("/"))
    return Settings(
        env=env,
        cors_origins=origins or (() if production else DEV_ORIGINS),
        serve_brand_assets=_bool("SERVE_BRAND_ASSETS", not production),
        rate_limit_per_minute=_int("RATE_LIMIT_PER_MINUTE", 600 if production else 0),
        what_if_max_sims=_int("WHAT_IF_MAX_SIMS", 2000),
        warm_caches=_bool("WARM_CACHES", production),
        response_cache=_bool("RESPONSE_CACHE", production),
        response_cache_stale=_bool("RESPONSE_CACHE_STALE", True),
    )


settings = load_settings()

BRAND_ASSET_KEYS = frozenset({"logo_url", "team_logo_url", "photo_url"})


def scrub_brand_assets(value):
    """Drop logo/photo URL fields from a JSON-like structure (production default)."""
    if isinstance(value, dict):
        return {k: scrub_brand_assets(v) for k, v in value.items() if k not in BRAND_ASSET_KEYS}
    if isinstance(value, list):
        return [scrub_brand_assets(v) for v in value]
    return value
