"""Find a freely licensed photo of every car in cars_catalogue_v1 and write the credits file the
website's Liveries page reads (frontend/public/livery-photos/photos.json).

For each car: the main image of its English Wikipedia article (searched for when the title
does not match), then that file's licence on Wikimedia Commons. Only free licences are kept
(CC0, public domain, CC BY, CC BY-SA); anything else is skipped and reported. Every photo is
stored with its author, licence and source page, which the site shows under it.

Needs internet access (Wikipedia and Wikimedia Commons). Run it from the repo folder:

    python fetch_livery_photos_v1.py                 # all cars; photos load from Wikimedia
    python fetch_livery_photos_v1.py --download      # also save resized copies under frontend/public/livery-photos/img
    python fetch_livery_photos_v1.py --only 2020-mercedes --use "2020-mercedes=File:Some photo.jpg"

Then commit frontend/public/livery-photos/ (photos.json, and img/ with --download).
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

import cars_catalogue_v1 as catalogue

WIKI = "https://en.wikipedia.org/w/api.php"
COMMONS = "https://commons.wikimedia.org/w/api.php"
UA = "Apex21-livery-photos/1.0 (fan analytics project; https://github.com/AshuShekhar07/F1-Apex-Analytics)"
OUT_DIR = Path(__file__).resolve().parent / "frontend" / "public" / "livery-photos"
WIDTH = 800                 # wide enough for the cards on a high-density screen


def free_licence(name: str | None) -> bool:
    """CC0, public domain, CC BY and CC BY-SA (any version or port); not NC, ND, GFDL-only or fair use."""
    n = (name or "").strip().lower()
    if n.startswith(("cc0", "public domain")) or re.match(r"pd([ -]|$)", n):
        return True
    return re.fullmatch(r"cc[ -]by(-sa)?([ -]\d(\.\d)?)?([ -][a-z]{2,3})?", n) is not None

session = requests.Session()
session.headers["User-Agent"] = UA


def api(url: str, **params) -> dict:
    params = {"format": "json", "formatversion": 2, **params}
    for attempt in range(5):
        r = session.get(url, params=params, timeout=30)
        if r.status_code == 429:
            time.sleep(5 * (attempt + 1))
            continue
        r.raise_for_status()
        time.sleep(0.4)                                        # be gentle with the API
        return r.json()
    raise RuntimeError(f"rate-limited by {url}")


def page_image(title: str) -> tuple[str | None, str | None]:
    """(article title, its main image file name) for a Wikipedia article."""
    data = api(WIKI, action="query", titles=title, prop="pageimages", piprop="name", redirects=1)
    pages = data.get("query", {}).get("pages", [])
    if pages and not pages[0].get("missing") and pages[0].get("pageimage"):
        return pages[0]["title"], pages[0]["pageimage"]
    return None, None


def search_article(car: catalogue.Car) -> tuple[str | None, str | None]:
    data = api(WIKI, action="query", list="search", srsearch=f'"{car.car}" Formula One car', srlimit=3)
    for hit in data.get("query", {}).get("search", []):
        title, image = page_image(hit["title"])
        if image:
            return title, image
    return None, None


def search_commons(car: catalogue.Car, width: int, used: set[str]) -> dict | None:
    """A freely licensed, landscape photo on Commons whose file name carries this car's model
    name (e.g. "MCL35M"): for cars that Wikipedia covers inside an earlier car's article."""
    code = car.car.split()[-1]
    data = api(COMMONS, action="query", list="search", srnamespace=6, srsearch=code, srlimit=30)
    for hit in data.get("query", {}).get("search", []):
        title = hit["title"]
        if code.lower() not in title.lower() or title in used or not title.lower().endswith((".jpg", ".jpeg")):
            continue
        info = commons_file(title, width)
        if info and not info["non_free"] and free_licence(info["license"]) \
                and (info["width"] or 0) >= 1.3 * (info["height"] or 1):
            return info
    return None


def plain(value: str | None) -> str | None:
    if not value:
        return None
    text = html.unescape(re.sub(r"<[^>]+>", "", value))
    return re.sub(r"\s+", " ", text).strip() or None


def commons_file(file_title: str, width: int = WIDTH) -> dict | None:
    if not file_title.startswith("File:"):
        file_title = f"File:{file_title}"
    data = api(COMMONS, action="query", titles=file_title, prop="imageinfo",
               iiprop="url|extmetadata|size", iiurlwidth=width)
    pages = data.get("query", {}).get("pages", [])
    if not pages or pages[0].get("missing") or not pages[0].get("imageinfo"):
        return None                                            # not on Commons (e.g. a non-free local file)
    info = pages[0]["imageinfo"][0]
    meta = info.get("extmetadata", {})
    get = lambda k: (meta.get(k) or {}).get("value")  # noqa: E731
    return {
        "file": pages[0]["title"],
        "thumb": info.get("thumburl") or info.get("url"),
        "width": info.get("thumbwidth") or info.get("width"),
        "height": info.get("thumbheight") or info.get("height"),
        "page": info.get("descriptionurl"),
        "author": plain(get("Artist")) or plain(get("Credit")) or "unknown",
        "license": plain(get("LicenseShortName")) or "unknown",
        "license_url": get("LicenseUrl"),
        "non_free": get("NonFree") in ("true", True),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--download", action="store_true", help="save resized copies under frontend/public/livery-photos/img")
    parser.add_argument("--only", action="append", default=[], help="car key(s) to fetch, e.g. 2020-mercedes")
    parser.add_argument("--use", action="append", default=[], help='KEY=File:Name.jpg: use this Commons file for a car')
    parser.add_argument("--width", type=int, default=WIDTH, help=f"photo width in pixels (default {WIDTH})")
    args = parser.parse_args()
    overrides = dict(u.split("=", 1) for u in args.use)

    out_file = OUT_DIR / "photos.json"
    existing = json.loads(out_file.read_text()) if out_file.exists() else {"photos": {}}
    photos: dict = existing.get("photos", {})
    cars = [c for c in catalogue.CARS if not args.only or c.key in args.only]
    found = skipped = missing = 0
    by_article: dict[str, tuple[str | None, str | None]] = {}
    used: dict[str, str] = {}                                  # Commons file -> car name it shows
    for car in cars:
        try:
            if car.key in overrides or car.photo:
                article, image = car.wiki, overrides.get(car.key) or car.photo
            else:
                if car.wiki not in by_article:
                    title, image = page_image(car.wiki)
                    by_article[car.wiki] = (title, image) if image else search_article(car)
                article, image = by_article[car.wiki]
            if not image:
                missing += 1
                print(f"  {car.key:28} no article image found ({car.wiki})")
                continue
            info = commons_file(image, args.width)
            if info and car.key not in overrides and not car.photo and used.get(info["file"], car.car) != car.car:
                # the article covers an earlier car too (MCL35 / MCL35M): look for this car's own photo
                own = search_commons(car, args.width, set(used))
                if own:
                    info = own
                else:
                    print(f"  {car.key:28} no photo of its own on Commons; sharing {info['file']}")
            if info is None or info["non_free"] or not free_licence(info["license"]):
                skipped += 1
                why = "not on Commons (likely non-free)" if info is None else f"licence {info['license']}"
                print(f"  {car.key:28} skipped {image}: {why}")
                continue
            info.pop("non_free")
            info["article"] = article
            used.setdefault(info["file"], car.car)
            if args.download:
                img_dir = OUT_DIR / "img"
                img_dir.mkdir(parents=True, exist_ok=True)
                ext = Path(info["thumb"].split("?")[0]).suffix.lower()
                ext = ext if ext in (".jpg", ".jpeg", ".png", ".webp") else ".jpg"
                for old in img_dir.glob(f"{car.key}.*"):
                    old.unlink()
                target = img_dir / f"{car.key}{ext}"
                r = session.get(info["thumb"], timeout=60)
                r.raise_for_status()
                target.write_bytes(r.content)
                info["src"] = f"/livery-photos/img/{car.key}{ext}"
                time.sleep(0.4)
            else:
                info["src"] = info["thumb"]
            photos[car.key] = info
            found += 1
            print(f"  {car.key:28} {info['license']:14} {info['author'][:40]}  ({info['file']})")
        except Exception as exc:                               # one car failing never stops the rest
            missing += 1
            print(f"  {car.key:28} error: {type(exc).__name__}: {exc}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps({
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "Wikimedia Commons (free licences only)",
        "photos": dict(sorted(photos.items())),
    }, indent=1, ensure_ascii=False) + "\n")
    print(f"\n{found} photo(s) found, {skipped} skipped (not freely licensed), {missing} without a photo; "
          f"wrote {out_file.relative_to(Path.cwd()) if out_file.is_relative_to(Path.cwd()) else out_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
