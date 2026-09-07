import os
import time
import requests
from sqlalchemy import create_engine, text

engine = create_engine(os.environ["DATABASE_URL"])
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"

# Wikidata QIDs for each team entity, looked up manually for accuracy
# (team names alone are too ambiguous/generic for reliable auto-search)
TEAM_QIDS = {
    1: "Q855469",    # McLaren F1 Team
    2: "Q1141225",   # Red Bull Racing
    3: "Q26719",      # Scuderia Ferrari
    4: "Q19908507",  # Haas F1 Team
    5: "Q1943248",   # Sauber Motorsport
    6: "Q642352",    # Scuderia Toro Rosso
    7: "Q1707891",   # Force India
    8: "Q471615",    # Williams Grand Prix Engineering
    9: "Q212088",    # Renault F1 Team
    10: "Q910444",   # Mercedes-AMG Petronas
    11: "Q65090492", # Racing Point F1 Team
    12: "Q65090492", # Alfa Romeo Racing (Sauber-run era) -- may need manual check
    14: "Q1943248",  # Alfa Romeo (Sauber-run era, later name) -- may need manual check
    15: "Q64980936", # AlphaTauri
    17: "Q1444927",  # Aston Martin F1 Team
    18: "Q29438",    # Alpine F1 Team
    19: "Q64980936", # RB (rebrand of AlphaTauri) -- may need manual check
    20: "Q1943248",  # Kick Sauber -- may need manual check
    21: "Q64980936", # Racing Bulls -- may need manual check
    22: "Q3013",     # Audi F1 -- placeholder, likely needs manual check (new team)
    23: "Q108241",   # Cadillac -- placeholder, likely needs manual check (new team)
}

def fetch_logo(qid, retries=5):
    params = {
        "action": "wbgetclaims", "entity": qid, "property": "P154", "format": "json"
    }
    for attempt in range(retries):
        r = requests.get(WIKIDATA_API, params=params, headers={"User-Agent": "Apex21-F1-Analytics/1.0"})
        if r.status_code == 429:
            time.sleep(10 * (attempt + 1))
            continue
        r.raise_for_status()
        claims = r.json().get("claims", {}).get("P154", [])
        if not claims:
            return None
        filename = claims[0]["mainsnak"]["datavalue"]["value"]
        # resolve Commons filename to a direct image URL
        r2 = requests.get(COMMONS_API, params={
            "action": "query", "titles": f"File:{filename}", "prop": "imageinfo",
            "iiprop": "url", "format": "json"
        }, headers={"User-Agent": "Apex21-F1-Analytics/1.0"})
        pages = r2.json().get("query", {}).get("pages", {})
        for page in pages.values():
            info = page.get("imageinfo")
            if info:
                return info[0]["url"]
        return None
    return None

with engine.connect() as conn:
    for team_id, qid in TEAM_QIDS.items():
        url = fetch_logo(qid)
        if url:
            conn.execute(text("UPDATE teams SET logo_url = :u WHERE id = :id"),
                         {"u": url, "id": team_id})
            conn.commit()
            print(f"  [{team_id}] {qid}: OK -> {url}")
        else:
            print(f"  [{team_id}] {qid}: NOT FOUND -- needs manual check")
        time.sleep(1.5)
