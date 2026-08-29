"""
Split a circuit's projected centerline into 3 real F1 sectors, using
boundary distances from a <circuit_id>.boundaries.json file (produced
by get_sector_boundaries.py in the Apex21 project, then copied here).

Usage:
    python split_into_sectors.py <circuit_id>
"""
import json
import math
import sys

import matplotlib.pyplot as plt

CIRCUIT_ID = sys.argv[1]

with open(f"data/circuits/{CIRCUIT_ID}.boundaries.json") as f:
    boundaries = json.load(f)

SECTOR1_END_M = boundaries["sector1_end_m"]
SECTOR2_END_M = boundaries["sector2_end_m"]
LAP_LENGTH_TELEMETRY_M = boundaries["lap_length_telemetry_m"]

s1_frac = SECTOR1_END_M / LAP_LENGTH_TELEMETRY_M
s2_frac = SECTOR2_END_M / LAP_LENGTH_TELEMETRY_M

with open(f"data/circuits/{CIRCUIT_ID}.xy.json") as f:
    data = json.load(f)

points = data["points"]
if points[0] != points[-1]:
    points = points + [points[0]]


def dist(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def lerp(a, b, t):
    return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]


seg_lengths = [dist(points[i], points[i + 1]) for i in range(len(points) - 1)]
total_length = sum(seg_lengths)
s1_target = s1_frac * total_length
s2_target = s2_frac * total_length


def point_at_distance(target_dist):
    cum = 0.0
    for i, seg_len in enumerate(seg_lengths):
        if cum + seg_len >= target_dist:
            t = (target_dist - cum) / seg_len if seg_len > 0 else 0
            return lerp(points[i], points[i + 1], t), i
        cum += seg_len
    return points[-1], len(points) - 2


s1_point, s1_idx = point_at_distance(s1_target)
s2_point, s2_idx = point_at_distance(s2_target)

sector1 = [points[0]] + points[1 : s1_idx + 1] + [s1_point]
sector2 = [s1_point] + points[s1_idx + 1 : s2_idx + 1] + [s2_point]
sector3 = [s2_point] + points[s2_idx + 1 : -1] + [points[0]]

out = {
    "properties": data["properties"],
    "sector1": sector1,
    "sector2": sector2,
    "sector3": sector3,
    "sector1_length_m": round(sum(dist(sector1[i], sector1[i+1]) for i in range(len(sector1)-1)), 1),
    "sector2_length_m": round(sum(dist(sector2[i], sector2[i+1]) for i in range(len(sector2)-1)), 1),
    "sector3_length_m": round(sum(dist(sector3[i], sector3[i+1]) for i in range(len(sector3)-1)), 1),
}
out_path = f"data/circuits/{CIRCUIT_ID}.sectors.json"
with open(out_path, "w") as f:
    json.dump(out, f)

print(f"Sector 1: {len(sector1)} points, {out['sector1_length_m']} m")
print(f"Sector 2: {len(sector2)} points, {out['sector2_length_m']} m")
print(f"Sector 3: {len(sector3)} points, {out['sector3_length_m']} m")
print(f"Saved -> {out_path}")

plt.figure(figsize=(8, 8), facecolor="#0b0d0e")
ax = plt.gca()
ax.set_facecolor("#0b0d0e")
for sector, color, label in [
    (sector1, "#e8433f", "Sector 1"),
    (sector2, "#3f7ee8", "Sector 2"),
    (sector3, "#e8d23f", "Sector 3"),
]:
    xs = [p[0] for p in sector]
    ys = [p[1] for p in sector]
    ax.plot(xs, ys, color=color, linewidth=5, solid_capstyle="round", label=label)
ax.scatter([s1_point[0], s2_point[0]], [s1_point[1], s2_point[1]], color="white", s=40, zorder=5)
ax.set_aspect("equal")
ax.legend(facecolor="#0b0d0e", labelcolor="white", loc="upper left")
ax.set_title(f"{out['properties']['Name']} — real S1/S2/S3 boundaries", color="white")
ax.tick_params(colors="white")
for spine in ax.spines.values():
    spine.set_color("#444")
plt.savefig(f"data/circuits/{CIRCUIT_ID}_sectors_preview.png", dpi=130, facecolor="#0b0d0e")
print(f"Saved preview -> data/circuits/{CIRCUIT_ID}_sectors_preview.png")
