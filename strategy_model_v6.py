import os
import math
from collections import Counter, defaultdict

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

engine = create_engine(os.environ["DATABASE_URL"])

ERA = "era2_18inch_groundeffect"

FINISHED = (
    "Finished",
    "+1 Lap",
    "+2 Laps",
    "+3 Laps",
    "+4 Laps",
    "+5 Laps",
    "+6 Laps",
)

VALID = {"SOFT", "MEDIUM", "HARD"}


def q(sql, params=None):
    with engine.connect() as conn:
        return conn.execute(
            text(sql),
            params or {}
        ).mappings().all()


# ============================================================
# LOAD ALL STATIC / HISTORICAL DATA
# ============================================================

def load_races():
    rows = q("""
        SELECT
            r.id,
            r.track_id,
            r.season_year,
            r.round_number,
            r.race_date,
            r.weekend_format,
            r.regulation_era,

            sw.air_temp_avg,
            sw.track_temp_avg,
            sw.humidity_avg,
            sw.wind_speed_avg,
            sw.rainfall,
            sw.rain_onset_lap

        FROM races r

        LEFT JOIN sessions s
          ON s.race_id = r.id
         AND s.session_type = 'R'

        LEFT JOIN session_weather sw
          ON sw.session_id = s.id

        WHERE r.regulation_era = :era
          AND r.race_date IS NOT NULL
          AND r.season_year BETWEEN 2022 AND 2026

        ORDER BY r.race_date
    """, {"era": ERA})

    return {
        int(x["id"]): dict(x)
        for x in rows
    }


def load_nominations():
    rows = q("""
        SELECT race_id, label
        FROM race_compound_nominations
    """)

    out = defaultdict(set)

    for x in rows:
        label = str(x["label"]).upper()

        if label in VALID:
            out[int(x["race_id"])].add(label)

    return dict(out)


def load_strategies():
    rows = q("""
        SELECT
            rs.race_id,
            rs.stint_number,
            rs.compound,
            rs.start_lap,
            rs.end_lap

        FROM race_stints rs

        JOIN races r
          ON r.id = rs.race_id

        JOIN sessions s
          ON s.race_id = r.id
         AND s.session_type = 'R'

        JOIN race_results rr
          ON rr.race_entry_id = rs.race_entry_id
         AND rr.session_id = s.id

        WHERE r.regulation_era = :era
          AND rs.finishing_position = 1
          AND rr.status = ANY(:finished)

        ORDER BY
            rs.race_id,
            rs.stint_number
    """, {
        "era": ERA,
        "finished": list(FINISHED),
    })

    out = defaultdict(list)

    for x in rows:
        c = str(x["compound"]).upper()

        if c not in VALID:
            continue

        out[int(x["race_id"])].append({
            "compound": c,
            "start": (
                int(x["start_lap"])
                if x["start_lap"] is not None
                else None
            ),
            "end": (
                int(x["end_lap"])
                if x["end_lap"] is not None
                else None
            ),
        })

    return dict(out)


def load_fp2():
    rows = q("""
        SELECT
            s.race_id,
            l.race_entry_id,
            l.tire_compound,
            l.lap_number,
            l.lap_time

        FROM sessions s

        JOIN races r
          ON r.id = s.race_id

        JOIN laps l
          ON l.session_id = s.id

        WHERE s.session_type = 'FP2'
          AND r.regulation_era = :era
          AND l.tire_compound IN (
              'SOFT',
              'MEDIUM',
              'HARD'
          )
          AND l.lap_time IS NOT NULL
          AND l.is_valid = TRUE

        ORDER BY
            s.race_id,
            l.race_entry_id,
            l.tire_compound,
            l.lap_number
    """, {"era": ERA})

    return [dict(x) for x in rows]


# ============================================================
# FP2 DEGRADATION
# ============================================================

def build_fp2(rows):
    if not rows:
        return {}

    df = pd.DataFrame(rows)

    output = {}

    for race_id, rdf in df.groupby("race_id"):

        data = {}

        for compound, cdf in rdf.groupby(
            "tire_compound"
        ):
            slopes = []
            medians = []

            for _, run in cdf.groupby(
                "race_entry_id"
            ):
                run = run.sort_values(
                    "lap_number"
                )

                vals = run["lap_time"].astype(
                    float
                ).to_numpy()

                if len(vals) < 8:
                    continue

                median = np.median(vals)
                mad = np.median(
                    np.abs(vals - median)
                )

                if mad > 0:
                    vals = vals[
                        np.abs(vals - median)
                        <= max(3 * mad, 2.0)
                    ]

                if len(vals) < 8:
                    continue

                first = np.median(
                    vals[:4]
                )

                last = np.median(
                    vals[-4:]
                )

                slope = (
                    last - first
                ) / max(len(vals) - 1, 1)

                if -0.15 <= slope <= 0.15:
                    slopes.append(
                        float(slope)
                    )

                medians.append(
                    float(np.median(vals))
                )

            if slopes:
                data[
                    compound.lower()
                    + "_slope"
                ] = float(
                    np.median(slopes)
                )

            if medians:
                data[
                    compound.lower()
                    + "_pace"
                ] = float(
                    np.median(medians)
                )

        output[int(race_id)] = data

    return output


# ============================================================
# PRE-RACE REPRESENTATION
# ============================================================

def feature(race, nominations, fp2):
    rid = int(race["id"])

    nom = nominations.get(
        rid,
        set()
    )

    f = fp2.get(
        rid,
        {}
    )

    return {
        "track_id": int(
            race["track_id"]
        ),

        "season": int(
            race["season_year"]
        ),

        "round": int(
            race["round_number"]
        ),

        "format": str(
            race["weekend_format"]
        ),

        "track_temp": (
            float(race["track_temp_avg"])
            if race["track_temp_avg"] is not None
            else None
        ),

        "air_temp": (
            float(race["air_temp_avg"])
            if race["air_temp_avg"] is not None
            else None
        ),

        "humidity": (
            float(race["humidity_avg"])
            if race["humidity_avg"] is not None
            else None
        ),

        "wind": (
            float(race["wind_speed_avg"])
            if race["wind_speed_avg"] is not None
            else None
        ),

        "rain": int(
            bool(race["rainfall"])
        ),

        "rain_onset": (
            float(race["rain_onset_lap"])
            if race["rain_onset_lap"] is not None
            else None
        ),

        "soft_nom": int(
            "SOFT" in nom
        ),

        "medium_nom": int(
            "MEDIUM" in nom
        ),

        "hard_nom": int(
            "HARD" in nom
        ),

        "three_nom": int(
            nom >= {
                "SOFT",
                "MEDIUM",
                "HARD"
            }
        ),

        "soft_slope": f.get(
            "soft_slope"
        ),

        "medium_slope": f.get(
            "medium_slope"
        ),

        "hard_slope": f.get(
            "hard_slope"
        ),

        "soft_pace": f.get(
            "soft_pace"
        ),

        "medium_pace": f.get(
            "medium_pace"
        ),

        "hard_pace": f.get(
            "hard_pace"
        ),
    }


# ============================================================
# HISTORICAL PRIORS
# ============================================================

def prior_distribution(
    target,
    races,
    strategies,
):
    history = []

    for rid, race in races.items():

        if rid == target["id"]:
            continue

        if race["race_date"] >= target["race_date"]:
            continue

        if (
            race["regulation_era"]
            != ERA
        ):
            continue

        if rid in strategies:
            history.append(rid)

    stops = [
        len(strategies[rid]) - 1
        for rid in history
    ]

    if not stops:
        return {}, history

    counts = Counter(
        min(x, 3)
        for x in stops
    )

    total = sum(
        counts.values()
    )

    probs = {
        k: counts[k] / total
        for k in (1, 2, 3)
    }

    return probs, history


# ============================================================
# STRATEGY SIMILARITY
# ============================================================

def numeric_distance(
    a,
    b,
    key,
    scale,
):
    x = a.get(key)
    y = b.get(key)

    if x is None or y is None:
        return None

    try:
        x = float(x)
        y = float(y)
    except Exception:
        return None

    if not (
        math.isfinite(x)
        and math.isfinite(y)
    ):
        return None

    return abs(x - y) / scale


def compound_similarity(
    a,
    b,
):
    union = a | b

    if not union:
        return 1.0

    return (
        len(a & b)
        / len(union)
    )


def similarity(
    target,
    candidate,
    tf,
    cf,
    tn,
    cn,
):
    distance = 0.0
    weight = 0.0

    # SAME CIRCUIT
    if (
        target["track_id"]
        == candidate["track_id"]
    ):
        distance += 0.0
        weight += 2.5
    else:
        distance += 2.5
        weight += 1.0

    # WEATHER
    d = numeric_distance(
        tf,
        cf,
        "track_temp",
        5.0,
    )

    if d is not None:
        distance += min(
            d,
            2.5
        )

    # rainfall is highly structural
    if (
        tf["rain"]
        != cf["rain"]
    ):
        distance += 5.0

    # rain timing
    if (
        tf["rain"]
        and cf["rain"]
    ):
        d = numeric_distance(
            tf,
            cf,
            "rain_onset",
            10.0,
        )

        if d is not None:
            distance += min(
                d,
                2.0
            )

    # nominations
    distance += (
        1.25
        * (
            1.0
            - compound_similarity(
                tn,
                cn,
            )
        )
    )

    # weekend format
    if (
        tf["format"]
        != cf["format"]
    ):
        distance += 0.75

    # FP2 degradation
    for key, scale in (
        ("soft_slope", 0.025),
        ("medium_slope", 0.025),
        ("hard_slope", 0.025),
    ):
        d = numeric_distance(
            tf,
            cf,
            key,
            scale,
        )

        if d is not None:
            distance += min(
                d * 0.65,
                1.5
            )

    # FP2 compound relative pace
    for key, scale in (
        ("soft_pace", 2.0),
        ("medium_pace", 2.0),
        ("hard_pace", 2.0),
    ):
        d = numeric_distance(
            tf,
            cf,
            key,
            scale,
        )

        if d is not None:
            distance += min(
                d * 0.30,
                0.8
            )

    return math.exp(
        -distance / 3.0
    )


# ============================================================
# PREDICTION
# ============================================================

def predict(
    target_id,
    races,
    nominations,
    strategies,
    features,
):
    target = races[target_id]

    target_features = features[
        target_id
    ]

    target_nom = nominations.get(
        target_id,
        set()
    )

    priors, history = prior_distribution(
        target,
        races,
        strategies,
    )

    if not history:
        return None

    # Collect historical analog evidence.
    stop_scores = Counter()
    sequence_scores = Counter()

    analog_count = 0

    for rid in history:

        candidate = races[rid]

        w = similarity(
            target,
            candidate,
            target_features,
            features[rid],
            target_nom,
            nominations.get(
                rid,
                set()
            ),
        )

        if w <= 0:
            continue

        sequence = tuple(
            x["compound"]
            for x in strategies[rid]
        )

        stops = min(
            len(sequence) - 1,
            3
        )

        # Historical global prior provides stability.
        stop_scores[stops] += (
            0.55 * w
        )

        sequence_scores[
            sequence
        ] += w

        analog_count += 1

    # Normalize analog evidence.
    total = sum(
        stop_scores.values()
    )

    analog_probs = {}

    if total > 0:
        analog_probs = {
            k: stop_scores[k] / total
            for k in (1, 2, 3)
        }

    # Blend:
    # 45% global historical prior
    # 55% condition-aware analog evidence.
    final = {}

    for k in (1, 2, 3):
        final[k] = (
            0.45 * priors.get(k, 0)
            + 0.55 * analog_probs.get(k, 0)
        )

    # --------------------------------------------------------
    # IMPORTANT STABILITY RULE
    #
    # A 3-stop result must have genuine evidence.
    # Don't let a single strange analog flip the prediction.
    # --------------------------------------------------------
    three_evidence = sequence_scores

    three_score = sum(
        score
        for seq, score in three_evidence.items()
        if len(seq) >= 4
    )

    two_score = sum(
        score
        for seq, score in three_evidence.items()
        if len(seq) == 3
    )

    if three_score < (
        0.35 * max(
            sum(sequence_scores.values()),
            1e-9,
        )
    ):
        final[3] *= 0.40

    # Re-normalize.
    total = sum(
        final.values()
    )

    if total > 0:
        final = {
            k: v / total
            for k, v in final.items()
        }

    predicted_stops = max(
        final,
        key=final.get,
    )

    # --------------------------------------------------------
    # STRATEGY SELECTION:
    # select from the predicted stop class,
    # but use the complete sequence posterior.
    # --------------------------------------------------------
    candidates = []

    for sequence, score in sequence_scores.items():

        if min(
            len(sequence) - 1,
            3
        ) != predicted_stops:
            continue

        candidates.append(
            (
                score,
                sequence,
            )
        )

    candidates.sort(
        reverse=True
    )

    if candidates:
        primary_score, primary = candidates[0]
    else:
        primary_score = 0.0
        primary = None

    return {
        "predicted_stops": predicted_stops,
        "probabilities": {
            str(k): round(
                final[k] * 100,
                1,
            )
            for k in (1, 2, 3)
        },
        "primary_sequence": (
            list(primary)
            if primary
            else None
        ),
        "analog_count": analog_count,
        "history_count": len(history),
    }


# ============================================================
# WALK-FORWARD BENCHMARK
# ============================================================

def main():
    print(
        "Loading data..."
    )

    races = load_races()
    nominations = load_nominations()
    strategies = load_strategies()

    print(
        "Loading FP2..."
    )

    fp2_rows = load_fp2()
    fp2 = build_fp2(
        fp2_rows
    )

    features = {
        rid: feature(
            race,
            nominations,
            fp2,
        )
        for rid, race
        in races.items()
    }

    targets = [
        rid
        for rid, r in races.items()
        if (
            r["season_year"]
            in (2022, 2023, 2024, 2025)
            and r["race_date"] is not None
            and r["rainfall"] is False
            and rid in strategies
        )
    ]

    targets.sort(
        key=lambda rid:
        races[rid]["race_date"]
    )

    results = []

    for i, rid in enumerate(
        targets,
        1,
    ):
        result = predict(
            rid,
            races,
            nominations,
            strategies,
            features,
        )

        if result is None:
            print(
                f"[{i}/{len(targets)}] "
                f"{rid} unavailable"
            )
            continue

        actual = min(
            len(
                strategies[rid]
            ) - 1,
            3,
        )

        correct = (
            actual
            == result["predicted_stops"]
        )

        results.append({
            "race_id": rid,
            "season": races[rid]["season_year"],
            "round": races[rid]["round_number"],
            "actual": actual,
            "predicted": result[
                "predicted_stops"
            ],
            "match": correct,
            "probabilities": result[
                "probabilities"
            ],
        })

        print(
            f"[{i}/{len(targets)}] "
            f"{rid} "
            f"{races[rid]['season_year']}-R"
            f"{races[rid]['round_number']} "
            f"actual={actual} "
            f"pred={result['predicted_stops']} "
            f"match={correct} "
            f"p={result['probabilities']}"
        )

    print(
        "\n=============================="
    )
    print(
        "V6 CLEAN WALK-FORWARD"
    )
    print(
        "=============================="
    )

    if not results:
        print(
            "No results."
        )
        return

    matches = sum(
        x["match"]
        for x in results
    )

    print(
        "validated:",
        len(results),
    )

    print(
        "stop-count:",
        f"{matches}/{len(results)}",
        f"={100*matches/len(results):.1f}%"
    )

    baseline = Counter(
        x["actual"]
        for x in results
    ).most_common(1)[0][0]

    baseline_matches = sum(
        x["actual"] == baseline
        for x in results
    )

    print(
        "dominant baseline:",
        baseline,
        "stop(s)"
    )

    print(
        "baseline accuracy:",
        f"{baseline_matches}/{len(results)}",
        f"={100*baseline_matches/len(results):.1f}%"
    )

    print(
        "\nBY SEASON"
    )

    for season in sorted(
        set(x["season"] for x in results)
    ):
        s = [
            x
            for x in results
            if x["season"] == season
        ]

        m = sum(
            x["match"]
            for x in s
        )

        print(
            season,
            f"{m}/{len(s)}",
            f"={100*m/len(s):.1f}%"
        )

    print(
        "\nFAILURES"
    )

    for x in results:
        if not x["match"]:
            print(
                x["race_id"],
                f"{x['season']}-R{x['round']}",
                "actual=",
                x["actual"],
                "pred=",
                x["predicted"],
                "p=",
                x["probabilities"],
            )


if __name__ == "__main__":
    main()
