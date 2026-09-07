import time
import requests

API = "https://commons.wikimedia.org/w/api.php"

SEARCH_TERMS = {
    1: "McLaren F1 logo",
    2: "Red Bull Racing F1 logo",
    3: "Scuderia Ferrari logo",
    6: "Toro Rosso F1 logo",
    7: "Force India F1 logo",
    9: "Renault F1 Team logo",
    12: "Alfa Romeo Racing F1 logo",
    14: "Alfa Romeo F1 Team logo",
    15: "AlphaTauri logo",
    17: "Aston Martin F1 logo",
    19: "RB Formula One Team logo",
    21: "Racing Bulls F1 logo",
    23: "Cadillac F1 logo",
}

def search_commons(term, limit=3, retries=5):
    params = {
        "action": "query", "list": "search", "srsearch": f"{term} filetype:bitmap|drawing",
        "srnamespace": 6, "srlimit": limit, "format": "json"
    }
    for attempt in range(retries):
        r = requests.get(API, params=params, headers={"User-Agent": "Apex21-F1-Analytics/1.0"})
        if r.status_code == 429:
            time.sleep(8 * (attempt + 1))
            continue
        r.raise_for_status()
        results = r.json().get("query", {}).get("search", [])
        titles = [res["title"] for res in results]
        urls = []
        for title in titles:
            r2 = requests.get(API, params={
                "action": "query", "titles": title, "prop": "imageinfo",
                "iiprop": "url", "iiurlwidth": 400, "format": "json"
            }, headers={"User-Agent": "Apex21-F1-Analytics/1.0"})
            pages = r2.json().get("query", {}).get("pages", {})
            for page in pages.values():
                info = page.get("imageinfo")
                if info:
                    urls.append((title, info[0].get("thumburl") or info[0]["url"]))
            time.sleep(0.5)
        return urls
    return []

for team_id, term in SEARCH_TERMS.items():
    print(f"=== [{team_id}] {term} ===")
    results = search_commons(term)
    if not results:
        print("    no candidates found")
    for title, url in results:
        print(f"    {title}\n    {url}")
    print()
    time.sleep(1.0)
