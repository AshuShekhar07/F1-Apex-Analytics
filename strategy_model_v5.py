import math
import os
from collections import Counter, defaultdict
from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

engine = create_engine(os.environ["DATABASE_URL"])

FINISHED = (
    "Finished",
    "+1 Lap",
    "+2 Laps",
    "+3 Laps",
    "+4 Laps",
    "+5 Laps",
    "+6 Laps",
)

ERA = "era2_18inch_groundeffect"
MIN_HISTORY = 8

VALID_COMPOUNDS = {
    "SOFT",
    "MEDIUM",
    "HARD",
    "INTERMEDIATE",
    "WET",
    "ULTRASOFT",
    "SUPERSOFT",
    "HYPERSOFT",
}


# ============================================================
# BULK DATA LOAD
# ============================================================

def load_races():
    with engine.connect() as conn:
        rows = conn.execute(text("""
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
                sw.rain_onset_lap,

                COALESCE(r.safety_car_periods, 0) AS safety_car_periods,
                COALESCE(r.vsc_periods, 0) AS vsc_periods,
                COALESCE(r.red_flags, 0) AS red_flags

            FROM races r

            LEFT JOIN sessions sr
              ON sr.race_id = r.id
             AND sr.session_type = 'R'

            LEFT JOIN session_weather sw
              ON sw.session_id = sr.id

            WHERE r.regulation_era = :era
              AND r.race_date IS NOT NULL
              AND r.season_year BETWEEN 2022 AND 2026

            ORDER BY r.race_date
        """), {"era": ERA}).mappings().all()

    return {
        int(r["id"]): dict(r)
        for r in rows
    }


def load_nominations():
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT
                race_id,
                label
            FROM race_compound_nominations
        """)).mappings().all()

    result = defaultdict(set)

    for r in rows:
        label = str(r["label"]).upper()
        if label in VALID_COMPOUNDS:
            result[int(r["race_id"])].add(label)

    return dict(result)


def load_winner_strategies():
    with engine.connect() as conn:
        rows = conn.execute(text("""
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
        """), {
            "era": ERA,
            "finished": list(FINISHED),
        }).mappings().all()

    grouped = defaultdict(list)

    for r in rows:
        compound = str(r["compound"]).upper()

        if compound not in VALID_COMPOUNDS:
            continue

        grouped[int(r["race_id"])].append({
            "compound": compound,
            "start_lap": int(r["start_lap"]) if r["start_lap"] is not None else None,
            "end_lap": int(r["end_lap"]) if r["end_lap"] is not None else None,
        })

    return dict(grouped)


def load_fp2():
    with engine.connect() as conn:
        rows = conn.execute(text("""
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
              AND r.season_year BETWEEN 2022 AND 2026
              AND l.tire_compound IN ('SOFT', 'MEDIUM', 'HARD')
              AND l.lap_time IS NOT NULL
              AND l.is_valid = TRUE

            ORDER BY
                s.race_id,
                l.race_entry_id,
                l.tire_compound,
                l.lap_number
        """), {"era": ERA}).mappings().all()

    return [dict(r) for r in rows]


# ============================================================
# FP2 FEATURE EXTRACTION
# ============================================================

def build_fp2_features(rows):
    if not rows:
        return {}

    df = pd.DataFrame(rows)

    out = {}

    for race_id, race_df in df.groupby("race_id"):
        feature = {}

        compound_medians = {}
        compound_slopes = {}

        for compound, comp_df in race_df.groupby("tire_compound"):

            run_slopes = []

            for _, run in comp_df.groupby("race_entry_id"):
                run = run.sort_values("lap_number")

                vals = run["lap_time"].astype(float).to_numpy()

                if len(vals) < 8:
                    continue

                # Robust outlier filtering.
                med = np.median(vals)
                mad = np.median(np.abs(vals - med))

                if mad > 0:
                    vals = vals[
                        np.abs(vals - med) <= max(3.0 * mad, 2.0)
                    ]

                if len(vals) < 8:
                    continue

                first = float(np.median(vals[:4]))
                last = float(np.median(vals[-4:]))

                slope = (last - first) / max(len(vals) - 1, 1)

                # Extremely positive/negative slopes are usually
                # traffic, pit entry/exit, or corrupted runs.
                if -0.20 <= slope <= 0.20:
                    run_slopes.append(slope)

            if run_slopes:
                compound_slopes[compound] = float(
                    np.median(run_slopes)
                )

            values = comp_df["lap_time"].astype(float).to_numpy()

            if len(values) >= 8:
                med = np.median(values)
                mad = np.median(np.abs(values - med))

                if mad > 0:
                    values = values[
                        np.abs(values - med) <= max(3.0 * mad, 2.0)
                    ]

                if len(values) >= 5:
                    compound_medians[compound] = float(
                        np.median(values)
                    )

        feature["soft_slope"] = compound_slopes.get("SOFT")
        feature["medium_slope"] = compound_slopes.get("MEDIUM")
        feature["hard_slope"] = compound_slopes.get("HARD")

        if "SOFT" in compound_medians and "MEDIUM" in compound_medians:
            feature["soft_medium_gap"] = (
                compound_medians["SOFT"] -
                compound_medians["MEDIUM"]
            )
        else:
            feature["soft_medium_gap"] = None

        if "MEDIUM" in compound_medians and "HARD" in compound_medians:
            feature["medium_hard_gap"] = (
                compound_medians["MEDIUM"] -
                compound_medians["HARD"]
            )
        else:
            feature["medium_hard_gap"] = None

        feature["fp2_compounds"] = len(compound_medians)

        out[int(race_id)] = feature

    return out


# ============================================================
# PRE-RACE FEATURE REPRESENTATION
# ============================================================

def race_features(race, nominations, fp2_features, weather_override=None):
    rid = int(race["id"])

    nom = nominations.get(rid, set())
    fp = fp2_features.get(rid, {})

    w = weather_override or {}

    track_temp = w.get(
        "track_temp",
        race["track_temp_avg"],
    )
    air_temp = w.get(
        "air_temp",
        race["air_temp_avg"],
    )
    humidity = w.get(
        "humidity",
        race["humidity_avg"],
    )
    wind = w.get(
        "wind_speed",
        race["wind_speed_avg"],
    )
    rain = w.get(
        "rain_expected",
        race["rainfall"],
    )
    rain_onset = w.get(
        "rain_onset_lap",
        race["rain_onset_lap"],
    )

    return {
        "track_id": int(race["track_id"]),
        "weekend_format": str(race["weekend_format"]),

        "track_temp": (
            float(track_temp)
            if track_temp is not None
            else None
        ),

        "air_temp": (
            float(air_temp)
            if air_temp is not None
            else None
        ),

        "humidity": (
            float(humidity)
            if humidity is not None
            else None
        ),

        "wind": (
            float(wind)
            if wind is not None
            else None
        ),

        "rain": int(bool(rain)),

        "rain_onset": (
            float(rain_onset)
            if rain_onset is not None
            else None
        ),

        "round_progress": (
            float(race["round_number"]) / 24.0
        ),

        "sc": float(race["safety_car_periods"] or 0),
        "vsc": float(race["vsc_periods"] or 0),
        "red": float(race["red_flags"] or 0),

        "nom_soft": int("SOFT" in nom),
        "nom_medium": int("MEDIUM" in nom),
        "nom_hard": int("HARD" in nom),

        "fp2_soft_slope": fp.get("soft_slope"),
        "fp2_medium_slope": fp.get("medium_slope"),
        "fp2_hard_slope": fp.get("hard_slope"),
        "fp2_soft_medium_gap": fp.get("soft_medium_gap"),
        "fp2_medium_hard_gap": fp.get("medium_hard_gap"),
        "fp2_compounds": fp.get("fp2_compounds", 0),
    }


# ============================================================
# ROBUST NORMALIZATION
# ============================================================

NUMERIC = [
    "track_temp",
    "air_temp",
    "humidity",
    "wind",
    "rain_onset",
    "round_progress",
    "sc",
    "vsc",
    "red",
    "fp2_soft_slope",
    "fp2_medium_slope",
    "fp2_hard_slope",
    "fp2_soft_medium_gap",
    "fp2_medium_hard_gap",
]

FP2_NUMERIC = [
    "fp2_soft_slope",
    "fp2_medium_slope",
    "fp2_hard_slope",
    "fp2_soft_medium_gap",
    "fp2_medium_hard_gap",
]


def robust_scale(values):
    vals = [
        float(x)
        for x in values
        if x is not None and np.isfinite(float(x))
    ]

    if len(vals) < 3:
        return 1.0

    q25, q75 = np.percentile(vals, [25, 75])
    iqr = q75 - q25

    if iqr < 1e-9:
        return 1.0

    return float(iqr)


# ============================================================
# SIMILARITY
# ============================================================

def nomination_distance(a, b):
    if not a and not b:
        return 0.0

    union = len(a | b)

    if union == 0:
        return 0.0

    return 1.0 - (
        len(a & b) / union
    )


def fp2_distance(target, candidate, scales):
    pieces = []

    for key in FP2_NUMERIC:
        a = target.get(key)
        b = candidate.get(key)

        if a is None or b is None:
            continue

        try:
            a = float(a)
            b = float(b)
        except Exception:
            continue

        if not np.isfinite(a) or not np.isfinite(b):
            continue

        scale = scales.get(key, 1.0)

        pieces.append(
            abs(a - b) / scale
        )

    if not pieces:
        return None

    return float(np.mean(pieces))


def race_distance(
    target_race,
    candidate_race,
    target_features,
    candidate_features,
    target_nom,
    candidate_nom,
    fp2_scales,
):
    """
    Lower = more similar.

    Same track is strongly preferred but does NOT completely eliminate
    cross-track analogs. This matters because modern-era same-track
    samples are sparse.
    """

    d = 0.0

    # --------------------------------------------------------
    # TRACK
    # --------------------------------------------------------
    if int(target_race["track_id"]) == int(candidate_race["track_id"]):
        d += 0.0
        same_track_multiplier = 3.0
    else:
        d += 2.25
        same_track_multiplier = 1.0

    # --------------------------------------------------------
    # WEATHER
    # --------------------------------------------------------
    tt = target_features.get("track_temp")
    ct = candidate_features.get("track_temp")

    if tt is not None and ct is not None:
        d += min(abs(tt - ct) / 4.0, 3.0)

    if target_features["rain"] != candidate_features["rain"]:
        d += 5.0

    if (
        target_features["rain"]
        and candidate_features["rain"]
        and target_features.get("rain_onset") is not None
        and candidate_features.get("rain_onset") is not None
    ):
        d += min(
            abs(
                target_features["rain_onset"]
                - candidate_features["rain_onset"]
            ) / 8.0,
            3.0,
        )

    # --------------------------------------------------------
    # WEEKEND FORMAT
    # --------------------------------------------------------
    if (
        target_features["weekend_format"]
        != candidate_features["weekend_format"]
    ):
        d += 0.8

    # --------------------------------------------------------
    # NOMINATIONS
    # --------------------------------------------------------
    d += 1.2 * nomination_distance(
        target_nom,
        candidate_nom,
    )

    # --------------------------------------------------------
    # ROUND / SEASON POSITION
    # --------------------------------------------------------
    d += 0.35 * abs(
        target_features["round_progress"]
        - candidate_features["round_progress"]
    )

    # --------------------------------------------------------
    # FP2
    # --------------------------------------------------------
    fp_d = fp2_distance(
        target_features,
        candidate_features,
        fp2_scales,
    )

    if fp_d is not None:
        d += min(1.8 * fp_d, 4.0)

    # Convert distance into similarity.
    similarity = math.exp(-d / 2.25)

    # Strong same-track prior without making it absolute.
    similarity *= same_track_multiplier

    return similarity, d


# ============================================================
# STRATEGY SCORING
# ============================================================

def recency_weight(target_date, candidate_date):
    days = max(
        0,
        (target_date - candidate_date).days,
    )

    years = days / 365.25

    # Mild decay only.
    return 0.5 ** (
        years / 5.0
    )


def strategy_key(strategy):
    return tuple(
        x["compound"]
        for x in strategy
    )


def candidate_strategies(
    target_race,
    races,
    features,
    nominations,
    strategies,
    fp2_scales,
):
    """
    Unified historical strategy ranking.

    Dry target:
        preserve the existing historical strategy pool.

    Wet/transition target:
        only rainfall-affected historical races whose winner actually
        used INTERMEDIATE or WET are eligible. This prevents a race that
        merely had recorded rainfall but was strategically dry from
        competing with genuinely wet strategies.

    Strict leakage:
        candidate date must be before target date.
        candidate regulation era must match global ERA.
    """
    target_id = int(target_race["id"])
    target_date = target_race["race_date"]

    target_features = features[target_id]
    target_nom = nominations.get(target_id, set())

    target_is_wet = bool(
        target_features.get("rain", 0)
    )

    evidence = defaultdict(float)
    evidence_races = defaultdict(set)
    stop_evidence = Counter()
    analogs = []

    for rid, candidate_race in races.items():

        if rid == target_id:
            continue

        # ----------------------------------------------------
        # HARD LEAKAGE BARRIER
        # ----------------------------------------------------
        if candidate_race["race_date"] >= target_date:
            continue

        if candidate_race["regulation_era"] != ERA:
            continue

        if rid not in strategies:
            continue

        sequence = strategy_key(
            strategies[rid]
        )

        if not sequence:
            continue

        # ----------------------------------------------------
        # WET TARGET CANDIDATE CONSTRAINT
        # ----------------------------------------------------
        #
        # Rain recorded by itself is not enough. The historical
        # winner must actually have used INTERMEDIATE/WET.
        #
        if target_is_wet:
            candidate_features = features[rid]

            if not bool(
                candidate_features.get("rain", 0)
            ):
                continue

            if not any(
                compound in ("INTERMEDIATE", "WET")
                for compound in sequence
            ):
                continue

        candidate_features = features[rid]
        candidate_nom = nominations.get(rid, set())

        similarity, distance = race_distance(
            target_race,
            candidate_race,
            target_features,
            candidate_features,
            target_nom,
            candidate_nom,
            fp2_scales,
        )

        # ----------------------------------------------------
        # ADDITIONAL WET-REGIME SIMILARITY
        # ----------------------------------------------------
        if target_is_wet:
            target_onset = target_features.get(
                "rain_onset"
            )
            candidate_onset = candidate_features.get(
                "rain_onset"
            )

            if (
                target_onset is not None
                and candidate_onset is not None
            ):
                onset_delta = abs(
                    float(target_onset)
                    - float(candidate_onset)
                )

                # Strong bonus for genuinely comparable
                # rain timing. This does not use target outcome.
                if onset_delta <= 5:
                    similarity *= 1.35
                elif onset_delta <= 10:
                    similarity *= 1.20
                elif onset_delta <= 20:
                    similarity *= 1.05
                else:
                    similarity *= 0.85

        recency = recency_weight(
            target_date,
            candidate_race["race_date"],
        )

        weight = similarity * (
            0.75 + 0.25 * recency
        )

        evidence[sequence] += weight
        evidence_races[sequence].add(rid)

        stops = max(
            0,
            len(sequence) - 1,
        )

        stop_evidence[stops] += weight

        analogs.append({
            "race_id": rid,
            "date": candidate_race["race_date"],
            "track_id": candidate_race["track_id"],
            "sequence": list(sequence),
            "stops": stops,
            "distance": distance,
            "similarity": similarity,
            "weight": weight,
        })

    analogs.sort(
        key=lambda x: x["weight"],
        reverse=True,
    )

    ranked = []

    for seq, score_value in evidence.items():
        complexity_penalty = (
            1.0
            if len(seq) <= 2
            else 0.94
            if len(seq) == 3
            else 0.88
        )

        final_score = (
            score_value * complexity_penalty
        )

        ranked.append({
            "sequence": list(seq),
            "score": final_score,
            "raw_score": score_value,
            "races": len(
                evidence_races[seq]
            ),
        })

    ranked.sort(
        key=lambda x: x["score"],
        reverse=True,
    )

    return (
        ranked,
        analogs,
        stop_evidence,
    )


# ============================================================
# WALK-FORWARD MODEL
# ============================================================

def build_model_data():
    races = load_races()
    nominations = load_nominations()
    strategies = load_winner_strategies()
    fp2_rows = load_fp2()
    fp2 = build_fp2_features(fp2_rows)

    features = {
        rid: race_features(
            race,
            nominations,
            fp2,
        )
        for rid, race in races.items()
    }

    # Build robust FP2 scales from the entire dataset of PRE-RACE
    # observable information. These are feature scales, not labels.
    scales = {}

    for key in FP2_NUMERIC:
        scales[key] = robust_scale(
            [
                f.get(key)
                for f in features.values()
            ]
        )

    return (
        races,
        nominations,
        strategies,
        features,
        scales,
    )


def validate_race(
    race_id,
    races,
    nominations,
    strategies,
    features,
    scales,
):
    target = races[race_id]

    history = [
        rid
        for rid, r in races.items()
        if (
            r["race_date"] < target["race_date"]
            and r["regulation_era"] == ERA
            and rid in strategies
        )
    ]

    if len(history) < MIN_HISTORY:
        return None

    ranked, analogs, stop_evidence = candidate_strategies(
        target,
        races,
        features,
        nominations,
        strategies,
        scales,
    )

    if not ranked:
        return None

    actual_sequence = strategy_key(
        strategies[race_id]
    )

    actual_stops = len(actual_sequence) - 1

    predicted_sequence = tuple(
        ranked[0]["sequence"]
    )

    # Aggregate the analog-derived stop distribution.
    stop_total = sum(
        stop_evidence.values()
    )

    stop_probs = {}

    if stop_total > 0:
        for stops in (1, 2, 3):
            stop_probs[stops] = (
                stop_evidence.get(
                    stops,
                    0.0,
                )
                / stop_total
            )

    predicted_stops = (
        max(
            stop_probs,
            key=stop_probs.get,
        )
        if stop_probs
        else max(1, len(predicted_sequence) - 1)
    )

    # Sequence posterior.
    seq_total = sum(
        x["score"]
        for x in ranked
    )

    primary_score = ranked[0]["score"]

    primary_probability = (
        primary_score / seq_total
        if seq_total > 0
        else 0.0
    )

    # Stop confidence is deliberately bounded.
    sorted_stops = sorted(
        stop_probs.items(),
        key=lambda x: x[1],
        reverse=True,
    )

    if len(sorted_stops) >= 2:
        stop_margin = (
            sorted_stops[0][1]
            - sorted_stops[1][1]
        )
    else:
        stop_margin = 1.0

    confidence = (
        45
        + 25 * sorted_stops[0][1]
        + 20 * max(stop_margin, 0)
        + 10 * min(
            len(analogs),
            10
        ) / 10
    )

    confidence = int(
        round(
            max(
                25,
                min(82, confidence),
            )
        )
    )

    return {
        "race_id": race_id,
        "season": target["season_year"],
        "round": target["round_number"],

        "actual_sequence": list(actual_sequence),
        "predicted_sequence": list(predicted_sequence),

        "actual_stops": actual_stops,
        "predicted_stops": predicted_stops,

        "sequence_match": (
            actual_sequence
            == predicted_sequence
        ),

        "stop_match": (
            min(actual_stops, 3)
            == predicted_stops
        ),

        "stop_probabilities": {
            str(k): round(
                v * 100,
                1,
            )
            for k, v in stop_probs.items()
        },

        "confidence": confidence,

        "historical_races": len(history),
        "analogs_used": len(analogs),

        "top_strategies": [
            {
                "sequence": x["sequence"],
                "score": round(
                    x["score"],
                    4,
                ),
                "races": x["races"],
            }
            for x in ranked[:5]
        ],

        "top_analogs": [
            {
                "race_id": x["race_id"],
                "sequence": x["sequence"],
                "distance": round(
                    x["distance"],
                    3,
                ),
                "weight": round(
                    x["weight"],
                    4,
                ),
            }
            for x in analogs[:5]
        ],
    }


# ============================================================
# TEST
# ============================================================

def main():
    print("Loading race data...")
    (
        races,
        nominations,
        strategies,
        features,
        scales,
    ) = build_model_data()

    targets = [
        rid
        for rid, r in races.items()
        if (
            r["season_year"] in (2022, 2023, 2024, 2025)
            and r["race_date"] is not None
            and rid in strategies
            and r["rainfall"] is False
        )
    ]

    targets.sort(
        key=lambda rid: races[rid]["race_date"]
    )

    results = []

    for i, rid in enumerate(targets, 1):
        try:
            result = validate_race(
                rid,
                races,
                nominations,
                strategies,
                features,
                scales,
            )

            if result is None:
                print(
                    f"[{i}/{len(targets)}] "
                    f"{rid} unavailable"
                )
                continue

            results.append(result)

            print(
                f"[{i}/{len(targets)}] "
                f"{rid} "
                f"{result['season']}-R{result['round']} "
                f"actual={result['actual_stops']} "
                f"pred={result['predicted_stops']} "
                f"match={result['stop_match']} "
                f"seq={'Y' if result['sequence_match'] else 'N'}"
            )

        except Exception as exc:
            print(
                f"[{i}/{len(targets)}] "
                f"{rid} ERROR "
                f"{type(exc).__name__}: {exc}"
            )

    print("\n==============================")
    print("V5 WALK-FORWARD RESULT")
    print("==============================")

    if not results:
        print("No validation results.")
        return

    stop_correct = sum(
        r["stop_match"]
        for r in results
    )

    sequence_correct = sum(
        r["sequence_match"]
        for r in results
    )

    print(
        "validated:",
        len(results),
    )

    print(
        "stop-count:",
        stop_correct,
        "/",
        len(results),
        "=",
        round(
            100 * stop_correct / len(results),
            1,
        ),
        "%",
    )

    print(
        "exact sequence:",
        sequence_correct,
        "/",
        len(results),
        "=",
        round(
            100 * sequence_correct / len(results),
            1,
        ),
        "%",
    )

    # Dominant baseline.
    counts = Counter(
        min(
            r["actual_stops"],
            3,
        )
        for r in results
    )

    baseline_stops = counts.most_common(1)[0][0]

    baseline_correct = sum(
        min(
            r["actual_stops"],
            3,
        ) == baseline_stops
        for r in results
    )

    print(
        "dominant baseline:",
        baseline_stops,
        "stop(s)",
    )

    print(
        "dominant baseline accuracy:",
        baseline_correct,
        "/",
        len(results),
        "=",
        round(
            100 * baseline_correct / len(results),
            1,
        ),
        "%",
    )

    print("\nBY SEASON")

    for season in sorted(
        set(
            r["season"]
            for r in results
        )
    ):
        subset = [
            r
            for r in results
            if r["season"] == season
        ]

        sc = sum(
            r["stop_match"]
            for r in subset
        )

        seq = sum(
            r["sequence_match"]
            for r in subset
        )

        print(
            season,
            "stop",
            f"{sc}/{len(subset)}",
            f"{100*sc/len(subset):.1f}%",
            "| seq",
            f"{seq}/{len(subset)}",
            f"{100*seq/len(subset):.1f}%",
        )

    print("\nFAILURES")

    for r in results:
        if not r["stop_match"]:
            print(
                r["race_id"],
                f"{r['season']}-R{r['round']}",
                "actual=",
                r["actual_stops"],
                "pred=",
                r["predicted_stops"],
                "probs=",
                r["stop_probabilities"],
                "analogs=",
                r["top_analogs"][:3],
            )


if __name__ == "__main__":
    main()
