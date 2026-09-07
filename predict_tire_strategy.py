import os
import argparse
from collections import Counter, defaultdict
from datetime import date
from sqlalchemy import create_engine, text

engine = create_engine(os.environ["DATABASE_URL"])

TEMP_TOLERANCE_C = 5.0
RAIN_ONSET_TOLERANCE_LAPS = 8
RECENCY_HALF_LIFE_YEARS = 3

FINISHED_STATUSES = (
    'Finished',
    '+1 Lap',
    '+2 Laps',
    '+3 Laps',
    '+5 Laps',
    '+6 Laps'
)

# Race-result evidence weights.
# A winner is substantially stronger evidence than P2/P3.
POSITION_WEIGHT = {
    1: 5.0,
    2: 2.0,
    3: 1.0,
}


def get_safety_car_likelihood(track_id):
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT
                COUNT(*) AS total_races,
                SUM(
                    CASE
                        WHEN COALESCE(safety_car_periods, 0) > 0
                          OR COALESCE(vsc_periods, 0) > 0
                        THEN 1 ELSE 0
                    END
                ) AS races_with_sc
            FROM races
            WHERE track_id = :tid
              AND race_date < CURRENT_DATE
        """), {"tid": track_id}).mappings().first()

    if row is None or row["total_races"] == 0:
        return None

    return round(
        100 * row["races_with_sc"] / row["total_races"]
    )


def get_regulation_era(season_year):
    if 2018 <= season_year <= 2021:
        return 'era1_13inch'
    elif 2022 <= season_year <= 2025:
        return 'era2_18inch_groundeffect'
    else:
        return 'era3_2026regs'


def get_strategy_recommendation(
    track_id,
    target_track_temp,
    target_rainfall,
    target_rain_onset_lap=None,
    target_regulation_era=None,
    top_n=3,
    exclude_race_id=None
):
    with engine.connect() as conn:
        query = """
            SELECT
                rs.race_id,
                rs.race_entry_id,
                rs.finishing_position,
                rs.stint_number,
                rs.compound,
                rs.start_lap,
                rs.end_lap,
                rs.stint_length,
                sw.track_temp_avg,
                sw.rainfall,
                sw.rain_onset_lap,
                r.season_year,
                rr.status
            FROM race_stints rs
            JOIN races r
              ON r.id = rs.race_id
            JOIN sessions s
              ON s.race_id = r.id
             AND s.session_type = 'R'
            JOIN session_weather sw
              ON sw.session_id = s.id
            JOIN race_results rr
              ON rr.race_entry_id = rs.race_entry_id
             AND rr.session_id = s.id
            WHERE r.track_id = :track_id
              AND rs.finishing_position <= :top_n
              AND sw.rainfall = :rainfall
              AND rr.status = ANY(:finished_statuses)
              AND (:exclude_race_id IS NULL OR r.id != :exclude_race_id)
        """

        params = {
            "track_id": track_id,
            "top_n": top_n,
            "rainfall": target_rainfall,
            "finished_statuses": list(FINISHED_STATUSES),
            "exclude_race_id": exclude_race_id,
        }

        if target_regulation_era is not None:
            query += """
                AND r.regulation_era = :era
            """
            params["era"] = target_regulation_era

        query += """
            ORDER BY
                r.season_year,
                rs.finishing_position,
                rs.stint_number
        """

        rows = conn.execute(
            text(query),
            params
        ).mappings().all()

    if not rows:
        era_note = (
            f" in the {target_regulation_era} era"
            if target_regulation_era
            else ""
        )

        return {
            "status": "no_data",
            "message": (
                f"No historical top-{top_n} classified finishes found "
                f"at this track under "
                f"{'wet' if target_rainfall else 'dry'} conditions"
                f"{era_note}."
            ),
            "confidence_pct": 0,
        }

    # ------------------------------------------------------------
    # CONDITION MATCHING
    # ------------------------------------------------------------

    if target_rainfall and target_rain_onset_lap is not None:
        matched = [
            r for r in rows
            if r["rain_onset_lap"] is not None
            and abs(
                int(r["rain_onset_lap"]) -
                target_rain_onset_lap
            ) <= RAIN_ONSET_TOLERANCE_LAPS
        ]

        used_tolerance = RAIN_ONSET_TOLERANCE_LAPS

        if not matched:
            used_tolerance = RAIN_ONSET_TOLERANCE_LAPS * 2

            matched = [
                r for r in rows
                if r["rain_onset_lap"] is not None
                and abs(
                    int(r["rain_onset_lap"]) -
                    target_rain_onset_lap
                ) <= used_tolerance
            ]

    else:
        matched = [
            r for r in rows
            if abs(
                float(r["track_temp_avg"]) -
                target_track_temp
            ) <= TEMP_TOLERANCE_C
        ]

        used_tolerance = TEMP_TOLERANCE_C

        if not matched:
            used_tolerance = TEMP_TOLERANCE_C * 2

            matched = [
                r for r in rows
                if abs(
                    float(r["track_temp_avg"]) -
                    target_track_temp
                ) <= used_tolerance
            ]

    if not matched:
        return {
            "status": "no_close_match",
            "message": (
                "No historical races with similar conditions "
                f"(target {target_track_temp}C, "
                f"{'wet' if target_rainfall else 'dry'}"
                f"{f', rain onset ~lap {target_rain_onset_lap}' if target_rainfall and target_rain_onset_lap else ''})."
            ),
            "confidence_pct": 0,
        }

    # ------------------------------------------------------------
    # GROUP STINTS INTO DRIVER/RACE INSTANCES
    # ------------------------------------------------------------

    by_entry = {}

    for r in matched:
        key = (
            r["race_id"],
            r["race_entry_id"]
        )

        by_entry.setdefault(key, []).append(r)

    current_year = date.today().year

    def recency_weight(season_year):
        years_ago = max(
            0,
            current_year - season_year
        )

        return 0.5 ** (
            years_ago / RECENCY_HALF_LIFE_YEARS
        )

    # ------------------------------------------------------------
    # BUILD STRATEGY EVIDENCE AT RACE LEVEL
    # ------------------------------------------------------------

    signatures = defaultdict(list)

    for key, stints in by_entry.items():
        stints_sorted = sorted(
            stints,
            key=lambda x: x["stint_number"]
        )

        signature = tuple(
            s["compound"]
            for s in stints_sorted
        )

        signatures[signature].append(
            stints_sorted
        )

    # For each race + strategy, retain the BEST finishing position
    # among drivers who used that strategy.
    #
    # This is important when multiple podium drivers use the same
    # strategy. We must not let a later P2/P3 row overwrite P1 evidence.
    race_strategies = defaultdict(dict)

    for signature, instances in signatures.items():
        for stints in instances:
            race_id = stints[0]["race_id"]
            season_year = stints[0]["season_year"]
            finishing_position = int(
                stints[0]["finishing_position"]
            )

            existing = race_strategies[race_id].get(signature)

            if (
                existing is None
                or finishing_position < existing["finishing_position"]
            ):
                race_strategies[race_id][signature] = {
                    "season_year": season_year,
                    "finishing_position": finishing_position,
                }

    strategy_scores = Counter()
    strategy_race_counts = Counter()
    strategy_winner_counts = Counter()

    for race_id, strategies in race_strategies.items():

        for signature, data in strategies.items():
            season_year = data["season_year"]
            finishing_position = data["finishing_position"]

            position_weight = POSITION_WEIGHT.get(
                finishing_position,
                0.0
            )

            recency = recency_weight(
                season_year
            )

            evidence = (
                position_weight *
                recency
            )

            strategy_scores[signature] += evidence
            strategy_race_counts[signature] += 1

            if finishing_position == 1:
                strategy_winner_counts[signature] += 1

    # ------------------------------------------------------------
    # SELECT PRIMARY STRATEGY
    # ------------------------------------------------------------

    best_sig = max(
        strategy_scores,
        key=strategy_scores.get
    )

    best_instances = signatures[best_sig]

    # ------------------------------------------------------------
    # REPRESENTATIVE STINT WINDOWS
    # ------------------------------------------------------------

    stint_plan = []

    for i in range(len(best_sig)):

        valid_instances = [
            inst
            for inst in best_instances
            if len(inst) > i
        ]

        weights = [
            recency_weight(
                inst[0]["season_year"]
            )
            for inst in valid_instances
        ]

        starts = [
            inst[i]["start_lap"]
            for inst in valid_instances
        ]

        ends = [
            inst[i]["end_lap"]
            for inst in valid_instances
        ]

        total_weight = sum(weights)

        stint_plan.append({
            "compound": best_sig[i],
            "avg_start_lap": round(
                sum(
                    value * weight
                    for value, weight
                    in zip(starts, weights)
                ) / total_weight
            ),
            "avg_end_lap": round(
                sum(
                    value * weight
                    for value, weight
                    in zip(ends, weights)
                ) / total_weight
            ),
        })

    # ------------------------------------------------------------
    # CONFIDENCE
    # ------------------------------------------------------------

    total_matches = len(by_entry)

    matched_races = len(
        race_strategies
    )

    total_strategy_score = sum(
        strategy_scores.values()
    )

    best_score = strategy_scores[
        best_sig
    ]

    share_of_evidence = (
        best_score / total_strategy_score
        if total_strategy_score > 0
        else 0
    )

    distinct_winning_strategies = len([
        sig
        for sig, count
        in strategy_winner_counts.items()
        if count > 0
    ])

    # Base confidence reflects evidence volume.
    confidence = min(
        75,
        30 + matched_races * 8
    )

    # Strong consensus increases confidence.
    if share_of_evidence >= 0.70:
        confidence += 15
    elif share_of_evidence >= 0.55:
        confidence += 5

    # Multiple different winning strategies mean the historical
    # evidence is genuinely uncertain.
    if distinct_winning_strategies >= 3:
        confidence -= 20
    elif distinct_winning_strategies == 2:
        confidence -= 10

    if used_tolerance > TEMP_TOLERANCE_C:
        confidence -= 10

    confidence_pct = max(
        15,
        min(85, round(confidence))
    )

    # ------------------------------------------------------------
    # ALTERNATIVE STRATEGIES
    # ------------------------------------------------------------

    alternatives = []

    for signature, score in strategy_scores.most_common():
        if signature == best_sig:
            continue

        alternatives.append({
            "sequence": list(signature),
            "instances": strategy_race_counts[signature],
            "winner_instances": strategy_winner_counts.get(
                signature,
                0
            ),
            "evidence_score": round(
                score,
                3
            ),
        })

        if len(alternatives) == 4:
            break

    sc_likelihood = get_safety_car_likelihood(
        track_id
    )

    return {
        "status": "ok",
        "strategy": stint_plan,

        "matched_instances": total_matches,
        "matched_races": matched_races,
        "matched_seasons": sorted(
            set(
                r["season_year"]
                for r in matched
            )
        ),

        "temp_tolerance_used_c": used_tolerance,

        "confidence_pct": confidence_pct,

        "safety_car_likelihood_pct": sc_likelihood,

        "flexibility_note": (
            f"This track has seen a Safety Car or VSC in "
            f"{sc_likelihood}% of races historically -- worth keeping "
            "some stint-length flexibility rather than committing "
            "rigidly to these exact lap windows."
        )
        if sc_likelihood is not None
        and sc_likelihood >= 50
        else None,

        "selection_method": (
            "Strategies ranked using race-level historical evidence: "
            "P1=5, P2=2, P3=1, with recency weighting. "
            "Confidence is reduced when matched races disagree."
        ),

        "primary_strategy_evidence_score": round(
            best_score,
            3
        ),

        "primary_strategy_evidence_share_pct": round(
            share_of_evidence * 100
        ),

        "primary_strategy_winner_instances":
            strategy_winner_counts.get(
                best_sig,
                0
            ),

        "alternate_strategies": alternatives,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--track_id",
        type=int,
        required=True
    )

    parser.add_argument(
        "--track_temp",
        type=float,
        required=True
    )

    parser.add_argument(
        "--rain",
        action="store_true"
    )

    parser.add_argument(
        "--rain_onset_lap",
        type=int,
        default=None
    )

    parser.add_argument(
        "--era",
        type=str,
        default=None,
        help=(
            "e.g. era1_13inch, "
            "era2_18inch_groundeffect, "
            "era3_2026regs"
        )
    )

    args = parser.parse_args()

    result = get_strategy_recommendation(
        args.track_id,
        args.track_temp,
        args.rain,
        args.rain_onset_lap,
        args.era
    )

    print(
        f"\nStatus: {result['status']}"
    )

    if result["status"] == "ok":

        print(
            f"Confidence: "
            f"{result['confidence_pct']}%"
        )

        print(
            f"Based on "
            f"{result['matched_instances']} driver instances "
            f"across {result['matched_races']} races "
            f"from seasons "
            f"{result['matched_seasons']}"
        )

        print(
            f"Temp tolerance used: "
            f"+/-{result['temp_tolerance_used_c']}C"
        )

        print(
            f"Selection method: "
            f"{result['selection_method']}"
        )

        print(
            f"Primary evidence score: "
            f"{result['primary_strategy_evidence_score']}"
        )

        print(
            f"Primary evidence share: "
            f"{result['primary_strategy_evidence_share_pct']}%"
        )

        print(
            f"Primary strategy wins: "
            f"{result['primary_strategy_winner_instances']}"
        )

        print("\nRecommended strategy:")

        for stint in result["strategy"]:
            print(
                f"  {stint['compound']}: "
                f"laps {stint['avg_start_lap']}-"
                f"{stint['avg_end_lap']}"
            )

        if result.get(
            "safety_car_likelihood_pct"
        ) is not None:
            print(
                "\nSafety Car/VSC historical likelihood "
                f"at this track: "
                f"{result['safety_car_likelihood_pct']}%"
            )

        if result.get("flexibility_note"):
            print(
                f"Note: "
                f"{result['flexibility_note']}"
            )

        if result["alternate_strategies"]:
            print(
                "\nOther strategies seen:"
            )

            for alt in result[
                "alternate_strategies"
            ]:
                print(
                    f"  {' -> '.join(alt['sequence'])} "
                    f"({alt['instances']} races, "
                    f"{alt['winner_instances']} wins, "
                    f"evidence {alt['evidence_score']})"
                )

    else:
        print(
            result.get(
                "message",
                result
            )
        )


def validate_historical_race(race_id):
    """
    Validate the strategy model against an already-completed race.

    Uses actual race weather from the DB rather than forecast/climatology.
    The production prediction path is intentionally untouched.
    """
    with engine.connect() as conn:
        race = conn.execute(text("""
            SELECT
                r.id AS race_id,
                r.season_year,
                r.race_date,
                r.track_id,
                r.regulation_era,
                sw.track_temp_avg,
                sw.rainfall,
                sw.rain_onset_lap
            FROM races r
            JOIN sessions s
              ON s.race_id = r.id
             AND s.session_type = 'R'
            JOIN session_weather sw
              ON sw.session_id = s.id
            WHERE r.id = :race_id
        """), {"race_id": race_id}).mappings().first()

        if race is None:
            return {
                "status": "not_found",
                "message": f"Race {race_id} not found or has no race weather."
            }

        winner_rows = conn.execute(text("""
            SELECT
                rs.stint_number,
                rs.compound,
                rs.start_lap,
                rs.end_lap,
                rs.finishing_position,
                rr.status
            FROM race_stints rs
            JOIN sessions s
              ON s.race_id = rs.race_id
             AND s.session_type = 'R'
            JOIN race_results rr
              ON rr.race_entry_id = rs.race_entry_id
             AND rr.session_id = s.id
            WHERE rs.race_id = :race_id
              AND rs.finishing_position = 1
              AND rr.status = ANY(:finished_statuses)
            ORDER BY rs.stint_number
        """), {
            "race_id": race_id,
            "finished_statuses": list(FINISHED_STATUSES),
        }).mappings().all()

    if not winner_rows:
        return {
            "status": "no_winner_strategy",
            "message": f"No classified winner strategy found for race {race_id}."
        }

    actual_strategy = [
        {
            "compound": row["compound"],
            "start_lap": row["start_lap"],
            "end_lap": row["end_lap"],
        }
        for row in winner_rows
    ]

    prediction = get_strategy_recommendation(
        track_id=race["track_id"],
        target_track_temp=float(race["track_temp_avg"]),
        target_rainfall=race["rainfall"],
        target_rain_onset_lap=race["rain_onset_lap"],
        target_regulation_era=race["regulation_era"],
        exclude_race_id=race_id,
    )

    if prediction["status"] != "ok":
        return {
            "status": "validation_unavailable",
            "race_id": race_id,
            "race": dict(race),
            "actual_winner_strategy": actual_strategy,
            "prediction": prediction,
        }

    predicted_sequence = [
        stint["compound"]
        for stint in prediction["strategy"]
    ]

    actual_sequence = [
        stint["compound"]
        for stint in actual_strategy
    ]

    sequence_match = (
        predicted_sequence == actual_sequence
    )

    # Compare pit-window/lap boundaries only when the sequence matches.
    lap_differences = []

    if sequence_match:
        for predicted, actual in zip(
            prediction["strategy"],
            actual_strategy
        ):
            lap_differences.append({
                "compound": actual["compound"],
                "predicted_start_lap": predicted["avg_start_lap"],
                "actual_start_lap": actual["start_lap"],
                "start_lap_difference": (
                    predicted["avg_start_lap"] -
                    actual["start_lap"]
                ),
                "predicted_end_lap": predicted["avg_end_lap"],
                "actual_end_lap": actual["end_lap"],
                "end_lap_difference": (
                    predicted["avg_end_lap"] -
                    actual["end_lap"]
                ),
            })

    return {
        "status": "ok",
        "race_id": race_id,
        "race": {
            "season_year": race["season_year"],
            "race_date": race["race_date"],
            "track_id": race["track_id"],
            "regulation_era": race["regulation_era"],
        },
        "actual_conditions": {
            "track_temp": float(race["track_temp_avg"]),
            "rainfall": race["rainfall"],
            "rain_onset_lap": race["rain_onset_lap"],
        },
        "actual_winner_strategy": actual_strategy,
        "predicted_strategy": prediction["strategy"],
        "actual_sequence": actual_sequence,
        "predicted_sequence": predicted_sequence,
        "sequence_match": sequence_match,
        "lap_differences": lap_differences,
        "prediction_confidence_pct": prediction["confidence_pct"],
        "matched_instances": prediction["matched_instances"],
        "matched_races": prediction["matched_races"],
        "matched_seasons": prediction["matched_seasons"],
    }
