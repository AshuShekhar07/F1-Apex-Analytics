import os
from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

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

FEATURES = [
    "track_temp",
    "air_temp",
    "humidity",
    "wind_speed",
    "rain",
    "rain_onset",
    "round_progress",
    "track_avg_stops",
    "track_1stop_rate",
    "track_2stop_rate",
    "track_3plus_rate",
    "track_recent_stops",
    "global_avg_stops",
    "global_2plus_rate",
    "sc_rate",
    "vsc_rate",
    "red_rate",
    "soft_nomination",
    "medium_nomination",
    "hard_nomination",
    "three_compound_nomination",
    "fp2_soft_slope",
    "fp2_medium_slope",
    "fp2_hard_slope",
    "fp2_soft_vs_medium",
    "fp2_medium_vs_hard",
]


def query(sql, params=None):
    with engine.connect() as conn:
        return conn.execute(
            text(sql),
            params or {}
        ).mappings().all()


def race_info(race_id):
    rows = query("""
        SELECT
            r.id,
            r.track_id,
            r.season_year,
            r.round_number,
            r.race_date,
            r.weekend_format,
            r.regulation_era
        FROM races r
        WHERE r.id = :id
    """, {"id": race_id})

    return rows[0] if rows else None


def weather(race_id):
    rows = query("""
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
        WHERE r.id = :id
        LIMIT 1
    """, {"id": race_id})

    if not rows:
        return {}

    return rows[0]


def winner_stops(race_id):
    rows = query("""
        SELECT COUNT(*) AS stints
        FROM race_stints rs
        JOIN races r
          ON r.id = rs.race_id
        JOIN sessions s
          ON s.race_id = r.id
         AND s.session_type = 'R'
        JOIN race_results rr
          ON rr.race_entry_id = rs.race_entry_id
         AND rr.session_id = s.id
        WHERE rs.race_id = :id
          AND rs.finishing_position = 1
          AND rr.status = ANY(:finished)
    """, {
        "id": race_id,
        "finished": list(FINISHED),
    })

    if not rows or rows[0]["stints"] is None:
        return None

    return max(0, int(rows[0]["stints"]) - 1)


def historical_races(target_date, era):
    return query("""
        SELECT
            id,
            track_id,
            season_year,
            round_number,
            race_date,
            regulation_era
        FROM races
        WHERE race_date IS NOT NULL
          AND race_date < :target_date
          AND regulation_era = :era
        ORDER BY race_date
    """, {
        "target_date": target_date,
        "era": era,
    })


def track_history(track_id, target_date, era):
    rows = historical_races(target_date, era)

    counts = []

    for r in rows:
        if int(r["track_id"]) != int(track_id):
            continue

        stops = winner_stops(r["id"])

        if stops is not None:
            counts.append(stops)

    if not counts:
        return {
            "avg": np.nan,
            "one": np.nan,
            "two": np.nan,
            "three": np.nan,
            "recent": np.nan,
            "two_plus": np.nan,
        }

    recent = counts[-4:]

    return {
        "avg": float(np.mean(counts)),
        "one": float(np.mean(np.array(counts) == 1)),
        "two": float(np.mean(np.array(counts) == 2)),
        "three": float(np.mean(np.array(counts) >= 3)),
        "recent": float(np.mean(recent)),
        "two_plus": float(np.mean(np.array(counts) >= 2)),
    }


def global_history(target_date, era):
    races = historical_races(target_date, era)

    counts = []

    for r in races:
        stops = winner_stops(r["id"])

        if stops is not None:
            counts.append(stops)

    if not counts:
        return np.nan, np.nan

    return (
        float(np.mean(counts)),
        float(np.mean(np.array(counts) >= 2)),
    )


def incident_rates(track_id, target_date):
    rows = query("""
        SELECT
            safety_car_periods,
            vsc_periods,
            red_flags
        FROM races
        WHERE track_id = :track_id
          AND race_date IS NOT NULL
          AND race_date < :target_date
    """, {
        "track_id": track_id,
        "target_date": target_date,
    })

    if not rows:
        return 0.0, 0.0, 0.0

    n = len(rows)

    return (
        sum(int(x["safety_car_periods"] or 0) > 0 for x in rows) / n,
        sum(int(x["vsc_periods"] or 0) > 0 for x in rows) / n,
        sum(int(x["red_flags"] or 0) > 0 for x in rows) / n,
    )


def nominations(race_id):
    rows = query("""
        SELECT label
        FROM race_compound_nominations
        WHERE race_id = :id
    """, {"id": race_id})

    labels = {str(x["label"]).upper() for x in rows}

    return {
        "soft": int("SOFT" in labels),
        "medium": int("MEDIUM" in labels),
        "hard": int("HARD" in labels),
        "three": int(
            "SOFT" in labels and
            "MEDIUM" in labels and
            "HARD" in labels
        ),
    }


def fp2_features(race_id):
    rows = query("""
        SELECT
            l.race_entry_id,
            l.tire_compound,
            l.lap_number,
            l.lap_time
        FROM sessions s
        JOIN laps l
          ON l.session_id = s.id
        WHERE s.race_id = :id
          AND s.session_type = 'FP2'
          AND l.tire_compound IN ('SOFT', 'MEDIUM', 'HARD')
          AND l.lap_time IS NOT NULL
          AND l.is_valid = TRUE
        ORDER BY
            l.race_entry_id,
            l.tire_compound,
            l.lap_number
    """, {"id": race_id})

    result = {
        "soft": np.nan,
        "medium": np.nan,
        "hard": np.nan,
    }

    if not rows:
        return result

    df = pd.DataFrame(rows)

    for compound, group in df.groupby("tire_compound"):

        slopes = []

        for _, run in group.groupby("race_entry_id"):
            run = run.sort_values("lap_number")

            vals = run["lap_time"].astype(float).to_numpy()

            if len(vals) < 8:
                continue

            # Remove extreme outliers.
            med = np.median(vals)
            mad = np.median(np.abs(vals - med))

            if mad > 0:
                vals = vals[
                    np.abs(vals - med) <= max(3 * mad, 2.0)
                ]

            if len(vals) < 8:
                continue

            # Compare the first/last four laps.
            first = np.median(vals[:4])
            last = np.median(vals[-4:])

            slope = (last - first) / max(len(vals) - 1, 1)

            # Ignore obviously pathological runs.
            if abs(slope) < 0.20:
                slopes.append(float(slope))

        if slopes:
            result[compound.lower()] = float(
                np.median(slopes)
            )

    return result


def build_features(race, include_fp2=True):
    rid = race["id"]
    target_date = race["race_date"]
    era = race["regulation_era"]

    w = weather(rid)

    th = track_history(
        race["track_id"],
        target_date,
        era,
    )

    global_avg, global_two_plus = global_history(
        target_date,
        era,
    )

    sc, vsc, red = incident_rates(
        race["track_id"],
        target_date,
    )

    nom = nominations(rid)

    fp = (
        fp2_features(rid)
        if include_fp2
        else {
            "soft": np.nan,
            "medium": np.nan,
            "hard": np.nan,
        }
    )

    return {
        "track_temp": float(w["track_temp_avg"])
        if w.get("track_temp_avg") is not None else np.nan,

        "air_temp": float(w["air_temp_avg"])
        if w.get("air_temp_avg") is not None else np.nan,

        "humidity": float(w["humidity_avg"])
        if w.get("humidity_avg") is not None else np.nan,

        "wind_speed": float(w["wind_speed_avg"])
        if w.get("wind_speed_avg") is not None else np.nan,

        "rain": int(bool(w.get("rainfall"))),

        "rain_onset": float(w["rain_onset_lap"])
        if w.get("rain_onset_lap") is not None else np.nan,

        "round_progress": float(race["round_number"]) / 24.0,

        "track_avg_stops": th["avg"],
        "track_1stop_rate": th["one"],
        "track_2stop_rate": th["two"],
        "track_3plus_rate": th["three"],
        "track_recent_stops": th["recent"],
        "track_two_plus_rate": th["two_plus"],

        "global_avg_stops": global_avg,
        "global_2plus_rate": global_two_plus,

        "sc_rate": sc,
        "vsc_rate": vsc,
        "red_rate": red,

        "soft_nomination": nom["soft"],
        "medium_nomination": nom["medium"],
        "hard_nomination": nom["hard"],
        "three_compound_nomination": nom["three"],

        "fp2_soft_slope": fp["soft"],
        "fp2_medium_slope": fp["medium"],
        "fp2_hard_slope": fp["hard"],

        "fp2_soft_vs_medium": (
            fp["soft"] - fp["medium"]
            if not np.isnan(fp["soft"])
            and not np.isnan(fp["medium"])
            else np.nan
        ),

        "fp2_medium_vs_hard": (
            fp["medium"] - fp["hard"]
            if not np.isnan(fp["medium"])
            and not np.isnan(fp["hard"])
            else np.nan
        ),
    }


def make_model():
    return Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scale", StandardScaler()),
        (
            "model",
            LogisticRegression(
                C=0.35,
                max_iter=3000,
                class_weight="balanced",
                random_state=42,
            ),
        ),
    ])


def train(target_race):
    era = target_race["regulation_era"]
    target_date = target_race["race_date"]

    races = historical_races(
        target_date,
        era,
    )

    rows = []

    for race in races:

        stops = winner_stops(race["id"])

        if stops is None:
            continue

        x = build_features(
            race,
            include_fp2=True,
        )

        x["race_id"] = race["id"]
        x["race_date"] = race["race_date"]

        # Stage 1.
        x["target_two_plus"] = int(stops >= 2)

        # Stage 2 only applies to 2+ stop races.
        x["target_three_plus"] = int(stops >= 3)

        rows.append(x)

    df = pd.DataFrame(rows)

    if len(df) < 20:
        return None

    x = df[FEATURES]

    # -----------------------------
    # STAGE 1
    # -----------------------------
    y1 = df["target_two_plus"]

    model1 = make_model()
    model1.fit(x, y1)

    # -----------------------------
    # STAGE 2
    # -----------------------------
    stage2_df = df[df["target_two_plus"] == 1].copy()

    model2 = None

    if (
        len(stage2_df) >= 10 and
        stage2_df["target_three_plus"].nunique() == 2
    ):
        model2 = make_model()
        model2.fit(
            stage2_df[FEATURES],
            stage2_df["target_three_plus"],
        )

    return {
        "stage1": model1,
        "stage2": model2,
        "training_races": len(df),
    }


def predict(race):
    trained = train(race)

    if trained is None:
        return {
            "status": "insufficient_history"
        }

    x = pd.DataFrame([
        build_features(
            race,
            include_fp2=True,
        )
    ])[FEATURES]

    model1 = trained["stage1"]

    p_two_plus = float(
        model1.predict_proba(x)[0][1]
    )

    # Bayesian-style shrinkage toward the historical track rate.
    th = track_history(
        race["track_id"],
        race["race_date"],
        race["regulation_era"],
    )

    if not np.isnan(th["two_plus"]):
        p_two_plus = (
            0.70 * p_two_plus +
            0.30 * th["two_plus"]
        )

    # Stage 2.
    if (
        trained["stage2"] is not None and
        p_two_plus >= 0.50
    ):
        p_three_plus_conditional = float(
            trained["stage2"].predict_proba(x)[0][1]
        )
    else:
        p_three_plus_conditional = 0.0

    p_three = (
        p_two_plus *
        p_three_plus_conditional
    )

    p_two = max(
        0.0,
        p_two_plus - p_three
    )

    p_one = max(
        0.0,
        1.0 - p_two_plus
    )

    probabilities = {
        1: p_one,
        2: p_two,
        3: p_three,
    }

    predicted = max(
        probabilities,
        key=probabilities.get,
    )

    ranked = sorted(
        probabilities.items(),
        key=lambda x: x[1],
        reverse=True,
    )

    margin = ranked[0][1] - ranked[1][1]

    # Confidence deliberately capped.
    confidence = (
        50
        + 35 * ranked[0][1]
        + 25 * margin
    )

    if trained["training_races"] < 35:
        confidence -= 8

    confidence = int(
        max(
            30,
            min(88, round(confidence))
        )
    )

    return {
        "status": "ok",
        "predicted_stops": predicted,
        "probabilities": {
            str(k): round(v * 100, 1)
            for k, v in probabilities.items()
        },
        "training_races": trained["training_races"],
        "confidence_pct": confidence,
    }


def validate(race_id):
    race = race_info(race_id)

    if race is None:
        return {
            "status": "not_found"
        }

    actual = winner_stops(race_id)

    prediction = predict(race)

    if prediction["status"] != "ok":
        return prediction

    return {
        "status": "ok",
        "race_id": race_id,
        "season": race["season_year"],
        "round": race["round_number"],
        "actual_stops": actual,
        "predicted_stops": prediction["predicted_stops"],
        "match": (
            prediction["predicted_stops"]
            == min(actual, 3)
        ),
        "probabilities": prediction["probabilities"],
        "confidence": prediction["confidence_pct"],
    }


def main():
    races = query("""
        SELECT
            r.id,
            r.season_year,
            r.round_number,
            r.race_date
        FROM races r
        WHERE r.season_year BETWEEN 2022 AND 2025
          AND r.race_date IS NOT NULL
          AND r.regulation_era =
              'era2_18inch_groundeffect'
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
    """)

    results = []

    for i, race in enumerate(races, 1):
        try:
            r = validate(race["id"])

            if r["status"] == "ok":
                results.append(r)

                print(
                    f"[{i}/{len(races)}] "
                    f"{r['race_id']} "
                    f"{r['season']}-R{r['round']} "
                    f"actual={r['actual_stops']} "
                    f"pred={r['predicted_stops']} "
                    f"match={r['match']} "
                    f"p={r['probabilities']}"
                )
            else:
                print(
                    f"[{i}/{len(races)}] "
                    f"{race['id']} unavailable"
                )

        except Exception as e:
            print(
                f"[{i}/{len(races)}] "
                f"{race['id']} ERROR {e}"
            )

    if not results:
        raise SystemExit(
            "No validation results."
        )

    matches = sum(
        x["match"]
        for x in results
    )

    print("\n===== V3 WALK-FORWARD =====")
    print("validated:", len(results))
    print(
        "matches:",
        matches,
        "/",
        len(results),
    )
    print(
        "accuracy:",
        round(
            100 * matches / len(results),
            1,
        ),
        "%"
    )

    print("\nBY SEASON")

    for season in sorted(
        set(x["season"] for x in results)
    ):
        subset = [
            x for x in results
            if x["season"] == season
        ]

        m = sum(x["match"] for x in subset)

        print(
            season,
            f"{m}/{len(subset)}",
            f"{100*m/len(subset):.1f}%"
        )

    print("\nFAILURES")

    for x in results:
        if not x["match"]:
            print(
                x["race_id"],
                f"{x['season']}-R{x['round']}",
                "actual=",
                x["actual_stops"],
                "pred=",
                x["predicted_stops"],
                "p=",
                x["probabilities"],
            )


if __name__ == "__main__":
    main()
