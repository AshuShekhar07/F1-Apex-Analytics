"""
Batch version of split_into_sectors.py -- runs the same real-boundary
split logic for every circuit that has both a .xy.json (from the
fetch/project step) and a .boundaries.json (from the FastF1 batch
pull), and doesn't already have a .sectors.json.
"""
import json
import math
import os

from circuits_registry import REMAINING_CIRCUITS


def dist(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def lerp(a, b, t):
    return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]


def point_at_distance(points, seg_lengths, target_dist):
    cum = 0.0
    for i, seg_len in enumerate(seg_lengths):
        if cum + seg_len >= target_dist:
            t = (target_dist - cum) / seg_len if seg_len > 0 else 0
            return lerp(points[i], points[i + 1], t), i
        cum += seg_len
    return points[-1], len(points) - 2


def split_circuit(circuit_id):
    with open(f"data/circuits/{circuit_id}.boundaries.json") as f:
        boundaries = json.load(f)
    s1_frac = boundaries["sector1_end_m"] / boundaries["lap_length_telemetry_m"]
    s2_frac = boundaries["sector2_end_m"] / boundaries["lap_length_telemetry_m"]

    with open(f"data/circuits/{circuit_id}.xy.json") as f:
        data = json.load(f)
    points = data["points"]
    if points[0] != points[-1]:
        points = points + [points[0]]

    seg_lengths = [dist(points[i], points[i + 1]) for i in range(len(points) - 1)]
    total_length = sum(seg_lengths)
    s1_target = s1_frac * total_length
    s2_target = s2_frac * total_length

    s1_point, s1_idx = point_at_distance(points, seg_lengths, s1_target)
    s2_point, s2_idx = point_at_distance(points, seg_lengths, s2_target)

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
    with open(f"data/circuits/{circuit_id}.sectors.json", "w") as f:
        json.dump(out, f)
    return out


results = []
for c in REMAINING_CIRCUITS:
    cid = c["id"]
    if os.path.exists(f"data/circuits/{cid}.sectors.json"):
        results.append((cid, c["location"], "skipped (already done)"))
        continue
    if not os.path.exists(f"data/circuits/{cid}.boundaries.json"):
        results.append((cid, c["location"], "MISSING boundaries.json"))
        continue
    if not os.path.exists(f"data/circuits/{cid}.xy.json"):
        results.append((cid, c["location"], "MISSING xy.json"))
        continue
    try:
        out = split_circuit(cid)
        results.append((cid, c["location"], f"OK  S1={out['sector1_length_m']}m S2={out['sector2_length_m']}m S3={out['sector3_length_m']}m"))
    except Exception as e:
        results.append((cid, c["location"], f"FAILED: {e}"))

print(f"\n{'ID':10s} {'Location':16s} {'Result'}")
print("-" * 90)
for row in results:
    print(f"{row[0]:10s} {row[1]:16s} {row[2]}")

ok_count = sum(1 for r in results if r[2].startswith("OK"))
print(f"\n{ok_count}/{len(results)} succeeded")
