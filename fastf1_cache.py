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
