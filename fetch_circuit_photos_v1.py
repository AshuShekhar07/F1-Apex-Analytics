"""Find a freely licensed landscape photo of every circuit the website draws, for the page headers,
and write the credits file the website reads (frontend/public/circuit-photos/photos.json).

For each circuit: Wikimedia Commons searches (the circuit's name, then "aerial" / "grandstand"
variants), keeping only JPEG photos that are clearly landscape and at least MIN_WIDTH pixels
wide, with no track-map, logo or diagram in the file name, and only free licences (CC0, public
domain, CC BY, CC BY-SA). Every photo is stored with its author, licence and source page, which
the site shows. A photo picked by hand goes in PICKS.

A circuit that already has a photo keeps it (reruns are cheap and stable) unless the photo is in
REJECT (looked at and not right for a header) or the circuit is named with --only; --refresh
searches every circuit again. Downloads are shrunk to WIDTH px wide JPEGs for the web.

Needs internet access (Wikimedia Commons), except --shrink. Run it from the repo folder:

    python fetch_circuit_photos_v1.py --download        # saves copies under frontend/public/circuit-photos/img
    python fetch_circuit_photos_v1.py --download --only jp-1962
    python fetch_circuit_photos_v1.py --shrink          # re-compress the saved copies (no internet)

Then commit frontend/public/circuit-photos/.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from fetch_livery_photos_v1 import COMMONS, api, free_licence, plain, session

ROOT = Path(__file__).resolve().parent
TRACKS = ROOT / "frontend" / "src" / "data" / "tracks"
OUT_DIR = ROOT / "frontend" / "public" / "circuit-photos"
WIDTH = 1600
MIN_WIDTH = 1600            # original width
MIN_ASPECT = 1.45           # clearly landscape: a header is wide and short
MAX_ASPECT = 2.6            # but not a thin strip, which the header would blow up and blur
QUALITY = 72                # JPEG quality of the saved copies
SKIP_WORDS = ("map", "layout", "logo", "diagram", "plan", "svg", "sign", "ticket", "trophy", "badge", "poster",
              "helmet", "portrait", "interview", "press", "museum", "statue", "plaque", "crash", "accident",
              "motogp", "rider", "racer", "mural", "graffiti", "painting", "drawing", "illustration", "banner",
              "vogelschau")
GOOD_WORDS = ("aerial", "panorama", "grandstand", "start", "straight", "circuit", "track", "race", "view", "pit")

# Searches per circuit, best first (the circuit's own name is always tried last).
QUERIES: dict[str, list[str]] = {
    "ae-2009": ["Yas Marina Circuit aerial", "Yas Marina Circuit"],
    "at-1969": ["Red Bull Ring Spielberg aerial", "Red Bull Ring grandstand"],
    "au-1953": ["Albert Park Circuit Melbourne Grand Prix", "Albert Park Grand Prix circuit"],
    "az-2016": ["Baku City Circuit", "Baku Formula 1 street circuit"],
    "be-1925": ["Spa-Francorchamps Eau Rouge", "Circuit de Spa-Francorchamps"],
    "bh-2002": ["Bahrain International Circuit aerial", "Bahrain International Circuit"],
    "br-1940": ["Interlagos circuit aerial", "Autódromo José Carlos Pace"],
    "ca-1978": ["Circuit Gilles Villeneuve aerial", "Circuit Gilles Villeneuve"],
    "cn-2004": ["Shanghai International Circuit grandstand", "Shanghai International Circuit"],
    "de-1927": ["Nürburgring Grand Prix circuit aerial", "Nürburgring GP-Strecke"],
    "de-1932": ["Hockenheimring aerial", "Hockenheimring grandstand"],
    "es-1991": ["Circuit de Barcelona-Catalunya aerial", "Circuit de Catalunya grandstand"],
    "fr-1969": ["Circuit Paul Ricard aerial", "Circuit Paul Ricard"],
    "gb-1948": ["Silverstone Circuit aerial", "Silverstone Wing grandstand"],
    "hu-1986": ["Hungaroring aerial", "Hungaroring grandstand"],
    "it-1914": ["Mugello Circuit aerial", "Autodromo del Mugello"],
    "it-1922": ["Autodromo Nazionale Monza aerial", "Monza circuit grandstand"],
    "it-1953": ["Imola circuit aerial", "Autodromo Enzo e Dino Ferrari"],
    "jp-1962": ["Suzuka Circuit aerial", "Suzuka Circuit grandstand"],
    "mc-1929": ["Monaco Grand Prix harbour circuit", "Circuit de Monaco"],
    "mx-1962": ["Autódromo Hermanos Rodríguez stadium", "Autódromo Hermanos Rodríguez"],
    "nl-1948": ["Circuit Zandvoort aerial", "Circuit Zandvoort"],
    "pt-2008": ["Autódromo Internacional do Algarve aerial", "Algarve International Circuit"],
    "qa-2004": ["Losail International Circuit night", "Losail International Circuit"],
    "ru-2014": ["Sochi Autodrom", "Sochi Autodrom Formula 1"],
    "sa-2021": ["Jeddah Corniche Circuit", "Jeddah Corniche"],
    "sg-2008": ["Marina Bay Street Circuit night", "Marina Bay Street Circuit"],
    "tr-2005": ["Istanbul Park circuit aerial", "Istanbul Park"],
    "us-2012": ["Circuit of the Americas aerial", "Circuit of the Americas tower"],
    "us-2022": ["Miami International Autodrome", "Miami Grand Prix Hard Rock Stadium circuit"],
    "us-2023": ["Las Vegas Strip Circuit", "Las Vegas Grand Prix circuit"],
}
# Hand-picked Commons files (after review), circuit id -> "File:..."
PICKS: dict[str, str] = {}
# Looked at and not right for a circuit header (a rider close-up, a mural, a 1927 drawing, a thin
# strip, a city road away from the circuit): never used, the next search result is taken instead.
REJECT: set[str] = {
    "File:Italian professional motorcycle racer and multiple MotoGP World Champion Valentino Rossi in action in the day 1 "
    "of Qatar test at the Losail International Circuit. (33220830442).jpg",
    "File:Serty31 - Aston Martin F1 Miami - 2025.jpg",
    "File:Nürburgring, Vogelschauansicht, Eugen Felle, 1927.jpg",
    "File:Imola banner.jpg",
    "File:Jeddah Corniche 37.jpg",
}


def circuits() -> list[dict]:
    out = []
    for f in sorted(TRACKS.glob("*.sectors.json")):
        p = json.loads(f.read_text())["properties"]
        out.append({"id": p["id"], "name": p["Name"], "location": p["Location"]})
    return out


def file_info(title: str, width: int) -> dict | None:
    data = api(COMMONS, action="query", titles=title, prop="imageinfo", iiprop="url|extmetadata|size|mime", iiurlwidth=width)
    pages = data.get("query", {}).get("pages", [])
    if not pages or pages[0].get("missing") or not pages[0].get("imageinfo"):
        return None
    info = pages[0]["imageinfo"][0]
    meta = info.get("extmetadata", {})
    get = lambda k: (meta.get(k) or {}).get("value")  # noqa: E731
    return {
        "file": pages[0]["title"], "thumb": info.get("thumburl") or info.get("url"), "mime": info.get("mime"),
        "orig_width": info.get("width"), "orig_height": info.get("height"),
        "page": info.get("descriptionurl"),
        "author": plain(get("Artist")) or plain(get("Credit")) or "unknown",
        "license": plain(get("LicenseShortName")) or "unknown", "license_url": get("LicenseUrl"),
        "non_free": get("NonFree") in ("true", True),
    }


def acceptable(info: dict | None) -> bool:
    if not info or info["file"] in REJECT or info["non_free"] or not free_licence(info["license"]) or info["mime"] != "image/jpeg":
        return False
    w, h = info["orig_width"] or 0, info["orig_height"] or 1
    return w >= MIN_WIDTH and MIN_ASPECT <= w / h <= MAX_ASPECT


def shrink(path: Path, width: int = WIDTH) -> None:
    """At most `width` px wide, progressive JPEG at QUALITY (Commons serves 1920 px copies)."""
    from PIL import Image

    with Image.open(path) as im:
        im = im.convert("RGB")
        if im.width > width:
            im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
        im.save(path, "JPEG", quality=QUALITY, optimize=True, progressive=True)


def score(title: str) -> int:
    t = title.lower()
    return sum(word in t for word in GOOD_WORDS)


def find(circuit: dict, width: int) -> dict | None:
    for query in QUERIES.get(circuit["id"], []) + [circuit["name"]]:
        data = api(COMMONS, action="query", list="search", srnamespace=6, srsearch=query, srlimit=30)
        hits = [h["title"] for h in data.get("query", {}).get("search", [])]
        hits = [t for t in hits if t.lower().endswith((".jpg", ".jpeg")) and not any(w in t.lower() for w in SKIP_WORDS)]
        for title in sorted(hits, key=lambda t: -score(t)):
            info = file_info(title, width)
            if acceptable(info):
                return info
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--download", action="store_true", help="save resized copies under frontend/public/circuit-photos/img")
    parser.add_argument("--only", action="append", default=[], help="circuit id(s), e.g. jp-1962")
    parser.add_argument("--width", type=int, default=WIDTH)
    parser.add_argument("--refresh", action="store_true", help="search again for circuits that already have a photo")
    parser.add_argument("--shrink", action="store_true", help="only re-compress the saved copies (no internet)")
    args = parser.parse_args()

    if args.shrink:
        for f in sorted((OUT_DIR / "img").glob("*.jpg")):
            before = f.stat().st_size
            shrink(f, args.width)
            print(f"  {f.name:12} {before // 1024:5} KB -> {f.stat().st_size // 1024:4} KB")
        return 0

    out_file = OUT_DIR / "photos.json"
    existing = json.loads(out_file.read_text()) if out_file.exists() else {"photos": {}}
    photos: dict = existing.get("photos", {})
    found = missing = kept = 0
    for c in circuits():
        if args.only and c["id"] not in args.only:
            continue
        have = photos.get(c["id"])
        if have and have["file"] in REJECT:
            photos.pop(c["id"])
            if have.get("src", "").startswith("/circuit-photos/img/"):
                (OUT_DIR / "img" / f"{c['id']}.jpg").unlink(missing_ok=True)
        elif have and not args.refresh and not args.only:
            kept += 1
            continue
        try:
            info = file_info(PICKS[c["id"]], args.width) if c["id"] in PICKS else find(c, args.width)
            if not acceptable(info):
                missing += 1
                print(f"  {c['id']:8} no free landscape photo found ({c['name']})")
                continue
            for k in ("non_free", "mime"):
                info.pop(k)
            info.update(name=c["name"], location=c["location"])
            if args.download:
                img = OUT_DIR / "img"
                img.mkdir(parents=True, exist_ok=True)
                target = img / f"{c['id']}.jpg"
                r = session.get(info["thumb"], timeout=60)
                r.raise_for_status()
                target.write_bytes(r.content)
                shrink(target, args.width)
                info["src"] = f"/circuit-photos/img/{c['id']}.jpg"
                time.sleep(0.4)
            else:
                info["src"] = info["thumb"]
            photos[c["id"]] = info
            found += 1
            print(f"  {c['id']:8} {info['license']:14} {info['author'][:40]}  ({info['file']})")
        except Exception as exc:                               # one circuit failing never stops the rest
            missing += 1
            print(f"  {c['id']:8} error: {type(exc).__name__}: {exc}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps({
        "source": "Wikimedia Commons; free licences only (CC0, public domain, CC BY, CC BY-SA), credited per photo",
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "photos": dict(sorted(photos.items())),
    }, indent=1, ensure_ascii=False) + "\n")
    print(f"{found} photos found, {kept} kept, {missing} without; wrote {out_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
