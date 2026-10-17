"""Single source of truth for the FastF1 on-disk cache location.

Reads FASTF1_CACHE_DIR from the environment (or .env). When unset, falls back
to ``cache/`` in the repository root, which is where the pipeline has always
cached sessions.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent


def fastf1_cache_dir() -> str:
    load_dotenv()
    configured = os.getenv("FASTF1_CACHE_DIR")
    path = Path(configured).expanduser() if configured else REPO_ROOT / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def forget_session(session) -> int:
    """Delete FastF1's saved copy of one session (its parsed files and its cached downloads), so
    the next load fetches it fresh. FastF1 never refreshes a saved session by itself: one loaded
    while F1 was still publishing it would stay incomplete. Returns how many items were removed."""
    import shutil

    import fastf1

    removed = 0
    cache_dir = getattr(fastf1.Cache, "_CACHE_DIR", None)
    api_path = getattr(session, "api_path", None)
    if not cache_dir or not api_path:
        return 0
    folder = Path(cache_dir) / api_path.removeprefix("/static/")
    if folder.is_dir():
        shutil.rmtree(folder, ignore_errors=True)
        removed += 1
    cached = getattr(fastf1.Cache, "_requests_session_cached", None)
    if cached is not None:
        try:
            keys = [r.cache_key for r in cached.cache.filter(valid=True, expired=True) if api_path in (r.url or "")]
            if keys:
                cached.cache.delete(*keys)
                removed += len(keys)
        except Exception:                      # an older requests-cache: the parsed files are gone anyway
            pass
    return removed
