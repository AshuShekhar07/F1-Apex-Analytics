"""
Rank a circuit's 3 real sectors by average speed (length / time) and
assign purple (fastest) / green (mid) / yellow (slowest).

Usage:
    python render_final_colors.py <circuit_id> <avg_s1_time> <avg_s2_time> <avg_s3_time>
"""
import json
import sys

import matplotlib.pyplot as plt

CIRCUIT_ID = sys.argv[1]
avg_times = {
    "sector1": float(sys.argv[2]),
    "sector2": float(sys.argv[3]),
    "sector3": float(sys.argv[4]),
}

with open(f"data/circuits/{CIRCUIT_ID}.sectors.json") as f:
    data = json.load(f)

lengths = {
    "sector1": data["sector1_length_m"],
    "sector2": data["sector2_length_m"],
    "sector3": data["sector3_length_m"],
}

speeds_kmh = {k: (lengths[k] / avg_times[k]) * 3.6 for k in lengths}
ranked = sorted(speeds_kmh.items(), key=lambda kv: kv[1], reverse=True)
role_by_key = {ranked[0][0]: "fastest", ranked[1][0]: "mid", ranked[2][0]: "slowest"}
hex_by_role = {"fastest": "#B026FF", "mid": "#39FF88", "slowest": "#FFE43B"}

colors = {
    k: {
        "hex": hex_by_role[role_by_key[k]],
        "role": role_by_key[k],
        "avg_speed_kmh": round(speeds_kmh[k], 1),
    }
    for k in lengths
}
data["colors"] = colors
with open(f"data/circuits/{CIRCUIT_ID}.sectors.json", "w") as f:
    json.dump(data, f)

print("Average speed per sector:")
for k in ["sector1", "sector2", "sector3"]:
    print(f"  {k}: {speeds_kmh[k]:.1f} km/h -> {colors[k]['role']} ({colors[k]['hex']})")

plt.figure(figsize=(8, 8), facecolor="#0b0d0e")
ax = plt.gca()
ax.set_facecolor("#0b0d0e")
for key in ["sector1", "sector2", "sector3"]:
    pts = data[key]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    hexcol = colors[key]["hex"]
    label = f"{key} — {colors[key]['role']} ({colors[key]['avg_speed_kmh']} km/h)"
    ax.plot(xs, ys, color=hexcol, linewidth=14, alpha=0.18, solid_capstyle="round")
    ax.plot(xs, ys, color=hexcol, linewidth=5, solid_capstyle="round", label=label)

ax.set_aspect("equal")
ax.legend(facecolor="#0b0d0e", labelcolor="white", loc="upper left", fontsize=8)
ax.set_title(f"{data['properties']['Name']} — pace-colored sectors", color="white")
ax.tick_params(colors="white")
for spine in ax.spines.values():
    spine.set_color("#333")
plt.savefig(f"data/circuits/{CIRCUIT_ID}_final_preview.png", dpi=140, facecolor="#0b0d0e")
print(f"Saved -> data/circuits/{CIRCUIT_ID}_final_preview.png")
