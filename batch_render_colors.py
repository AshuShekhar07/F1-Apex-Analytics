"""
Batch version of render_final_colors.py -- assigns purple/green/yellow
per circuit based on real avg sector speed (length / time), using the
DB query results pasted in below.
"""
import json
import os

TRACK_ID_MAP = {
    "Austin": "us-2012", "Baku": "az-2016", "Barcelona": "es-1991",
    "Budapest": "hu-1986", "Hockenheim": "de-1932", "Imola": "it-1953",
    "Istanbul": "tr-2005", "Jeddah": "sa-2021", "Le Castellet": "fr-1969",
    "Lusail": "qa-2004", "Marina Bay": "sg-2008", "Mexico City": "mx-1962",
    "Miami": "us-2022", "Montréal": "ca-1978", "Mugello": "it-1914",
    "Nürburgring": "de-1927", "Portimão": "pt-2008", "Sakhir": "bh-2002",
    "Shanghai": "cn-2004", "Sochi": "ru-2014", "Spielberg": "at-1969",
    "Suzuka": "jp-1962",
    "Las Vegas": "us-2023", "Melbourne": "au-1953", "Monaco": "mc-1929",
    "Monza": "it-1922", "Silverstone": "gb-1948", "Spa-Francorchamps": "be-1925",
    "São Paulo": "br-1940", "Yas Marina": "ae-2009", "Zandvoort": "nl-1948",
}

DB_TIMES = {
    "Austin": (30.039, 42.895, 35.628),
    "Baku": (42.208, 47.362, 28.102),
    "Barcelona": (26.911, 34.370, 29.347),
    "Budapest": (33.007, 32.516, 25.853),
    "Hockenheim": (20.087, 41.441, 28.282),
    "Imola": (29.498, 30.741, 30.216),
    "Istanbul": (39.360, 36.125, 28.124),
    "Jeddah": (40.337, 33.617, 33.838),
    "Le Castellet": (28.328, 32.362, 48.487),
    "Lusail": (35.806, 32.025, 30.225),
    "Marina Bay": (32.803, 45.629, 36.378),
    "Mexico City": (31.937, 34.220, 23.202),
    "Miami": (35.654, 38.403, 29.346),
    "Montréal": (24.418, 26.782, 34.464),
    "Mugello": (36.084, 26.799, 34.273),
    "Nürburgring": (32.323, 40.337, 25.429),
    "Portimão": (26.277, 33.338, 28.535),
    "Sakhir": (32.730, 41.537, 24.877),
    "Shanghai": (28.449, 31.482, 46.234),
    "Sochi": (39.879, 37.235, 32.554),
    "Spielberg": (19.759, 33.658, 23.875),
    "Suzuka": (38.913, 46.211, 20.655),
}

results = []
for track_name, times in DB_TIMES.items():
    cid = TRACK_ID_MAP[track_name]
    sectors_path = f"data/circuits/{cid}.sectors.json"
    if not os.path.exists(sectors_path):
        results.append((cid, track_name, "MISSING sectors.json"))
        continue

    with open(sectors_path) as f:
        data = json.load(f)

    lengths = {
        "sector1": data["sector1_length_m"],
        "sector2": data["sector2_length_m"],
        "sector3": data["sector3_length_m"],
    }
    avg_times = {"sector1": times[0], "sector2": times[1], "sector3": times[2]}
    speeds_kmh = {k: (lengths[k] / avg_times[k]) * 3.6 for k in lengths}
    ranked = sorted(speeds_kmh.items(), key=lambda kv: kv[1], reverse=True)
    role_by_key = {ranked[0][0]: "fastest", ranked[1][0]: "mid", ranked[2][0]: "slowest"}
    hex_by_role = {"fastest": "#B026FF", "mid": "#39FF88", "slowest": "#FFE43B"}

    colors = {
        k: {"hex": hex_by_role[role_by_key[k]], "role": role_by_key[k], "avg_speed_kmh": round(speeds_kmh[k], 1)}
        for k in lengths
    }
    data["colors"] = colors
    with open(sectors_path, "w") as f:
        json.dump(data, f)

    summary = ", ".join(f"{k}={colors[k]['role']}({colors[k]['avg_speed_kmh']}km/h)" for k in ["sector1", "sector2", "sector3"])
    results.append((cid, track_name, f"OK  {summary}"))

print(f"{'ID':10s} {'Track':16s} {'Result'}")
print("-" * 110)
for row in results:
    print(f"{row[0]:10s} {row[1]:16s} {row[2]}")

ok_count = sum(1 for r in results if r[2].startswith("OK"))
print(f"\n{ok_count}/{len(results)} colored")
