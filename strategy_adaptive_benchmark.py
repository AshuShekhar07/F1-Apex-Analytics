from collections import Counter
from sqlalchemy import create_engine, text
import os

engine = create_engine(os.environ["DATABASE_URL"])

ERA = "era2_18inch_groundeffect"
FINISHED = [
    "Finished","+1 Lap","+2 Laps","+3 Laps","+4 Laps","+5 Laps","+6 Laps"
]


def q(sql, p=None):
    with engine.connect() as c:
        return c.execute(text(sql), p or {}).mappings().all()


races = q("""
SELECT
    r.id,
    r.track_id,
    r.season_year,
    r.round_number,
    r.race_date,
    sw.track_temp_avg
FROM races r
JOIN sessions s
  ON s.race_id = r.id
 AND s.session_type = 'R'
JOIN session_weather sw
  ON sw.session_id = s.id
WHERE r.regulation_era = :era
  AND r.race_date IS NOT NULL
  AND r.season_year BETWEEN 2022 AND 2025
  AND sw.rainfall = FALSE
ORDER BY r.race_date
""", {"era": ERA})


def get_stops(rid):
    x = q("""
        SELECT COUNT(*) AS stints
        FROM race_stints rs
        JOIN sessions s
          ON s.race_id = rs.race_id
         AND s.session_type = 'R'
        JOIN race_results rr
          ON rr.race_entry_id = rs.race_entry_id
         AND rr.session_id = s.id
        WHERE rs.race_id = :rid
          AND rs.finishing_position = 1
          AND rr.status = ANY(:finished)
    """, {"rid": rid, "finished": FINISHED})

    return None if not x else max(0, int(x[0]["stints"]) - 1)


stops = {}
for r in races:
    s = get_stops(r["id"])
    if s is not None:
        stops[r["id"]] = s

races = [r for r in races if r["id"] in stops]


def majority(values):
    if not values:
        return None
    return Counter(values).most_common(1)[0][0]


def global_pred(hist):
    return majority([stops[x["id"]] for x in hist])


def track_values(hist, track_id):
    return [
        stops[x["id"]]
        for x in hist
        if x["track_id"] == track_id
    ]


def temp_neighbours(hist, target, n=3):
    usable = [
        x for x in hist
        if x["track_temp_avg"] is not None
    ]

    usable.sort(
        key=lambda x: abs(
            float(x["track_temp_avg"]) -
            float(target["track_temp_avg"])
        )
    )

    return usable[:n]


def predict(policy, hist, target):
    g = global_pred(hist)

    tv = track_values(
        hist,
        target["track_id"],
    )

    # ---------------------------------------------------------
    # A: global
    # ---------------------------------------------------------
    if policy == "global":
        return g

    # ---------------------------------------------------------
    # B: latest track if available, otherwise global
    # ---------------------------------------------------------
    if policy == "track_latest":
        if tv:
            return tv[-1]
        return g

    # ---------------------------------------------------------
    # C: track latest only when >= 2 historical races exist
    # otherwise global
    # ---------------------------------------------------------
    if policy == "track_latest_2plus":
        if len(tv) >= 2:
            return tv[-1]
        return g

    # ---------------------------------------------------------
    # D: use track latest only when it agrees with global
    # otherwise global
    # ---------------------------------------------------------
    if policy == "agree_or_global":
        if tv and tv[-1] == g:
            return tv[-1]
        return g

    # ---------------------------------------------------------
    # E: track latest gets one vote, global gets two votes
    # ---------------------------------------------------------
    if policy == "weighted_global":
        vals = [g, g]
        if tv:
            vals.append(tv[-1])
        return majority(vals)

    # ---------------------------------------------------------
    # F: temperature analog majority, 3 closest
    # ---------------------------------------------------------
    if policy == "temp3":
        closest = temp_neighbours(
            hist,
            target,
            3,
        )
        p = majority(
            [stops[x["id"]] for x in closest]
        )
        return p if p is not None else g

    # ---------------------------------------------------------
    # G: temperature analog + global
    # global gets two votes
    # ---------------------------------------------------------
    if policy == "temp_global":
        closest = temp_neighbours(
            hist,
            target,
            3,
        )

        vals = [g, g]
        vals += [
            stops[x["id"]]
            for x in closest
        ]

        return majority(vals)

    # ---------------------------------------------------------
    # H: same track + temperature analog ensemble
    # ---------------------------------------------------------
    if policy == "track_temp_ensemble":
        closest = temp_neighbours(
            hist,
            target,
            3,
        )

        vals = [g, g]

        if tv:
            vals.append(tv[-1])

        vals += [
            stops[x["id"]]
            for x in closest
        ]

        return majority(vals)

    raise ValueError(policy)


policies = [
    "global",
    "track_latest",
    "track_latest_2plus",
    "agree_or_global",
    "weighted_global",
    "temp3",
    "temp_global",
    "track_temp_ensemble",
]

results = {
    p: []
    for p in policies
}


for target in races:
    hist = [
        x
        for x in races
        if x["race_date"] < target["race_date"]
    ]

    if not hist:
        continue

    actual = stops[target["id"]]

    for policy in policies:
        pred = predict(
            policy,
            hist,
            target,
        )

        if pred is not None:
            results[policy].append(
                {
                    "season": target["season_year"],
                    "round": target["round_number"],
                    "race_id": target["id"],
                    "actual": actual,
                    "pred": pred,
                    "match": actual == pred,
                }
            )


print("\n===== ADAPTIVE POLICY BENCHMARK =====")

summary = []

for policy in policies:
    rows = results[policy]

    if not rows:
        continue

    correct = sum(
        x["match"]
        for x in rows
    )

    acc = 100 * correct / len(rows)

    summary.append(
        (
            acc,
            policy,
            correct,
            len(rows),
        )
    )

    print(
        f"{policy:24s} "
        f"{correct}/{len(rows)} "
        f"= {acc:.1f}%"
    )

print("\n===== BY SEASON =====")

for policy in policies:
    rows = results[policy]

    print(f"\n{policy}")

    for season in sorted(
        set(x["season"] for x in rows)
    ):
        s = [
            x
            for x in rows
            if x["season"] == season
        ]

        correct = sum(
            x["match"]
            for x in s
        )

        print(
            f"  {season}: "
            f"{correct}/{len(s)} "
            f"= {100*correct/len(s):.1f}%"
        )

print("\n===== RANKING =====")

for i, x in enumerate(
    sorted(
        summary,
        reverse=True,
    ),
    1,
):
    print(
        i,
        x[1],
        f"{x[0]:.1f}%"
    )


best = max(
    summary,
    key=lambda x: x[0],
)

print("\nBEST POLICY:")
print(best[1])
print(
    f"{best[2]}/{best[3]} "
    f"= {best[0]:.1f}%"
)


print("\n===== FAILURES FOR BEST =====")

for x in results[best[1]]:
    if not x["match"]:
        print(
            x["race_id"],
            f"{x['season']}-R{x['round']}",
            "actual=",
            x["actual"],
            "pred=",
            x["pred"],
        )
