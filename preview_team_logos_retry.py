import time
import requests

API = "https://en.wikipedia.org/w/api.php"

RETRY_TITLES = {
    1: "McLaren Racing",
    2: "Oracle Red Bull Racing",
    3: "Scuderia Ferrari HP",
    6: "Toro Rosso",
    7: "Sahara Force India F1 Team",
    9: "Renault in Formula One",
    12: "Alfa Romeo in Formula One",
    14: "Alfa Romeo in Formula One",
    15: "AlphaTauri",
    17: "Aston Martin Aramco Formula One Team",
    19: "Visa Cash App RB F1 Team",
    21: "Racing Bulls",
    23: "Cadillac Formula One",
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
        data = r.json().get("query", {}).get("pages", {})
        for page in data.values():
            if "missing" in page:
                return "MISSING_PAGE", None
            thumb = page.get("thumbnail", {}).get("source")
            fname = page.get("pageimage")
            if thumb:
                return thumb.split("?")[0], fname
        return None, None
    return None, None

for team_id, title in RETRY_TITLES.items():
    url, fname = fetch_thumbnail(title)
    if url == "MISSING_PAGE":
        print(f"[{team_id}] '{title}': page does not exist")
    elif url:
        print(f"[{team_id}] '{title}'\n    file: {fname}\n    url:  {url}\n")
    else:
        print(f"[{team_id}] '{title}': page exists but no thumbnail found\n")
    time.sleep(1.2)
