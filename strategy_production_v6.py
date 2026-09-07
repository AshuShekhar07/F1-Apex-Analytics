import os
from datetime import date

from sqlalchemy import create_engine, text

import strategy_model_v5 as model

from fetch_race_forecast import predict_race_conditions

engine = create_engine(os.environ["DATABASE_URL"])


def find_race(track_id, race_date):
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
                COALESCE(r.safety_car_periods, 0) AS safety_car_periods,
                COALESCE(r.vsc_periods, 0) AS vsc_periods,
                COALESCE(r.red_flags, 0) AS red_flags,
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


def get_target_fp2(race_id):
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT
                COUNT(*) AS laps,
                COUNT(DISTINCT l.race_entry_id) AS drivers
            FROM sessions s
            JOIN laps l
              ON l.session_id = s.id
            WHERE s.race_id = :race_id
              AND s.session_type = 'FP2'
              AND l.lap_time IS NOT NULL
              AND l.is_valid = TRUE
        """), {"race_id": race_id}).mappings().first()

    if not row or not row["laps"]:
        return False

    return int(row["laps"]) > 0


def get_nominations(race_id):
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT
                label,
                c_compound
            FROM race_compound_nominations
            WHERE race_id = :race_id
            ORDER BY c_compound
        """), {"race_id": race_id}).mappings().all()

    return {
        str(x["label"]).upper(): x["c_compound"]
        for x in rows
    }


def get_stint_plan(
    sequence,
    races,
    strategies,
    track_id,
    target_date,
    era,
):
    """
    Calculate stint windows directly from race_stints.

    Same-track exact-sequence examples are preferred.
    When none exist, same-era cross-track examples are normalized
    to percentage-of-race-distance and scaled to the target track.
    """

    wanted = tuple(sequence)

    # --------------------------------------------------------
    # TARGET RACE DISTANCE
    # --------------------------------------------------------
    with engine.connect() as conn:
        track = conn.execute(text("""
            SELECT total_race_laps
            FROM tracks
            WHERE id = :track_id
            LIMIT 1
        """), {
            "track_id": track_id,
        }).mappings().first()

    target_laps = (
        int(track["total_race_laps"])
        if track
        and track["total_race_laps"] is not None
        else None
    )

    if target_laps is None:
        return []

    # --------------------------------------------------------
    # LOAD COMPLETE WINNER STRATEGIES DIRECTLY FROM DB
    # --------------------------------------------------------
    rows = []

    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT
                r.id AS race_id,
                r.track_id,
                r.race_date,
                rs.stint_number,
                rs.compound,
                rs.start_lap,
                rs.end_lap
            FROM races r
            JOIN race_stints rs
              ON rs.race_id = r.id
             AND rs.finishing_position = 1
            JOIN sessions s
              ON s.race_id = r.id
             AND s.session_type = 'R'
            JOIN race_results rr
              ON rr.race_entry_id = rs.race_entry_id
             AND rr.session_id = s.id
            WHERE r.regulation_era = :era
              AND r.race_date < :target_date
              AND rr.status = ANY(:finished)
            ORDER BY
                r.id,
                rs.stint_number
        """), {
            "era": era,
            "target_date": target_date,
            "finished": [
                "Finished",
                "+1 Lap",
                "+2 Laps",
                "+3 Laps",
                "+4 Laps",
                "+5 Laps",
                "+6 Laps",
            ],
        }).mappings().all()

    by_race = {}

    for row in rows:
        rid = int(row["race_id"])

        by_race.setdefault(
            rid,
            {
                "track_id": int(row["track_id"]),
                "race_date": row["race_date"],
                "stints": [],
            },
        )

        by_race[rid]["stints"].append({
            "compound": str(
                row["compound"]
            ).upper(),
            "start": (
                int(row["start_lap"])
                if row["start_lap"] is not None
                else None
            ),
            "end": (
                int(row["end_lap"])
                if row["end_lap"] is not None
                else None
            ),
        })

    # --------------------------------------------------------
    # EXACT SAME-TRACK MATCHES
    # --------------------------------------------------------
    same_track = []

    for data in by_race.values():
        if data["track_id"] != int(track_id):
            continue

        strategy = data["stints"]

        if tuple(
            x["compound"]
            for x in strategy
        ) != wanted:
            continue

        if any(
            x["start"] is None
            or x["end"] is None
            for x in strategy
        ):
            continue

        same_track.append(strategy)

    def build_absolute(samples):
        output = []

        for i, compound in enumerate(wanted):
            starts = [
                sample[i]["start"]
                for sample in samples
            ]

            ends = [
                sample[i]["end"]
                for sample in samples
            ]

            output.append({
                "compound": compound,
                "avg_start_lap": round(
                    sum(starts) / len(starts)
                ),
                "avg_end_lap": round(
                    sum(ends) / len(ends)
                ),
                "sample_count": len(samples),
                "window_source": "same_track",
            })

        return output

    if same_track:
        return build_absolute(
            same_track
        )

    # --------------------------------------------------------
    # CROSS-TRACK NORMALIZED MATCHES
    # --------------------------------------------------------
    normalized = []

    for data in by_race.values():
        strategy = data["stints"]

        if tuple(
            x["compound"]
            for x in strategy
        ) != wanted:
            continue

        if any(
            x["start"] is None
            or x["end"] is None
            for x in strategy
        ):
            continue

        race_distance = strategy[-1]["end"]

        if race_distance <= 0:
            continue

        normalized.append([
            {
                "start_pct":
                    x["start"] / race_distance,
                "end_pct":
                    x["end"] / race_distance,
            }
            for x in strategy
        ])

    if not normalized:
        return []

    output = []

    for i, compound in enumerate(wanted):
        starts = [
            x[i]["start_pct"]
            for x in normalized
        ]

        ends = [
            x[i]["end_pct"]
            for x in normalized
        ]

        output.append({
            "compound": compound,
            "avg_start_lap": max(
                1,
                round(
                    sum(starts)
                    / len(starts)
                    * target_laps
                ),
            ),
            "avg_end_lap": min(
                target_laps,
                round(
                    sum(ends)
                    / len(ends)
                    * target_laps
                ),
            ),
            "sample_count": len(normalized),
            "window_source":
                "cross_track_normalized",
        })

    return output



def get_supporting_races(
    sequence,
    target_date,
    era,
    limit=5,
):
    """
    Return the actual distinct historical races supporting
    the selected strategy. Evidence is one winner strategy
    per race, never one row per driver/stint.
    """
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT
                r.id AS race_id,
                r.season_year,
                r.round_number,
                r.race_date,
                t.name AS track_name,
                rs.stint_number,
                rs.compound,
                rs.start_lap,
                rs.end_lap
            FROM races r
            JOIN tracks t
              ON t.id = r.track_id
            JOIN race_stints rs
              ON rs.race_id = r.id
             AND rs.finishing_position = 1
            JOIN sessions s
              ON s.race_id = r.id
             AND s.session_type = 'R'
            JOIN race_results rr
              ON rr.race_entry_id = rs.race_entry_id
             AND rr.session_id = s.id
            WHERE r.regulation_era = :era
              AND r.race_date < :target_date
              AND rr.status = ANY(:finished)
            ORDER BY
                r.race_date,
                rs.stint_number
        """), {
            "era": era,
            "target_date": target_date,
            "finished": [
                "Finished",
                "+1 Lap",
                "+2 Laps",
                "+3 Laps",
                "+4 Laps",
                "+5 Laps",
                "+6 Laps",
            ],
        }).mappings().all()

    grouped = {}

    for row in rows:
        rid = int(row["race_id"])

        grouped.setdefault(
            rid,
            {
                "race_id": rid,
                "season": row["season_year"],
                "round": row["round_number"],
                "race_date": row["race_date"],
                "track_name": row["track_name"],
                "sequence": [],
                "stints": [],
            },
        )

        grouped[rid]["sequence"].append(
            str(row["compound"]).upper()
        )

        grouped[rid]["stints"].append({
            "compound": str(
                row["compound"]
            ).upper(),
            "start_lap": row["start_lap"],
            "end_lap": row["end_lap"],
        })

    wanted = tuple(sequence)

    matches = [
        r for r in grouped.values()
        if tuple(r["sequence"]) == wanted
    ]

    # Newest supporting races first.
    matches.sort(
        key=lambda x: x["race_date"],
        reverse=True,
    )

    return matches[:limit]

def confidence(
    ranked,
    history_count,
    fp2_available,
    forecast_tier,
):
    if not ranked:
        return 20

    scores = [
        max(0.0, float(x["score"]))
        for x in ranked[:5]
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

    c = (
        35
        + 35 * primary_share
        + 25 * margin
    )

    # Small same-era samples should never produce huge confidence.
    if history_count < 8:
        c = min(c, 50)
    elif history_count < 15:
        c = min(c, 65)
    elif history_count < 25:
        c = min(c, 75)

    if not fp2_available:
        c -= 5

    if forecast_tier == "climatology":
        c = min(c, 45)
    elif forecast_tier == "forecast":
        c = min(c, 75)

    return max(20, min(82, round(c)))


def predict(track_id, race_date):
    target = find_race(
        track_id,
        race_date,
    )

    if target is None:
        return {
            "status": "not_found",
            "message": (
                f"No race found for track {track_id} "
                f"on {race_date}."
            ),
        }

    era = target["regulation_era"]

    # IMPORTANT:
    # Dynamically scope the model to the target regulation era.
    model.ERA = era

    forecast = predict_race_conditions(
        track_id,
        race_date,
    )

    if forecast.get("status") != "ok":
        return forecast

    races = model.load_races()
    nominations = model.load_nominations()
    strategies = model.load_winner_strategies()

    fp2_rows = model.load_fp2()
    fp2 = model.build_fp2_features(
        fp2_rows
    )

    features = {
        rid: model.race_features(
            race,
            nominations,
            fp2,
        )
        for rid, race in races.items()
    }

    # Target's real pre-race forecast overrides historical race-weather values.
    target_weather = {
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

    target_features = model.race_features(
        target,
        nominations,
        fp2,
        weather_override=target_weather,
    )

    features[target["id"]] = target_features

    scales = {
        key: model.robust_scale([
            x.get(key)
            for x in features.values()
        ])
        for key in model.FP2_NUMERIC
    }

    # Only races before target date count as evidence.
    prior_ids = [
        rid
        for rid, r in races.items()
        if (
            r["race_date"] < race_date
            and r["regulation_era"] == era
            and rid in strategies
        )
    ]

    ranked, analogs, stop_evidence = (
        model.candidate_strategies(
            target,
            {
                rid: races[rid]
                for rid in prior_ids
            },
            features,
            nominations,
            strategies,
            scales,
        )
    )

    # ------------------------------------------------------------
    # UNIFIED WET STRATEGY FALLBACK
    # ------------------------------------------------------------
    #
    # Same-era evidence is always preferred.
    # Only when a wet target has no same-era wet strategy evidence
    # do we explicitly fall back one regulation era.
    #
    # This keeps the primary model era-isolated while allowing
    # 2026+ wet predictions to use the immediately preceding
    # compound/regulation era when no 2026 wet examples exist.
    #
    target_is_wet = bool(
        target_features.get("rain", 0)
    )

    evidence_scope = (
        "cross-track, same-era, pre-target-date"
    )
    era_fallback_used = False

    if target_is_wet and not ranked:
        previous_era = {
            "era2_18inch_groundeffect":
                "era1_13inch",
            "era3_2026regs":
                "era2_18inch_groundeffect",
        }.get(era)

        if previous_era:
            original_era = model.ERA

            try:
                model.ERA = previous_era

                fallback_ids = [
                    rid
                    for rid, r in races.items()
                    if (
                        r["race_date"] < race_date
                        and r["regulation_era"] == previous_era
                        and rid in strategies
                    )
                ]

                ranked, analogs, stop_evidence = (
                    model.candidate_strategies(
                        target,
                        {
                            rid: races[rid]
                            for rid in fallback_ids
                        },
                        features,
                        nominations,
                        strategies,
                        scales,
                    )
                )

                if ranked:
                    era_fallback_used = True
                    evidence_scope = (
                        "cross-track, prior-era, "
                        "pre-target-date"
                    )
            finally:
                model.ERA = original_era

    if not ranked:
        return {
            "status": "no_strategy_history",
            "regulation_era": era,
            "historical_races": len(prior_ids),
            "message": (
                "No usable same-era historical strategy "
                "evidence is available."
            ),
        }

    primary = ranked[0]["sequence"]

    plan = get_stint_plan(
        primary,
        races,
        strategies,
        track_id,
        race_date,
        era,
    )

    # Build top alternatives.
    alternatives = []

    for item in ranked[1:4]:
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
                races,
                strategies,
                track_id,
                race_date,
                era,
            ),
        })

    # Stop distribution.
    total_stop_evidence = sum(
        stop_evidence.values()
    )

    if total_stop_evidence > 0:
        stop_probabilities = {
            str(k): round(
                100
                * stop_evidence.get(k, 0.0)
                / total_stop_evidence,
                1,
            )
            for k in (1, 2, 3)
        }
    else:
        stop_probabilities = {}

    fp2_available = get_target_fp2(
        target["id"]
    )

    compounds = get_nominations(
        target["id"]
    )

    # Add real C-compound labels.
    def annotate(stints):
        for stint in stints:
            if compounds:
                stint["c_compound"] = compounds.get(
                    stint["compound"]
                )
        return stints

    annotate(plan)

    for alt in alternatives:
        annotate(alt["stints"])

    c = confidence(
        ranked,
        len(prior_ids),
        fp2_available,
        forecast.get("tier"),
    )

    # Explicit reliability metadata for wet/transition predictions.
    wet_evidence_count = len(analogs) if target_is_wet else 0

    if target_is_wet:
        if era_fallback_used:
            c = min(c, 45)
        elif wet_evidence_count < 3:
            c = min(c, 40)
        elif wet_evidence_count < 6:
            c = min(c, 50)
        elif wet_evidence_count < 10:
            c = min(c, 60)

    c = max(20, min(82, int(round(c))))

    if c <= 50:
        confidence_level = "low"
    elif c < 65:
        confidence_level = "medium"
    else:
        confidence_level = "high"

    weather_context = "wet" if target_is_wet else "dry"

    return {
        "status": "ok",

        "strategy": plan,

        "primary_strategy": {
            "sequence": primary,
            "score": round(
                float(ranked[0]["score"]),
                4,
            ),
            "historical_races": ranked[0]["races"],
        },

        "alternatives": alternatives,

        "stop_probabilities": stop_probabilities,

                "confidence_pct": c,
        "confidence_level": confidence_level,
        "weather_context": weather_context,
        "wet_race_warning": bool(target_is_wet),
        "wet_model_reliability": (
            "limited"
            if target_is_wet
            and (
                era_fallback_used
                or wet_evidence_count < 6
            )
            else "supported"
            if target_is_wet
            else "standard"
        ),
        "tier": forecast.get("tier"),
        "days_out": forecast.get(
            "days_out"
        ),

        "regulation_era": era,

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
            "same_era_historical_races": len(
                prior_ids
            ),
            "condition_matched_analogs": len(
                analogs
            ),
            "fp2_available": fp2_available,
            "compound_nominations_available": bool(
                compounds
            ),
            "wet_evidence_races": wet_evidence_count,
            "era_fallback_used": era_fallback_used,
            "evidence_scope": evidence_scope
        },

        "compound_nominations": compounds,

        "evidence_scope": evidence_scope,

        "primary_strategy_evidence": {
            "distinct_races": ranked[0]["races"],
            "scope": evidence_scope,
            "supporting_races": get_supporting_races(
                primary,
                race_date,
                era,
                limit=5,
            ),
        },

        "selection_method": (
            "Era-isolated historical strategy ranking. "
            "Only races before the target date contribute "
            "historical evidence. Target-race results, "
            "stints and incident outcomes are excluded. "
            "Pre-race forecast, FP2 and sourced Pirelli "
            "nominations may be used."
        ),

        "confidence_note": (
            "Confidence measures separation of the "
            "available strategy evidence. It is not an "
            "estimated probability that the exact strategy "
            "will occur."
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
