"""
Master registry of every circuit needed for full 2018-2026 F1 coverage,
beyond the 9 already done. Each entry:
  id            - bacinger/f1-circuits GeoJSON id
  location      - human name (matches bacinger's GeoJSON "Location")
  fastf1_gp     - string to pass to fastf1.get_session(year, fastf1_gp, session)
  fastf1_year   - a year that circuit actually hosted a race, for boundary extraction
  db_pattern    - SQL ILIKE pattern(s) to match this track in the Apex21 `tracks` table
"""

REMAINING_CIRCUITS = [
    {"id": "bh-2002", "location": "Sakhir",        "fastf1_gp": "Bahrain",         "fastf1_year": 2024, "db_pattern": ["%Bahrain%"]},
    {"id": "cn-2004", "location": "Shanghai",       "fastf1_gp": "Chinese",         "fastf1_year": 2024, "db_pattern": ["%Shanghai%", "%China%"]},
    {"id": "az-2016", "location": "Baku",           "fastf1_gp": "Azerbaijan",      "fastf1_year": 2024, "db_pattern": ["%Baku%", "%Azerbaijan%"]},
    {"id": "es-1991", "location": "Barcelona",      "fastf1_gp": "Spanish",         "fastf1_year": 2024, "db_pattern": ["%Catalunya%", "%Barcelona%", "%Spain%"]},
    {"id": "ca-1978", "location": "Montreal",       "fastf1_gp": "Canadian",        "fastf1_year": 2024, "db_pattern": ["%Montreal%", "%Canada%", "%Gilles%"]},
    {"id": "fr-1969", "location": "Le Castellet",   "fastf1_gp": "French",          "fastf1_year": 2021, "db_pattern": ["%Paul Ricard%", "%France%"]},
    {"id": "at-1969", "location": "Spielberg",      "fastf1_gp": "Austrian",        "fastf1_year": 2024, "db_pattern": ["%Red Bull Ring%", "%Austria%", "%Spielberg%"]},
    {"id": "de-1932", "location": "Hockenheim",     "fastf1_gp": "German",          "fastf1_year": 2019, "db_pattern": ["%Hockenheim%", "%Germany%"]},
    {"id": "hu-1986", "location": "Budapest",       "fastf1_gp": "Hungarian",       "fastf1_year": 2024, "db_pattern": ["%Hungaroring%", "%Hungary%", "%Budapest%"]},
    {"id": "sg-2008", "location": "Singapore",      "fastf1_gp": "Singapore",       "fastf1_year": 2024, "db_pattern": ["%Marina Bay%", "%Singapore%"]},
    {"id": "ru-2014", "location": "Sochi",          "fastf1_gp": "Russia",          "fastf1_year": 2021, "db_pattern": ["%Sochi%", "%Russia%"]},
    {"id": "jp-1962", "location": "Suzuka",         "fastf1_gp": "Japan",           "fastf1_year": 2024, "db_pattern": ["%Suzuka%", "%Japan%"]},
    {"id": "us-2012", "location": "Austin",         "fastf1_gp": "United States",   "fastf1_year": 2024, "db_pattern": ["%Austin%", "%Americas%", "%COTA%"]},
    {"id": "mx-1962", "location": "Mexico City",    "fastf1_gp": "Mexico",          "fastf1_year": 2024, "db_pattern": ["%Mexico%", "%Rodr%guez%"]},
    {"id": "it-1914", "location": "Scarperia e San Piero", "fastf1_gp": "Tuscan",   "fastf1_year": 2020, "db_pattern": ["%Mugello%"]},
    {"id": "de-1927", "location": "Nürburg",        "fastf1_gp": "Eifel",           "fastf1_year": 2020, "db_pattern": ["%Nürburgring%", "%Nurburgring%", "%Eifel%"]},
    {"id": "pt-2008", "location": "Portimão",       "fastf1_gp": "Portuguese",      "fastf1_year": 2021, "db_pattern": ["%Portim%", "%Algarve%", "%Portugal%"]},
    {"id": "it-1953", "location": "Imola",          "fastf1_gp": "Emilia Romagna",  "fastf1_year": 2024, "db_pattern": ["%Imola%", "%Emilia%"]},
    {"id": "tr-2005", "location": "Istanbul",       "fastf1_gp": "Turkish",         "fastf1_year": 2021, "db_pattern": ["%Istanbul%", "%Turk%"]},
    {"id": "qa-2004", "location": "Lusail",         "fastf1_gp": "Qatar",           "fastf1_year": 2024, "db_pattern": ["%Losail%", "%Lusail%", "%Qatar%"]},
    {"id": "sa-2021", "location": "Jeddah",         "fastf1_gp": "Saudi Arabia",    "fastf1_year": 2024, "db_pattern": ["%Jeddah%", "%Saudi%"]},
    {"id": "us-2022", "location": "Miami",          "fastf1_gp": "Miami",           "fastf1_year": 2024, "db_pattern": ["%Miami%"]},
]

# Not yet processed: Madrid (es-2026) -- 2026 Spanish GP hasn't been raced yet
# as of this project's timeline, so there's no telemetry or DB data for it.

if __name__ == "__main__":
    for c in REMAINING_CIRCUITS:
        print(f"{c['id']:10s} {c['location']}")
    print(f"\nTotal: {len(REMAINING_CIRCUITS)} circuits")
