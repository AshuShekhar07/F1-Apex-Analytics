import time
import requests

API = "https://en.wikipedia.org/w/api.php"

TEAM_TITLES = {
    1: "McLaren",
    2: "Red Bull Racing",
    3: "Scuderia Ferrari",
    4: "Haas F1 Team",
    5: "Sauber Motorsport",
    6: "Scuderia Toro Rosso",
    7: "Force India",
    8: "Williams Grand Prix Engineering",
    9: "Renault F1 Team",
    10: "Mercedes-AMG Petronas F1 Team",
    11: "Racing Point F1 Team",
    12: "Alfa Romeo Racing",
    14: "Alfa Romeo F1 Team",
    15: "Scuderia AlphaTauri",
    17: "Aston Martin F1 Team",
    18: "Alpine F1 Team",
    19: "RB Formula One Team",
    20: "Kick Sauber",
    21: "Racing Bulls F1 Team",
    22: "Audi in Formula One",
    23: "Cadillac F1 Team",
}

def fetch_thumbnail(title, retries=5):
    params = {
        "action": "query", "titles": title, "prop": "pageimages",
        "piprop": "thumbnail|name", "pithumbsize": 500, "format": "json", "redirects": 1,
    }
    for attempt in range(retries):
        r = requests.get(API, params=params, headers={"User-Agent": "Apex21-F1-Analytics/1.0"})
        if r.status_code == 429:
            time.sleep(8 * (attempt + 1))
            continue
        r.raise_for_status()
        pages = r.json().get("query", {}).get("pages", {})
        for page in pages.values():
            thumb = page.get("thumbnail", {}).get("source")
            fname = page.get("pageimage")
            if thumb:
                return thumb.split("?")[0], fname
        return None, None
    return None, None

for team_id, title in TEAM_TITLES.items():
    url, fname = fetch_thumbnail(title)
    if url:
        print(f"[{team_id}] {title}\n    file: {fname}\n    url:  {url}\n")
    else:
        print(f"[{team_id}] {title}: NOT FOUND\n")
    time.sleep(1.2)
