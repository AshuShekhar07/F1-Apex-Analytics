from collections import Counter, defaultdict
from sqlalchemy import create_engine, text
import os

engine = create_engine(os.environ["DATABASE_URL"])

ERA = "era2_18inch_groundeffect"

FINISHED = [
    "Finished","+1 Lap","+2 Laps","+3 Laps",
    "+4 Laps","+5 Laps","+6 Laps"
]


def q(sql, params=None):
    with engine.connect() as c:
        return c.execute(
            text(sql),
            params or {}
        ).mappings().all()


# ------------------------------------------------------------
# RACES
# ------------------------------------------------------------

races = q("""
SELECT
    r.id,
    r.track_id,
    r.season_year,
    r.round_number,
    r.race_date,
    r.regulation_era
FROM races r
WHERE r.regulation_era = :era
  AND r.race_date IS NOT NULL
  AND r.season_year BETWEEN 2022 AND 2025
  AND EXISTS (
      SELECT 1
      FROM sessions s
      JOIN session_weather sw
        ON sw.session_id = s.id
      WHERE s.race_id = r.id
        AND s.session_type = 'R'
        AND sw.rainfall = FALSE
  )
ORDER BY r.race_date
""", {"era": ERA})


# ------------------------------------------------------------
# GET TOP-3 STRATEGIES
# ------------------------------------------------------------

def top3_strategies(race_id):
    rows = q("""
        SELECT
            rs.race_entry_id,
            rs.finishing_position,
            rs.stint_number,
            rs.compound,
            rs.start_lap,
            rs.end_lap,
            rr.status
        FROM race_stints rs
        JOIN sessions s
          ON s.race_id = rs.race_id
         AND s.session_type = 'R'
        JOIN race_results rr
          ON rr.race_entry_id = rs.race_entry_id
         AND rr.session_id = s.id
        WHERE rs.race_id = :rid
          AND rs.finishing_position BETWEEN 1 AND 3
          AND rr.status = ANY(:finished)
        ORDER BY
            rs.finishing_position,
            rs.stint_number
    """, {
        "rid": race_id,
        "finished": FINISHED,
    })

    grouped = {}

    for x in rows:
        entry = x["race_entry_id"]

        grouped.setdefault(
            entry,
            {
                "position": int(
                    x["finishing_position"]
                ),
                "sequence": [],
            }
        )

        grouped[entry]["sequence"].append(
            str(x["compound"]).upper()
        )

    result = []

    for x in grouped.values():
        if not x["sequence"]:
            continue

        result.append(x)

    result.sort(
        key=lambda x: x["position"]
    )

    return result


race_data = {}

for r in races:
    strategies = top3_strategies(r["id"])

    if len(strategies) < 1:
        continue

    race_data[r["id"]] = {
        "race": r,
        "drivers": strategies,
    }


# ------------------------------------------------------------
# LABELS
# ------------------------------------------------------------

def stops(sequence):
    return max(
        0,
        len(sequence) - 1
    )


def top3_stop_label(drivers):
    """
    Majority stop count among P1-P3.
    Ties go to the lower stop count.
    """
    counts = Counter(
        stops(x["sequence"])
        for x in drivers
    )

    return min(
        counts,
        key=lambda k: (
            -counts[k],
            k,
        )
    )


def top3_sequence_label(drivers):
    """
    Most common exact sequence among P1-P3.
    P1 breaks ties.
    """
    counts = Counter(
        tuple(x["sequence"])
        for x in drivers
    )

    best = None
    best_key = None

    for seq, count in counts.items():
        first_position = min(
            x["position"]
            for x in drivers
            if tuple(x["sequence"]) == seq
        )

        key = (
            count,
            -first_position,
        )

        if best_key is None or key > best_key:
            best_key = key
            best = seq

    return best


# ------------------------------------------------------------
# PRIOR MODE
# ------------------------------------------------------------

def global_prior(history):
    values = [
        top3_stop_label(
            race_data[rid]["drivers"]
        )
        for rid in history
    ]

    if not values:
        return None

    return Counter(values).most_common(1)[0][0]


def recent_prior(history, n):
    values = [
        top3_stop_label(
            race_data[rid]["drivers"]
        )
        for rid in history[-n:]
    ]

    if not values:
        return None

    return Counter(values).most_common(1)[0][0]


def track_prior(history, track_id):
    values = [
        top3_stop_label(
            race_data[rid]["drivers"]
        )
        for rid in history
        if race_data[rid]["race"]["track_id"] == track_id
    ]

    if not values:
        return None

    return Counter(values).most_common(1)[0][0]


# ------------------------------------------------------------
# POLICIES
# ------------------------------------------------------------

def predict(policy, target, history):
    g = global_prior(history)

    same_track = [
        rid for rid in history
        if race_data[rid]["race"]["track_id"]
        == target["race"]["track_id"]
    ]

    if policy == "global":
        return g

    if policy == "recent3":
        return recent_prior(history, 3)

    if policy == "track":
        return (
            track_prior(
                history,
                target["race"]["track_id"]
            )
            or g
        )

    if policy == "track_latest":
        if same_track:
            return top3_stop_label(
                race_data[
                    same_track[-1]
                ]["drivers"]
            )
        return g

    if policy == "track2":
        if len(same_track) >= 2:
            return Counter([
                top3_stop_label(
                    race_data[x]["drivers"]
                )
                for x in same_track[-2:]
            ]).most_common(1)[0][0]
        return g

    if policy == "track_or_global":
        t = track_prior(
            history,
            target["race"]["track_id"]
        )

        if t is None:
            return g

        # Only override global if track history has
        # at least two races and majority is clear.
        values = [
            top3_stop_label(
                race_data[x]["drivers"]
            )
            for x in same_track
        ]

        counts = Counter(values)

        if (
            len(values) >= 2
            and counts[t] / len(values) >= 0.60
        ):
            return t

        return g

    raise ValueError(policy)


policies = [
    "global",
    "recent3",
    "track",
    "track_latest",
    "track2",
    "track_or_global",
]


results = {
    p: []
    for p in policies
}


# ------------------------------------------------------------
# STRICT WALK-FORWARD
# ------------------------------------------------------------

ordered_ids = sorted(
    race_data,
    key=lambda rid:
        race_data[rid]["race"]["race_date"]
)


for rid in ordered_ids:

    target = race_data[rid]["race"]

    history = [
        x
        for x in ordered_ids
        if race_data[x]["race"]["race_date"]
        < target["race_date"]
    ]

    if len(history) < 3:
        continue

    actual = top3_stop_label(
        race_data[rid]["drivers"]
    )

    for policy in policies:
        pred = predict(
            policy,
            race_data[rid],
            history,
        )

        if pred is None:
            continue

        results[policy].append({
            "race_id": rid,
            "season": target["season_year"],
            "round": target["round_number"],
            "actual": actual,
            "pred": pred,
            "match": pred == actual,
        })


print("\n================================")
print("TOP-3 STRATEGY TARGET BENCHMARK")
print("================================")

summary = []

for policy in policies:
    rows = results[policy]

    correct = sum(
        x["match"]
        for x in rows
    )

    acc = (
        100 * correct / len(rows)
        if rows
        else 0
    )

    summary.append(
        (
            acc,
            policy,
            correct,
            len(rows),
        )
    )

    print(
        f"{policy:20s} "
        f"{correct}/{len(rows)} "
        f"= {acc:.1f}%"
    )


print("\n===== BY SEASON =====")

for policy in policies:
    print(f"\n{policy}")

    rows = results[policy]

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


best = max(
    summary,
    key=lambda x: x[0]
)

print("\n===== BEST =====")
print(
    best[1],
    f"{best[2]}/{best[3]} "
    f"= {best[0]:.1f}%"
)


# ------------------------------------------------------------
# EXACT SEQUENCE BASELINES
# ------------------------------------------------------------

print("\n===== EXACT TOP-3 SEQUENCE =====")

seq_rows = []

for rid in ordered_ids:
    target = race_data[rid]["race"]

    history = [
        x
        for x in ordered_ids
        if race_data[x]["race"]["race_date"]
        < target["race_date"]
    ]

    if len(history) < 3:
        continue

    actual = top3_sequence_label(
        race_data[rid]["drivers"]
    )

    # Global sequence majority.
    history_sequences = [
        top3_sequence_label(
            race_data[x]["drivers"]
        )
        for x in history
    ]

    pred = Counter(
        history_sequences
    ).most_common(1)[0][0]

    seq_rows.append({
        "race_id": rid,
        "season": target["season_year"],
        "actual": actual,
        "pred": pred,
        "match": actual == pred,
    })


correct = sum(
    x["match"]
    for x in seq_rows
)

print(
    "global exact sequence:",
    f"{correct}/{len(seq_rows)}",
    f"= {100*correct/len(seq_rows):.1f}%"
)


print("\n===== ACTUAL TOP-3 LABEL DISTRIBUTION =====")

print(
    Counter(
        top3_stop_label(
            race_data[rid]["drivers"]
        )
        for rid in ordered_ids
    )
)

print("\n===== SAMPLE RACES =====")

for rid in ordered_ids[-15:]:
    r = race_data[rid]

    print(
        rid,
        f"{r['race']['season_year']}"
        f"-R{r['race']['round_number']}",
        [
            {
                "P": x["position"],
                "stops": stops(x["sequence"]),
                "sequence": x["sequence"],
            }
            for x in r["drivers"]
        ],
        "label=",
        top3_stop_label(
            r["drivers"]
        )
    )
