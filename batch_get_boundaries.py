"""
Batch-pulls real sector boundary distances for all 22 remaining
circuits, using the same lap-relative-time method validated earlier
(bug fix: sector boundary timestamps are relative to LAP start, not
session start -- do NOT add LapStartTime).

Writes one <circuit_id>.boundaries.json per circuit into
./boundaries_batch/. Safe to re-run -- skips circuits already done.
"""
import json
import os
import time

import fastf1
import numpy as np
import pandas as pd

fastf1.Cache.enable_cache("fastf1_cache")

CIRCUITS = [
    {"id": "bh-2002", "location": "Sakhir",        "fastf1_gp": "Bahrain",        "fastf1_year": 2024},
    {"id": "cn-2004", "location": "Shanghai",      "fastf1_gp": "Chinese",        "fastf1_year": 2024},
    {"id": "az-2016", "location": "Baku",          "fastf1_gp": "Azerbaijan",     "fastf1_year": 2024},
    {"id": "es-1991", "location": "Barcelona",     "fastf1_gp": "Spanish",        "fastf1_year": 2024},
    {"id": "ca-1978", "location": "Montreal",      "fastf1_gp": "Canadian",       "fastf1_year": 2024},
    {"id": "fr-1969", "location": "Le Castellet",  "fastf1_gp": "French",         "fastf1_year": 2021},
    {"id": "at-1969", "location": "Spielberg",     "fastf1_gp": "Austrian",       "fastf1_year": 2024},
    {"id": "de-1932", "location": "Hockenheim",    "fastf1_gp": "German",         "fastf1_year": 2019},
    {"id": "hu-1986", "location": "Budapest",      "fastf1_gp": "Hungarian",      "fastf1_year": 2024},
    {"id": "sg-2008", "location": "Singapore",     "fastf1_gp": "Singapore",      "fastf1_year": 2024},
    {"id": "ru-2014", "location": "Sochi",         "fastf1_gp": "Russia",         "fastf1_year": 2021},
    {"id": "jp-1962", "location": "Suzuka",        "fastf1_gp": "Japan",          "fastf1_year": 2024},
    {"id": "us-2012", "location": "Austin",        "fastf1_gp": "United States",  "fastf1_year": 2024},
    {"id": "mx-1962", "location": "Mexico City",   "fastf1_gp": "Mexico",         "fastf1_year": 2024},
    {"id": "it-1914", "location": "Mugello",       "fastf1_gp": "Tuscan",         "fastf1_year": 2020},
    {"id": "de-1927", "location": "Nürburgring",   "fastf1_gp": "Eifel",          "fastf1_year": 2020},
    {"id": "pt-2008", "location": "Portimão",      "fastf1_gp": "Portuguese",     "fastf1_year": 2021},
    {"id": "it-1953", "location": "Imola",         "fastf1_gp": "Emilia Romagna", "fastf1_year": 2024},
    {"id": "tr-2005", "location": "Istanbul",      "fastf1_gp": "Turkish",        "fastf1_year": 2021},
    {"id": "qa-2004", "location": "Lusail",        "fastf1_gp": "Qatar",          "fastf1_year": 2024},
    {"id": "sa-2021", "location": "Jeddah",        "fastf1_gp": "Saudi Arabia",   "fastf1_year": 2024},
    {"id": "us-2022", "location": "Miami",         "fastf1_gp": "Miami",          "fastf1_year": 2024},
]

os.makedirs("boundaries_batch", exist_ok=True)


def sector_boundary_distances(lap):
    if pd.isna(lap["Sector1Time"]) or pd.isna(lap["Sector2Time"]):
        return None
    tel = lap.get_car_data().add_distance()
    if tel.empty:
        return None
    s1_end_time = lap["Sector1Time"]
    s2_end_time = lap["Sector1Time"] + lap["Sector2Time"]
    s1_dist = np.interp(
        s1_end_time.total_seconds(), tel["Time"].dt.total_seconds(), tel["Distance"]
    )
    s2_dist = np.interp(
        s2_end_time.total_seconds(), tel["Time"].dt.total_seconds(), tel["Distance"]
    )
    lap_length = tel["Distance"].iloc[-1]
    return s1_dist, s2_dist, lap_length


results = []
for c in CIRCUITS:
    cid = c["id"]
    out_path = f"boundaries_batch/{cid}.boundaries.json"
    if os.path.exists(out_path):
        results.append((cid, c["location"], "skipped (already done)", "-"))
        continue

    try:
        session = fastf1.get_session(c["fastf1_year"], c["fastf1_gp"], "R")
        session.load(laps=True, telemetry=True, weather=False)
        laps = session.laps.pick_accurate().head(25)

        s1_results, s2_results, lengths = [], [], []
        for _, lap in laps.iterlaps():
            r = sector_boundary_distances(lap)
            if r is None:
                continue
            s1, s2, length = r
            s1_results.append(s1)
            s2_results.append(s2)
            lengths.append(length)

        if not s1_results:
            results.append((cid, c["location"], "NO USABLE LAPS", "-"))
            continue

        out = {
            "year": c["fastf1_year"],
            "gp": c["fastf1_gp"],
            "num_laps_sampled": len(s1_results),
            "sector1_end_m": round(float(np.mean(s1_results)), 1),
            "sector2_end_m": round(float(np.mean(s2_results)), 1),
            "lap_length_telemetry_m": round(float(np.mean(lengths)), 1),
            "std_s1": round(float(np.std(s1_results)), 2),
            "std_s2": round(float(np.std(s2_results)), 2),
        }
        with open(out_path, "w") as f:
            json.dump(out, f, indent=2)

        results.append((cid, c["location"], "OK", f"{len(s1_results)} laps, std {out['std_s1']}/{out['std_s2']}m"))

    except Exception as e:
        results.append((cid, c["location"], f"FAILED: {e}", "-"))

    time.sleep(1)

print(f"\n{'ID':10s} {'Location':16s} {'Status':40s} {'Detail'}")
print("-" * 100)
for row in results:
    print(f"{row[0]:10s} {row[1]:16s} {row[2]:40s} {row[3]}")

ok_count = sum(1 for r in results if r[2] == "OK")
print(f"\n{ok_count}/{len(results)} succeeded")
