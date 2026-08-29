"""
Batch version of the fetch + project step, for every circuit in the
registry that hasn't already been fetched. Safe to re-run -- it skips
any circuit whose .xy.json already exists.
"""
import json
import math
import os
import time
import urllib.request
import urllib.error

import matplotlib.pyplot as plt

from circuits_registry import REMAINING_CIRCUITS

os.makedirs("data/circuits", exist_ok=True)

def fetch_geojson(circuit_id):
    geojson_path = f"data/circuits/{circuit_id}.geojson"
    if os.path.exists(geojson_path):
        return geojson_path, "already fetched"
    url = f"https://raw.githubusercontent.com/bacinger/f1-circuits/master/circuits/{circuit_id}.geojson"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = resp.read()
        with open(geojson_path, "wb") as f:
            f.write(data)
        return geojson_path, "fetched"
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}"
    except Exception as e:
        return None, str(e)


def project(circuit_id):
    with open(f"data/circuits/{circuit_id}.geojson") as f:
        gj = json.load(f)
    feature = gj["features"][0]
    coords = feature["geometry"]["coordinates"]
    props = feature["properties"]

    lats = [c[1] for c in coords]
    lons = [c[0] for c in coords]
    lat0 = sum(lats) / len(lats)
    lon0 = sum(lons) / len(lons)
    R = 6371000

    def proj_point(lon, lat):
        x = math.radians(lon - lon0) * R * math.cos(math.radians(lat0))
        y = math.radians(lat - lat0) * R
        return x, y

    xy = [proj_point(lon, lat) for lon, lat in coords]
    with open(f"data/circuits/{circuit_id}.xy.json", "w") as f:
        json.dump({"properties": props, "points": xy}, f)

    xs = [p[0] for p in xy]
    ys = [p[1] for p in xy]
    plt.figure(figsize=(6, 6), facecolor="#0b0d0e")
    ax = plt.gca()
    ax.set_facecolor("#0b0d0e")
    ax.plot(xs, ys, color="#2be0d3", linewidth=3)
    ax.set_aspect("equal")
    ax.set_title(f"{props['Name']} ({len(xy)} pts)", color="white", fontsize=10)
    ax.tick_params(colors="white")
    for spine in ax.spines.values():
        spine.set_color("#444")
    plt.savefig(f"data/circuits/{circuit_id}_preview.png", dpi=100, facecolor="#0b0d0e")
    plt.close()

    return len(xy), props.get("length"), sum(
        math.hypot(xy[i+1][0]-xy[i][0], xy[i+1][1]-xy[i][1]) for i in range(len(xy)-1)
    )


results = []
for c in REMAINING_CIRCUITS:
    cid = c["id"]
    xy_path = f"data/circuits/{cid}.xy.json"
    if os.path.exists(xy_path):
        results.append((cid, c["location"], "skipped (already done)", "-", "-"))
        continue

    geojson_path, status = fetch_geojson(cid)
    if geojson_path is None:
        results.append((cid, c["location"], f"FETCH FAILED: {status}", "-", "-"))
        continue

    try:
        n_points, official_length, measured_length = project(cid)
        results.append((cid, c["location"], "OK", n_points, f"{official_length}m official / {measured_length:.0f}m measured"))
    except Exception as e:
        results.append((cid, c["location"], f"PROJECT FAILED: {e}", "-", "-"))

    time.sleep(0.3)  # be polite to GitHub's raw content servers

print(f"\n{'ID':10s} {'Location':20s} {'Status':30s} {'Pts':5s} {'Length'}")
print("-" * 100)
for row in results:
    print(f"{row[0]:10s} {row[1]:20s} {row[2]:30s} {str(row[3]):5s} {row[4]}")

ok_count = sum(1 for r in results if r[2] == "OK")
print(f"\n{ok_count}/{len(results)} succeeded")
