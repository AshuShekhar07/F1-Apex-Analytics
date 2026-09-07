import os
import warnings

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from strategy_model_v3 import (
    FEATURES,
    build_features,
    historical_races,
    race_info,
    winner_stops,
)

warnings.filterwarnings("ignore")

engine = create_engine(os.environ["DATABASE_URL"])

# Deliberately REMOVE FP2/noisy compound features from the stop-count model.
# Strategy selection can use FP2 later; this model only decides 1 vs 2+ stops.
STOP_FEATURES = [
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
    "track_two_plus_rate",

    "global_avg_stops",
    "global_2plus_rate",

    "sc_rate",
    "vsc_rate",
    "red_rate",

    "soft_nomination",
    "medium_nomination",
    "hard_nomination",
    "three_compound_nomination",
]


def make_model():
    return Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scale", StandardScaler()),
        (
            "model",
            LogisticRegression(
                C=0.20,
                max_iter=3000,
                # IMPORTANT:
                # no class balancing.
                # The real-world prior is heavily 1-stop.
                class_weight=None,
                random_state=42,
            ),
        ),
    ])


def train_model(target_race):
    races = historical_races(
        target_race["race_date"],
        target_race["regulation_era"],
    )

    rows = []

    for race in races:
        stops = winner_stops(race["id"])

        if stops is None:
            continue

        x = build_features(
            race,
            include_fp2=False,
        )

        x["target"] = int(stops >= 2)
        rows.append(x)

    df = pd.DataFrame(rows)

    if len(df) < 15:
        return None

    if df["target"].nunique() < 2:
        return None

    model = make_model()

    model.fit(
        df[STOP_FEATURES],
        df["target"],
    )

    return model, len(df)


def predict_probability(race):
    trained = train_model(race)

    if trained is None:
        return None

    model, n = trained

    x = pd.DataFrame([
        build_features(
            race,
            include_fp2=False,
        )
    ])[STOP_FEATURES]

    p = float(
        model.predict_proba(x)[0][1]
    )

    return {
        "p_two_plus": p,
        "training_races": n,
    }


def validate(race_id):
    race = race_info(race_id)

    if race is None:
        return None

    actual = winner_stops(race_id)

    if actual is None:
        return None

    result = predict_probability(race)

    if result is None:
        return None

    return {
        "race_id": race_id,
        "season": race["season_year"],
        "round": race["round_number"],
        "actual_stops": actual,
        "actual_two_plus": int(actual >= 2),
        "p_two_plus": result["p_two_plus"],
        "training_races": result["training_races"],
    }


def evaluate_thresholds(results):
    print("\n===== THRESHOLD TEST =====")

    # These are diagnostics, NOT selected automatically.
    thresholds = [
        0.30,
        0.35,
        0.40,
        0.45,
        0.50,
        0.55,
        0.60,
        0.65,
    ]

    for threshold in thresholds:
        correct = 0

        for r in results:
            pred = int(
                r["p_two_plus"] >= threshold
            )

            actual = r["actual_two_plus"]

            if pred == actual:
                correct += 1

        acc = 100 * correct / len(results)

        print(
            f"threshold={threshold:.2f} "
            f"accuracy={acc:.1f}% "
            f"correct={correct}/{len(results)}"
        )


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

            if r is None:
                print(
                    f"[{i}/{len(races)}] "
                    f"{race['id']} unavailable"
                )
                continue

            results.append(r)

            default_prediction = (
                2 if r["p_two_plus"] >= 0.50
                else 1
            )

            print(
                f"[{i}/{len(races)}] "
                f"{r['race_id']} "
                f"{r['season']}-R{r['round']} "
                f"actual={r['actual_stops']} "
                f"p(2+)={r['p_two_plus']:.3f} "
                f"pred={default_prediction}"
            )

        except Exception as e:
            print(
                f"[{i}/{len(races)}] "
                f"{race['id']} ERROR {e}"
            )

    print("\n===== V4 WALK-FORWARD =====")
    print("validated:", len(results))

    if not results:
        return

    # 82.1%-style naive baseline on this exact sample.
    baseline_matches = sum(
        r["actual_two_plus"] == 0
        for r in results
    )

    baseline_acc = (
        100 * baseline_matches / len(results)
    )

    model_matches = sum(
        (
            r["p_two_plus"] >= 0.50
        ) == (
            r["actual_two_plus"] == 1
        )
        for r in results
    )

    model_acc = (
        100 * model_matches / len(results)
    )

    print(
        f"always-1-stop baseline: "
        f"{baseline_matches}/{len(results)} "
        f"= {baseline_acc:.1f}%"
    )

    print(
        f"V4 threshold 0.50: "
        f"{model_matches}/{len(results)} "
        f"= {model_acc:.1f}%"
    )

    evaluate_thresholds(results)

    print("\n===== 2+ STOP SIGNALS =====")

    actual_two = [
        r for r in results
        if r["actual_two_plus"] == 1
    ]

    actual_one = [
        r for r in results
        if r["actual_two_plus"] == 0
    ]

    if actual_two:
        print(
            "actual 2+ races:",
            len(actual_two),
            "mean predicted probability:",
            round(
                np.mean(
                    [r["p_two_plus"] for r in actual_two]
                ),
                3,
            ),
        )

    if actual_one:
        print(
            "actual 1-stop races:",
            len(actual_one),
            "mean predicted probability:",
            round(
                np.mean(
                    [r["p_two_plus"] for r in actual_one]
                ),
                3,
            ),
        )

    print("\n===== TOP 2+ PROBABILITY RACES =====")

    for r in sorted(
        results,
        key=lambda x: x["p_two_plus"],
        reverse=True,
    )[:15]:
        print(
            r["race_id"],
            f"{r['season']}-R{r['round']}",
            "actual=",
            r["actual_stops"],
            "p2+=",
            round(r["p_two_plus"], 3),
        )


def query(sql, params=None):
    with engine.connect() as conn:
        return conn.execute(
            text(sql),
            params or {},
        ).mappings().all()


if __name__ == "__main__":
    main()
