import os
import time
import requests
from sqlalchemy import create_engine, text

engine = create_engine(os.environ["DATABASE_URL"])

API = "https://en.wikipedia.org/w/api.php"

TITLE_OVERRIDES = {
    "Kimi Antonelli": "Andrea Kimi Antonelli",
}

def fetch_thumbnail(name, retries=3):
    title = TITLE_OVERRIDES.get(name, name)
    params = {
        "action": "query",
        "titles": title,
        "prop": "pageimages",
        "piprop": "thumbnail",
        "pithumbsize": 500,
        "format": "json",
        "redirects": 1,
    }
    for attempt in range(retries):
        r = requests.get(API, params=params, headers={"User-Agent": "Apex21-F1-Analytics/1.0 (personal project)"})
        if r.status_code == 429:
            wait = 5 * (attempt + 1)
            print(f"    rate limited, waiting {wait}s...")
            time.sleep(wait)
            continue
        r.raise_for_status()
        pages = r.json().get("query", {}).get("pages", {})
        for page in pages.values():
            thumb = page.get("thumbnail", {}).get("source")
            if thumb:
                return thumb.split("?")[0]
        return None
    return None

with engine.connect() as conn:
    already_done = {row[0] for row in conn.execute(
        text("SELECT id FROM drivers WHERE photo_url IS NOT NULL")
    ).fetchall()}

    drivers = conn.execute(text("SELECT id, name FROM drivers ORDER BY id")).mappings().all()
    for d in drivers:
        if d["id"] in already_done:
            print(f"  [{d['id']}] {d['name']}: already done, skipping")
            continue
        url = fetch_thumbnail(d["name"])
        if url:
            conn.execute(text("UPDATE drivers SET photo_url = :u WHERE id = :id"),
                         {"u": url, "id": d["id"]})
            conn.commit()  # commit immediately so progress isn't lost on a later failure
            print(f"  [{d['id']}] {d['name']}: OK")
        else:
            print(f"  [{d['id']}] {d['name']}: NOT FOUND -- needs manual check")
        time.sleep(1.0)  # gentler pacing
