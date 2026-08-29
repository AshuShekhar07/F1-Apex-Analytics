"""
Load a circuit's GeoJSON centerline, project it from lat/lon to local
flat X/Y meters (equirectangular projection centered on the track),
and plot it so we can visually confirm the shape before it goes 3D.
"""
import json
import math
import sys
import matplotlib.pyplot as plt

CIRCUIT_ID = sys.argv[1] if len(sys.argv) > 1 else "gb-1948"
path = f"data/circuits/{CIRCUIT_ID}.geojson"

with open(path) as f:
    gj = json.load(f)

feature = gj["features"][0]
coords = feature["geometry"]["coordinates"]
props = feature["properties"]

lats = [c[1] for c in coords]
lons = [c[0] for c in coords]
lat0 = sum(lats) / len(lats)
lon0 = sum(lons) / len(lons)

R = 6371000
def project(lon, lat):
    x = math.radians(lon - lon0) * R * math.cos(math.radians(lat0))
    y = math.radians(lat - lat0) * R
    return x, y

xy = [project(lon, lat) for lon, lat in coords]
xs = [p[0] for p in xy]
ys = [p[1] for p in xy]

out_path = f"data/circuits/{CIRCUIT_ID}.xy.json"
with open(out_path, "w") as f:
    json.dump({"properties": props, "points": xy}, f)

plt.figure(figsize=(8, 8), facecolor="#0b0d0e")
ax = plt.gca()
ax.set_facecolor("#0b0d0e")
ax.plot(xs, ys, color="#2be0d3", linewidth=3)
ax.set_aspect("equal")
ax.set_title(f"{props['Name']} — projected centerline ({len(xy)} points)", color="white")
ax.tick_params(colors="white")
for spine in ax.spines.values():
    spine.set_color("#444")
plt.savefig(f"data/circuits/{CIRCUIT_ID}_preview.png", dpi=130, facecolor="#0b0d0e")
print(f"Points: {len(xy)}")
print(f"Track length from data: {props.get('length')} m")
print(f"Saved projected data -> {out_path}")
print(f"Saved preview image -> data/circuits/{CIRCUIT_ID}_preview.png")
