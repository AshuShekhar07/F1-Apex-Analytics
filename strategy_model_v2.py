import math
import os
from collections import Counter, defaultdict
from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

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

MODEL_FEATURES = [
    "track_id",
    "round_number",
    "season_progress",
    "track_temp",
    "air_temp",
    "humidity",
    "wind_speed",
    "rain_expected",
    "rain_onset_lap",
    "weekend_format",
    "sc_rate",
    "vsc_rate",
    "red_flag_rate",
    "track_avg_stops",
    "track_1stop_rate",
    "track_2stop_rate",
    "track_3plus_rate",
    "track_recent_stops",
    "global_avg_stops",
    "fp2_soft_slope",
    "fp2_medium_slope",
    "fp2_hard_slope",
    "fp2_soft_to_medium",
    "fp2_medium_to_hard",
    "fp2_longrun_coverage",
    "fp2_compound_count",
]

CAT_FEATURES = ["track_id", "weekend_format"]
NUM_FEATURES = [x for x in MODEL_FEATURES if x not in CAT_FEATURES]


def _fetch_all(sql, params=None):
    with engine.connect() as conn:
        return conn.execute(text(sql), params or {}).mappings().all()


def get_race_weather(race_id):
    rows = _fetch_all("""
        SELECT
            sw.air_temp_avg,
            sw.track_temp_avg,
            sw.humidity_avg,
            sw.wind_speed_avg,
            sw.rainfall,
            sw.rain_onset_lap
        FROM races r
        JOIN sessions s
          ON s.race_id = r.id
         AND s.session_type = 'R'
        JOIN session_weather sw
          ON sw.session_id = s.id
        WHERE r.id = :race_id
        LIMIT 1
    """, {"race_id": race_id})
    return rows[0] if rows else None


def get_historical_races(target_date, target_era=None):
    sql = """
        SELECT
            r.id,
            r.track_id,
            r.season_year,
            r.round_number,
            r.race_date,
            r.weekend_format,
            r.regulation_era,
            r.safety_car_periods,
            r.vsc_periods,
            r.red_flags,
            r.winner_time_seconds
        FROM races r
        WHERE r.race_date IS NOT NULL
          AND r.race_date < :target_date
    """
    params = {"target_date": target_date}

    if target_era:
        sql += " AND r.regulation_era = :era"
        params["era"] = target_era

    sql += " ORDER BY r.race_date"
    return _fetch_all(sql, params)


def get_race_stop_count(race_id):
    rows = _fetch_all("""
        SELECT
            rs.race_entry_id,
            rs.finishing_position,
            COUNT(*) AS stints,
            rr.status
        FROM race_stints rs
        JOIN races r
          ON r.id = rs.race_id
        JOIN sessions s
          ON s.race_id = r.id
         AND s.session_type = 'R'
        JOIN race_results rr
          ON rr.race_entry_id = rs.race_entry_id
         AND rr.session_id = s.id
        WHERE rs.race_id = :race_id
          AND rs.finishing_position = 1
          AND rr.status = ANY(:finished)
        GROUP BY
            rs.race_entry_id,
            rs.finishing_position,
            rr.status
        LIMIT 1
    """, {
        "race_id": race_id,
        "finished": list(FINISHED),
    })

    if not rows:
        return None

    return max(0, int(rows[0]["stints"]) - 1)


def get_race_winner_strategy(race_id):
    rows = _fetch_all("""
        SELECT
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
        WHERE rs.race_id = :race_id
          AND rs.finishing_position = 1
          AND rr.status = ANY(:finished)
        ORDER BY rs.stint_number
    """, {
        "race_id": race_id,
        "finished": list(FINISHED),
    })
    return rows


def get_track_history(target_track_id, target_date, era):
    races = get_historical_races(target_date, era)
    races = [r for r in races if int(r["track_id"]) == int(target_track_id)]

    stop_counts = []
    for r in races:
        stops = get_race_stop_count(r["id"])
        if stops is not None:
            stop_counts.append(stops)

    if not stop_counts:
        return {
            "avg": np.nan,
            "one": np.nan,
            "two": np.nan,
            "three": np.nan,
            "recent": np.nan,
        }

    recent = stop_counts[-3:]

    return {
        "avg": float(np.mean(stop_counts)),
        "one": float(sum(x == 1 for x in stop_counts) / len(stop_counts)),
        "two": float(sum(x == 2 for x in stop_counts) / len(stop_counts)),
        "three": float(sum(x >= 3 for x in stop_counts) / len(stop_counts)),
        "recent": float(np.mean(recent)),
    }


def get_global_history(target_date, era):
    races = get_historical_races(target_date, era)

    stops = []
    for r in races:
        s = get_race_stop_count(r["id"])
        if s is not None:
            stops.append(s)

    if not stops:
        return np.nan

    return float(np.mean(stops))


def get_event_rates(target_track_id, target_date):
    rows = _fetch_all("""
        SELECT
            safety_car_periods,
            vsc_periods,
            red_flags
        FROM races
        WHERE track_id = :track_id
          AND race_date IS NOT NULL
          AND race_date < :target_date
    """, {
        "track_id": target_track_id,
        "target_date": target_date,
    })

    if not rows:
        return 0.0, 0.0, 0.0

    n = len(rows)

    sc = sum(
        (int(r["safety_car_periods"] or 0) > 0)
        for r in rows
    ) / n

    vsc = sum(
        (int(r["vsc_periods"] or 0) > 0)
        for r in rows
    ) / n

    red = sum(
        (int(r["red_flags"] or 0) > 0)
        for r in rows
    ) / n

    return sc, vsc, red


def get_fp2_features(race_id):
    rows = _fetch_all("""
        SELECT
            l.race_entry_id,
            l.tire_compound,
            l.lap_number,
            l.lap_time
        FROM sessions s
        JOIN laps l
          ON l.session_id = s.id
        WHERE s.race_id = :race_id
          AND s.session_type = 'FP2'
          AND l.tire_compound IN ('SOFT', 'MEDIUM', 'HARD')
          AND l.lap_time IS NOT NULL
          AND l.is_valid = TRUE
        ORDER BY l.race_entry_id, l.tire_compound, l.lap_number
    """, {"race_id": race_id})

    if not rows:
        return {
            "soft_slope": np.nan,
            "medium_slope": np.nan,
            "hard_slope": np.nan,
            "soft_to_medium": np.nan,
            "medium_to_hard": np.nan,
            "coverage": 0.0,
            "compound_count": 0.0,
        }

    df = pd.DataFrame(rows)

    slopes = {}
    medians = {}
    usable_runs = 0

    for compound, group in df.groupby("tire_compound"):
        run_values = []

        for _, run in group.groupby("race_entry_id"):
            run = run.sort_values("lap_number")

            if len(run) < 6:
                continue

            vals = run["lap_time"].astype(float).to_numpy()

            # Robustly reject obvious outliers caused by pit/in/out laps.
            med = np.median(vals)
            mad = np.median(np.abs(vals - med))

            if mad > 0:
                keep = np.abs(vals - med) <= max(3.0 * mad, 2.0)
                vals = vals[keep]

            if len(vals) < 6:
                continue

            first_n = min(4, len(vals) // 2)
            last_n = min(4, len(vals) // 2)

            first = float(np.median(vals[:first_n]))
            last = float(np.median(vals[-last_n:]))

            slope = (last - first) / max(len(vals) - 1, 1)

            run_values.append(slope)
            usable_runs += 1

        if run_values:
            slopes[compound] = float(np.median(run_values))

        compound_vals = group["lap_time"].astype(float).to_numpy()
        if len(compound_vals) >= 5:
            medians[compound] = float(np.median(compound_vals))

    total_rows = len(df)

    return {
        "soft_slope": slopes.get("SOFT", np.nan),
        "medium_slope": slopes.get("MEDIUM", np.nan),
        "hard_slope": slopes.get("HARD", np.nan),
        "soft_to_medium": (
            medians["SOFT"] - medians["MEDIUM"]
            if "SOFT" in medians and "MEDIUM" in medians
            else np.nan
        ),
        "medium_to_hard": (
            medians["MEDIUM"] - medians["HARD"]
            if "MEDIUM" in medians and "HARD" in medians
            else np.nan
        ),
        "coverage": float(usable_runs / max(total_rows, 1)),
        "compound_count": float(len(medians)),
    }


def get_weather_for_target(race_id=None, target_conditions=None):
    if target_conditions is not None:
        return target_conditions

    w = get_race_weather(race_id)

    if not w:
        return {
            "track_temp": np.nan,
            "air_temp": np.nan,
            "humidity": np.nan,
            "wind_speed": np.nan,
            "rain_expected": False,
            "rain_onset_lap": np.nan,
        }

    return {
        "track_temp": float(w["track_temp_avg"]) if w["track_temp_avg"] is not None else np.nan,
        "air_temp": float(w["air_temp_avg"]) if w["air_temp_avg"] is not None else np.nan,
        "humidity": float(w["humidity_avg"]) if w["humidity_avg"] is not None else np.nan,
        "wind_speed": float(w["wind_speed_avg"]) if w["wind_speed_avg"] is not None else np.nan,
        "rain_expected": bool(w["rainfall"]),
        "rain_onset_lap": (
            float(w["rain_onset_lap"])
            if w["rain_onset_lap"] is not None
            else np.nan
        ),
    }


def build_features_for_race(race, target_conditions=None, include_fp2=True):
    target_date = race["race_date"]
    track_id = race["track_id"]
    era = race["regulation_era"]

    weather = get_weather_for_target(
        race_id=race["id"],
        target_conditions=target_conditions,
    )

    sc_rate, vsc_rate, red_rate = get_event_rates(
        track_id,
        target_date,
    )

    th = get_track_history(
        track_id,
        target_date,
        era,
    )

    global_avg = get_global_history(
        target_date,
        era,
    )

    fp2 = (
        get_fp2_features(race["id"])
        if include_fp2 and race.get("id") is not None
        else {
            "soft_slope": np.nan,
            "medium_slope": np.nan,
            "hard_slope": np.nan,
            "soft_to_medium": np.nan,
            "medium_to_hard": np.nan,
            "coverage": 0.0,
            "compound_count": 0.0,
        }
    )

    return {
        "track_id": str(track_id),
        "round_number": float(race["round_number"]),
        "season_progress": float(race["round_number"]) / 24.0,
        "track_temp": weather["track_temp"],
        "air_temp": weather["air_temp"],
        "humidity": weather["humidity"],
        "wind_speed": weather["wind_speed"],
        "rain_expected": int(weather["rain_expected"]),
        "rain_onset_lap": weather["rain_onset_lap"],
        "weekend_format": str(race["weekend_format"]),
        "sc_rate": sc_rate,
        "vsc_rate": vsc_rate,
        "red_flag_rate": red_rate,
        "track_avg_stops": th["avg"],
        "track_1stop_rate": th["one"],
        "track_2stop_rate": th["two"],
        "track_3plus_rate": th["three"],
        "track_recent_stops": th["recent"],
        "global_avg_stops": global_avg,
        "fp2_soft_slope": fp2["soft_slope"],
        "fp2_medium_slope": fp2["medium_slope"],
        "fp2_hard_slope": fp2["hard_slope"],
        "fp2_soft_to_medium": fp2["soft_to_medium"],
        "fp2_medium_to_hard": fp2["medium_to_hard"],
        "fp2_longrun_coverage": fp2["coverage"],
        "fp2_compound_count": fp2["compound_count"],
    }


def build_training_set(target_date, target_era, min_history=10):
    races = get_historical_races(target_date, target_era)

    rows = []

    for race in races:
        winner_stops = get_race_stop_count(race["id"])

        if winner_stops not in (1, 2, 3, 4):
            continue

        # Do not create a training row until there is enough prior history.
        prior_count = sum(
            1 for r in races
            if r["race_date"] < race["race_date"]
        )

        if prior_count < min_history:
            continue

        feats = build_features_for_race(
            race,
            target_conditions=None,
            include_fp2=True,
        )

        feats["target"] = min(int(winner_stops), 3)
        feats["race_id"] = race["id"]
        feats["race_date"] = race["race_date"]
        rows.append(feats)

    return pd.DataFrame(rows)


def _make_pipeline():
    preprocessor = ColumnTransformer(
        transformers=[
            (
                "cat",
                Pipeline([
                    ("imputer", SimpleImputer(strategy="most_frequent")),
                    ("onehot", OneHotEncoder(handle_unknown="ignore")),
                ]),
                CAT_FEATURES,
            ),
            (
                "num",
                Pipeline([
                    ("imputer", SimpleImputer(strategy="median")),
                ]),
                NUM_FEATURES,
            ),
        ],
        remainder="drop",
    )

    model = HistGradientBoostingClassifier(
        learning_rate=0.055,
        max_iter=180,
        max_leaf_nodes=7,
        min_samples_leaf=5,
        l2_regularization=1.5,
        random_state=42,
    )

    return Pipeline([
        ("prep", preprocessor),
        ("model", model),
    ])


def train_for_prediction(target_race, target_conditions=None, era_override=None):
    target_era = era_override or target_race["regulation_era"]

    training = build_training_set(
        target_race["race_date"],
        target_era,
        min_history=8,
    )

    if len(training) < 15:
        return None, {
            "status": "insufficient_history",
            "training_races": len(training),
            "era": target_era,
        }

    X = training[MODEL_FEATURES].copy()
    y = training["target"].astype(int)

    model = _make_pipeline()
    model.fit(X, y)

    target_x = pd.DataFrame([
        build_features_for_race(
            target_race,
            target_conditions=target_conditions,
            include_fp2=True,
        )
    ])[MODEL_FEATURES]

    probabilities = model.predict_proba(target_x)[0]
    classes = model.named_steps["model"].classes_

    probs = {
        int(cls): float(prob)
        for cls, prob in zip(classes, probabilities)
    }

    for cls in (1, 2, 3):
        probs.setdefault(cls, 0.0)

    predicted = max(probs, key=probs.get)

    return {
        "predicted_stops": int(predicted),
        "probabilities": probs,
        "training_races": len(training),
        "training_seasons": sorted(
            set(int(x) for x in training["race_date"].apply(lambda x: x.year))
        ),
        "era": target_era,
        "features": target_x.iloc[0].to_dict(),
    }, training


def get_candidate_strategies(
    track_id,
    target_date,
    era,
    predicted_stops,
    nominations=None,
    top_n=12,
):
    races = get_historical_races(target_date, era)

    candidates = []

    for race in races:
        if int(race["track_id"]) != int(track_id):
            continue

        strategy = get_race_winner_strategy(race["id"])

        if len(strategy) - 1 != predicted_stops:
            continue

        seq = tuple(str(x["compound"]) for x in strategy)

        if nominations:
            # Convert only when nomination data exists.
            if not all(x in nominations for x in seq):
                continue

        candidates.append({
            "race_id": race["id"],
            "race_date": race["race_date"],
            "season": race["season_year"],
            "sequence": seq,
            "stints": strategy,
        })

    counts = Counter(x["sequence"] for x in candidates)

    ranked = []
    for seq, count in counts.most_common():
        examples = [
            x for x in candidates
            if x["sequence"] == seq
        ]

        # Recent examples get a mild bonus, but never dominate frequency.
        ages = [
            max(0, (target_date - x["race_date"]).days / 365.25)
            for x in examples
        ]

        recency = float(
            np.mean([
                0.5 ** (age / 4.0)
                for age in ages
            ])
        )

        score = count * (0.75 + 0.25 * recency)

        ranked.append({
            "sequence": list(seq),
            "historical_races": count,
            "score": round(score, 4),
            "examples": [
                {
                    "race_id": x["race_id"],
                    "season": x["season"],
                }
                for x in examples[:3]
            ],
        })

    ranked.sort(key=lambda x: x["score"], reverse=True)
    return ranked[:top_n]


def get_stint_plan(sequence, track_id, target_date, era):
    races = get_historical_races(target_date, era)

    instances = []

    for race in races:
        if int(race["track_id"]) != int(track_id):
            continue

        strategy = get_race_winner_strategy(race["id"])

        if tuple(str(x["compound"]) for x in strategy) != tuple(sequence):
            continue

        instances.append((race, strategy))

    if not instances:
        return []

    plan = []

    for i, compound in enumerate(sequence):
        starts = []
        ends = []

        for race, strategy in instances:
            if i >= len(strategy):
                continue

            starts.append(int(strategy[i]["start_lap"]))
            ends.append(int(strategy[i]["end_lap"]))

        if not starts:
            continue

        plan.append({
            "compound": compound,
            "avg_start_lap": int(round(np.median(starts))),
            "avg_end_lap": int(round(np.median(ends))),
        })

    return plan


def predict_strategy(
    race,
    target_conditions=None,
    nominations=None,
    allow_cross_era=True,
):
    result, training = train_for_prediction(
        race,
        target_conditions=target_conditions,
    )

    era_fallback = False

    if result is None and allow_cross_era:
        result, training = train_for_prediction(
            race,
            target_conditions=target_conditions,
            era_override=None,
        )

        if result is not None:
            era_fallback = result["era"] != race["regulation_era"]

    if result is None:
        return {
            "status": "insufficient_history",
            "message": "Not enough leakage-free historical races to train the strategy model.",
        }

    predicted_stops = result["predicted_stops"]

    candidates = get_candidate_strategies(
        race["track_id"],
        race["race_date"],
        result["era"],
        predicted_stops,
        nominations=nominations,
    )

    # If exact nomination filtering leaves nothing, retain historical strategy
    # evidence instead of fabricating a strategy.
    if not candidates and nominations:
        candidates = get_candidate_strategies(
            race["track_id"],
            race["race_date"],
            result["era"],
            predicted_stops,
            nominations=None,
        )

    if not candidates:
        return {
            "status": "model_ok_no_strategy_history",
            "predicted_stops": predicted_stops,
            "stop_probabilities": result["probabilities"],
            "training_races": result["training_races"],
            "training_seasons": result["training_seasons"],
            "regulation_era": result["era"],
            "era_fallback_used": era_fallback,
        }

    primary = candidates[0]

    plan = get_stint_plan(
        primary["sequence"],
        race["track_id"],
        race["race_date"],
        result["era"],
    )

    p = result["probabilities"]
    ordered = sorted(p.items(), key=lambda x: x[1], reverse=True)
    margin = ordered[0][1] - ordered[1][1]

    confidence = (
        45
        + 40 * ordered[0][1]
        + 20 * margin
    )

    if result["training_races"] < 25:
        confidence -= 10
    elif result["training_races"] < 40:
        confidence -= 5

    if era_fallback:
        confidence -= 15

    confidence = max(20, min(90, round(confidence)))

    return {
        "status": "ok",
        "strategy": plan,
        "predicted_stops": predicted_stops,
        "stop_probabilities": {
            str(k): round(v * 100, 1)
            for k, v in p.items()
        },
        "strategy_alternatives": candidates[1:5],
        "training_races": result["training_races"],
        "training_seasons": result["training_seasons"],
        "regulation_era": result["era"],
        "era_fallback_used": era_fallback,
        "confidence_pct": confidence,
        "selection_method": (
            "Walk-forward race-level model predicts stop count from weather, "
            "track history, incident history, weekend format and FP2 long-run "
            "degradation; historical winner strategies are then ranked within "
            "the predicted stop-count class."
        ),
    }


def validate_race(race_id):
    race_rows = _fetch_all("""
        SELECT
            id,
            track_id,
            season_year,
            round_number,
            race_date,
            weekend_format,
            regulation_era
        FROM races
        WHERE id = :id
    """, {"id": race_id})

    if not race_rows:
        return {"status": "not_found", "race_id": race_id}

    race = race_rows[0]

    weather = get_weather_for_target(race_id=race_id)

    prediction = predict_strategy(
        dict(race),
        target_conditions=weather,
        nominations=None,
        allow_cross_era=False,
    )

    actual_stops = get_race_stop_count(race_id)
    actual_strategy = get_race_winner_strategy(race_id)

    if prediction.get("status") not in ("ok", "model_ok_no_strategy_history"):
        return {
            "status": "validation_unavailable",
            "race_id": race_id,
            "actual_stops": actual_stops,
            "prediction": prediction,
        }

    predicted_stops = prediction["predicted_stops"]

    return {
        "status": "ok",
        "race_id": race_id,
        "season": race["season_year"],
        "round": race["round_number"],
        "actual_stops": actual_stops,
        "predicted_stops": predicted_stops,
        "stop_match": (
            predicted_stops == min(actual_stops, 3)
            if actual_stops is not None
            else False
        ),
        "actual_sequence": [
            str(x["compound"]) for x in actual_strategy
        ],
        "predicted_sequence": [
            x["compound"] for x in prediction.get("strategy", [])
        ],
        "confidence_pct": prediction.get("confidence_pct"),
        "probabilities": prediction.get("stop_probabilities"),
        "training_races": prediction.get("training_races"),
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--race_id", type=int)
    parser.add_argument("--track_id", type=int)
    parser.add_argument("--race_date")
    parser.add_argument("--era")
    args = parser.parse_args()

    if args.race_id:
        print(validate_race(args.race_id))
        raise SystemExit

    if not args.track_id or not args.race_date:
        raise SystemExit(
            "Use --race_id for validation, or --track_id + --race_date for prediction."
        )

    rows = _fetch_all("""
        SELECT
            id,
            track_id,
            season_year,
            round_number,
            race_date,
            weekend_format,
            regulation_era
        FROM races
        WHERE track_id = :track_id
          AND race_date = :race_date
        ORDER BY id DESC
        LIMIT 1
    """, {
        "track_id": args.track_id,
        "race_date": date.fromisoformat(args.race_date),
    })

    if not rows:
        raise SystemExit("No race found for that track/date.")

    print(
        predict_strategy(
            dict(rows[0]),
            target_conditions=None,
            nominations=None,
        )
    )
