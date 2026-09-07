import os
from datetime import date

from sqlalchemy import create_engine, text

from fetch_race_forecast import predict_race_conditions
from strategy_model_v5 import (
    ERA,
    build_fp2_features,
    candidate_strategies,
    load_fp2,
    load_nominations,
    load_races,
    load_winner_strategies,
    race_features,
    robust_scale,
    FP2_NUMERIC,
)

engine = create_engine(os.environ["DATABASE_URL"])


def find_target_race(track_id, race_date):
    with engine.connect() as conn:
        row = conn.execute(text("""
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

            WHERE r.track_id = :track_id
              AND r.race_date = :race_date
            ORDER BY r.id DESC
            LIMIT 1
        """), {
            "track_id": track_id,
            "race_date": race_date,
        }).mappings().first()

    return dict(row) if row else None


def get_compound_nominations(track_id, race_date):
    target = find_target_race(
        track_id,
        race_date,
    )

    if not target:
        return {}

    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT
                label,
                c_compound
            FROM race_compound_nominations
            WHERE race_id = :race_id
        """), {
            "race_id": target["id"],
        }).mappings().all()

    return {
        str(x["label"]).upper():
        x["c_compound"]
        for x in rows
    }


def make_target_features(
    target,
    forecast,
    nominations,
    fp2,
):
    weather = {
        "track_temp": forecast.get(
            "target_track_temp"
        ),
        "air_temp": forecast.get(
            "target_air_temp"
        ),
        "humidity": forecast.get(
            "target_humidity"
        ),
        "wind_speed": forecast.get(
            "target_wind_speed"
        ),
        "rain_expected": forecast.get(
            "rain_expected"
        ),
        "rain_onset_lap": forecast.get(
            "rain_onset_lap"
        ),
    }

    # race_features knows how to use forecast values first
    # and DB values only as fallback.
    return race_features(
        target,
        nominations,
        fp2,
        weather_override=weather,
    )


def confidence_from_ranking(
    ranked,
    analog_count,
    condition_tier,
):
    if not ranked:
        return 20

    scores = [
        max(0.0, float(x["score"]))
        for x in ranked
    ]

    total = sum(scores)

    if total <= 0:
        return 20

    primary_share = scores[0] / total

    second_share = (
        scores[1] / total
        if len(scores) > 1
        else 0.0
    )

    margin = max(
        0.0,
        primary_share - second_share,
    )

    confidence = (
        35
        + 35 * primary_share
        + 25 * margin
    )

    if analog_count < 5:
        confidence -= 10
    elif analog_count < 10:
        confidence -= 5

    if condition_tier == "climatology":
        confidence = min(
            confidence,
            45,
        )
    elif condition_tier == "forecast":
        confidence = min(
            confidence,
            75,
        )

    return max(
        20,
        min(
            82,
            round(confidence),
        )
    )


def predict(
    track_id,
    race_date,
):
    target = find_target_race(
        track_id,
        race_date,
    )

    if target is None:
        return {
            "status": "not_found",
            "message": (
                "No race exists in the database "
                f"for track {track_id} on {race_date}."
            ),
        }

    if target["regulation_era"] != ERA:
        return {
            "status": "unsupported_era",
            "message": (
                "V5 production strategy model is "
                f"restricted to {ERA}."
            ),
        }

    forecast = predict_race_conditions(
        track_id,
        race_date,
    )

    if forecast.get("status") != "ok":
        return forecast

    races = load_races()
    nominations = load_nominations()
    strategies = load_winner_strategies()

    fp2 = build_fp2_features(
        load_fp2()
    )

    features = {
        rid: race_features(
            race,
            nominations,
            fp2,
        )
        for rid, race in races.items()
    }

    fp2_scales = {
        key: robust_scale([
            f.get(key)
            for f in features.values()
        ])
        for key in FP2_NUMERIC
    }

    target_features = make_target_features(
        target,
        forecast,
        nominations,
        fp2,
    )

    # Inject the target's REAL pre-race forecast features.
    features[target["id"]] = target_features

    ranked, analogs, stop_evidence = candidate_strategies(
        target,
        races,
        features,
        nominations,
        strategies,
        fp2_scales,
    )

    if not ranked:
        return {
            "status": "no_strategy_history",
            "message": (
                "No usable same-era historical strategy "
                "evidence is available."
            ),
            "regulation_era": ERA,
        }

    # Top strategy plus four alternatives.
    selected = ranked[:5]

    primary = selected[0]["sequence"]

    strategy = get_stint_plan(
        primary,
        track_id,
        race_date,
        ERA,
    )

    alternatives = []

    for item in selected[1:]:
        seq = item["sequence"]

        alternatives.append({
            "sequence": seq,
            "score": round(
                float(item["score"]),
                4,
            ),
            "historical_races": item["races"],
            "stints": get_stint_plan(
                seq,
                track_id,
                race_date,
                ERA,
            ),
        })

    analog_count = len(analogs)

    tier = forecast.get(
        "tier",
        "forecast",
    )

    confidence = confidence_from_ranking(
        ranked,
        analog_count,
        tier,
    )

    # Stop distribution from condition-aware analogs.
    stop_total = sum(
        stop_evidence.values()
    )

    if stop_total > 0:
        stop_probabilities = {
            str(k): round(
                100 * stop_evidence.get(
                    k,
                    0.0,
                ) / stop_total,
                1,
            )
            for k in (1, 2, 3)
        }
    else:
        stop_probabilities = {}

    compound_data = get_compound_nominations(
        track_id,
        race_date,
    )

    def annotate(stints):
        for stint in stints:
            if compound_data:
                stint["c_compound"] = (
                    compound_data.get(
                        stint["compound"]
                    )
                )
        return stints

    annotate(strategy)

    for alt in alternatives:
        annotate(
            alt["stints"]
        )

    return {
        "status": "ok",

        "strategy": strategy,

        "primary_strategy": {
            "sequence": primary,
            "score": round(
                float(selected[0]["score"]),
                4,
            ),
            "historical_races": selected[0]["races"],
        },

        "alternatives": alternatives,

        "stop_probabilities": stop_probabilities,

        "confidence_pct": confidence,

        "tier": tier,
        "days_out": forecast.get(
            "days_out"
        ),

        "regulation_era": ERA,

        "forecast_conditions": {
            "track_temp": forecast.get(
                "target_track_temp"
            ),
            "rain_expected": forecast.get(
                "rain_expected"
            ),
            "rain_onset_lap": forecast.get(
                "rain_onset_lap"
            ),
        },

        "evidence": {
            "historical_races": len([
                rid
                for rid, r in races.items()
                if (
                    r["race_date"]
                    and r["race_date"] < race_date
                    and r["regulation_era"] == ERA
                    and rid in strategies
                )
            ]),
            "condition_matched_analogs": analog_count,
            "fp2_available": (
                target["id"] in fp2
            ),
            "compound_nominations_available": bool(
                compound_data
            ),
        },

        "selection_method": (
            "Strict walk-forward historical strategy "
            "ranking using regulation-era, circuit, "
            "weather, Pirelli nomination, FP2 degradation "
            "and compound-pace similarity. Target-race "
            "results and incidents are never used."
        ),

        "confidence_note": (
            "Confidence represents separation between "
            "ranked historical strategy evidence, not a "
            "claim that the exact race outcome is "
            "predictable."
        ),
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--track_id",
        type=int,
        required=True,
    )

    parser.add_argument(
        "--race_date",
        required=True,
    )

    args = parser.parse_args()

    result = predict(
        args.track_id,
        date.fromisoformat(
            args.race_date
        ),
    )

    print(result)
