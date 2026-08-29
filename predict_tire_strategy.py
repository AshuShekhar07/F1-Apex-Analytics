import os
import sys
import argparse
from collections import Counter
from datetime import date
from sqlalchemy import create_engine, text

engine = create_engine(os.environ["DATABASE_URL"])

TEMP_TOLERANCE_C = 5.0
MIN_MATCHES_FOR_HIGH_CONFIDENCE = 5
RAIN_ONSET_TOLERANCE_LAPS = 8
RECENCY_HALF_LIFE_YEARS = 3

# statuses that represent a genuine classified finish, not a mechanical/accident retirement.
# a driver's final stint length only reflects a real strategic choice if they actually
# finished -- otherwise the stint just ends whenever the car broke or crashed, which would
# otherwise pollute the lap-window averages as if it were a deliberate pit decision.
FINISHED_STATUSES = ('Finished', '+1 Lap', '+2 Laps', '+3 Laps', '+5 Laps', '+6 Laps')


def get_safety_car_likelihood(track_id):
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT
                COUNT(*) AS total_races,
                SUM(CASE WHEN COALESCE(safety_car_periods, 0) > 0 OR COALESCE(vsc_periods, 0) > 0 THEN 1 ELSE 0 END) AS races_with_sc
            FROM races
            WHERE track_id = :tid AND race_date < CURRENT_DATE
        """), {"tid": track_id}).mappings().first()
    if row is None or row["total_races"] == 0:
        return None
    return round(100 * row["races_with_sc"] / row["total_races"])


def get_regulation_era(season_year):
    if 2018 <= season_year <= 2021:
        return 'era1_13inch'
    elif 2022 <= season_year <= 2025:
        return 'era2_18inch_groundeffect'
    else:
        return 'era3_2026regs'


def get_strategy_recommendation(track_id, target_track_temp, target_rainfall, target_rain_onset_lap=None,
                                  target_regulation_era=None, top_n=3):
    with engine.connect() as conn:
        query = """
            SELECT rs.race_id, rs.race_entry_id, rs.finishing_position,
                   rs.stint_number, rs.compound, rs.start_lap, rs.end_lap, rs.stint_length,
                   sw.track_temp_avg, sw.rainfall, sw.rain_onset_lap, r.season_year, rr.status
            FROM race_stints rs
            JOIN races r ON r.id = rs.race_id
            JOIN sessions s ON s.race_id = r.id AND s.session_type = 'R'
            JOIN session_weather sw ON sw.session_id = s.id
            JOIN race_results rr ON rr.race_entry_id = rs.race_entry_id AND rr.session_id = s.id
            WHERE r.track_id = :track_id
              AND rs.finishing_position <= :top_n
              AND sw.rainfall = :rainfall
              AND rr.status = ANY(:finished_statuses)
        """
        params = {"track_id": track_id, "top_n": top_n, "rainfall": target_rainfall,
                  "finished_statuses": list(FINISHED_STATUSES)}
        if target_regulation_era is not None:
            query += " AND r.regulation_era = :era"
            params["era"] = target_regulation_era
        query += " ORDER BY r.season_year, rs.finishing_position, rs.stint_number"

        rows = conn.execute(text(query), params).mappings().all()

    if not rows:
        era_note = f" in the {target_regulation_era} era" if target_regulation_era else ""
        return {
            "status": "no_data",
            "message": f"No historical top-{top_n} classified finishes found at this track under "
                       f"{'wet' if target_rainfall else 'dry'} conditions{era_note}.",
            "confidence_pct": 0,
        }

    if target_rainfall and target_rain_onset_lap is not None:
        matched = [r for r in rows if r["rain_onset_lap"] is not None
                   and abs(int(r["rain_onset_lap"]) - target_rain_onset_lap) <= RAIN_ONSET_TOLERANCE_LAPS]
        used_tolerance = RAIN_ONSET_TOLERANCE_LAPS
        if not matched:
            used_tolerance = RAIN_ONSET_TOLERANCE_LAPS * 2
            matched = [r for r in rows if r["rain_onset_lap"] is not None
                       and abs(int(r["rain_onset_lap"]) - target_rain_onset_lap) <= used_tolerance]
    else:
        matched = [r for r in rows if abs(float(r["track_temp_avg"]) - target_track_temp) <= TEMP_TOLERANCE_C]
        used_tolerance = TEMP_TOLERANCE_C
        if not matched:
            used_tolerance = TEMP_TOLERANCE_C * 2
            matched = [r for r in rows if abs(float(r["track_temp_avg"]) - target_track_temp) <= used_tolerance]

    if not matched:
        return {
            "status": "no_close_match",
            "message": f"No historical races with similar conditions "
                       f"(target {target_track_temp}C, {'wet' if target_rainfall else 'dry'}"
                       f"{f', rain onset ~lap {target_rain_onset_lap}' if target_rainfall and target_rain_onset_lap else ''}).",
            "confidence_pct": 0,
        }

    current_year = date.today().year

    def recency_weight(season_year):
        years_ago = max(0, current_year - season_year)
        return 0.5 ** (years_ago / RECENCY_HALF_LIFE_YEARS)

    by_entry = {}
    for r in matched:
        key = (r["race_id"], r["race_entry_id"])
        by_entry.setdefault(key, []).append(r)

    signatures = {}
    weights_by_sig = {}
    for key, stints in by_entry.items():
        stints_sorted = sorted(stints, key=lambda x: x["stint_number"])
        sig = tuple(s["compound"] for s in stints_sorted)
        w = recency_weight(stints_sorted[0]["season_year"])
        signatures.setdefault(sig, []).append(stints_sorted)
        weights_by_sig[sig] = weights_by_sig.get(sig, 0) + w

    best_sig = max(weights_by_sig, key=weights_by_sig.get)
    best_instances = signatures[best_sig]
    sig_counts = Counter({sig: len(instances) for sig, instances in signatures.items()})

    stint_plan = []
    num_stints = len(best_sig)
    for i in range(num_stints):
        ws = [recency_weight(inst[i]["season_year"]) for inst in best_instances]
        starts = [inst[i]["start_lap"] for inst in best_instances]
        ends = [inst[i]["end_lap"] for inst in best_instances]
        total_w = sum(ws)
        stint_plan.append({
            "compound": best_sig[i],
            "avg_start_lap": round(sum(s * w for s, w in zip(starts, ws)) / total_w),
            "avg_end_lap": round(sum(e * w for e, w in zip(ends, ws)) / total_w),
        })

    total_matches = len(by_entry)
    confidence_pct = min(85, 40 + total_matches * 8)
    if used_tolerance > TEMP_TOLERANCE_C:
        confidence_pct -= 15

    sc_likelihood = get_safety_car_likelihood(track_id)

    return {
        "status": "ok",
        "strategy": stint_plan,
        "matched_instances": total_matches,
        "matched_seasons": sorted(set(r["season_year"] for r in matched)),
        "temp_tolerance_used_c": used_tolerance,
        "confidence_pct": max(10, confidence_pct),
        "safety_car_likelihood_pct": sc_likelihood,
        "flexibility_note": (
            f"This track has seen a Safety Car or VSC in {sc_likelihood}% of races historically -- "
            f"worth keeping some stint-length flexibility rather than committing rigidly to these exact lap windows."
        ) if sc_likelihood is not None and sc_likelihood >= 50 else None,
        "alternate_strategies": [
            {"sequence": list(sig), "instances": count}
            for sig, count in sig_counts.most_common(4) if sig != best_sig
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--track_id", type=int, required=True)
    parser.add_argument("--track_temp", type=float, required=True)
    parser.add_argument("--rain", action="store_true")
    parser.add_argument("--rain_onset_lap", type=int, default=None)
    parser.add_argument("--era", type=str, default=None,
                         help="e.g. era1_13inch, era2_18inch_groundeffect, era3_2026regs")
    args = parser.parse_args()

    result = get_strategy_recommendation(args.track_id, args.track_temp, args.rain,
                                          args.rain_onset_lap, args.era)
    print(f"\nStatus: {result['status']}")
    if result["status"] == "ok":
        print(f"Confidence: {result['confidence_pct']}%")
        print(f"Based on {result['matched_instances']} similar historical instances from seasons {result['matched_seasons']}")
        print(f"Temp tolerance used: +/-{result['temp_tolerance_used_c']}C\n")
        print("Recommended strategy:")
        for s in result["strategy"]:
            print(f"  {s['compound']}: laps {s['avg_start_lap']}-{s['avg_end_lap']}")
        if result.get("safety_car_likelihood_pct") is not None:
            print(f"\nSafety Car/VSC historical likelihood at this track: {result['safety_car_likelihood_pct']}%")
        if result.get("flexibility_note"):
            print(f"Note: {result['flexibility_note']}")
        if result["alternate_strategies"]:
            print("\nOther strategies seen:")
            for alt in result["alternate_strategies"]:
                print(f"  {' -> '.join(alt['sequence'])} ({alt['instances']} instances)")
    else:
        print(result.get("message", result))
