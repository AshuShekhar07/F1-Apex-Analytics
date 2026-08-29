import os
import sys
import argparse
from datetime import date
from sqlalchemy import create_engine, text

from fetch_race_forecast import predict_race_conditions
from predict_tire_strategy import get_strategy_recommendation, get_regulation_era

engine = create_engine(os.environ["DATABASE_URL"])


def has_fp2_data(track_id, race_date):
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT s.id FROM sessions s
            JOIN races r ON r.id = s.race_id
            WHERE r.track_id = :tid AND r.race_date = :rd AND s.session_type = 'FP2'
        """), {"tid": track_id, "rd": race_date}).mappings().first()
    return row is not None


def get_compound_nominations(track_id, race_date):
    """Real Pirelli C-compound data for this specific race, if we have it sourced.
    Coverage is currently thin (only some 2025-2026 races) -- returns None when missing
    rather than guessing, so callers can fall back to the relative label honestly."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT rcn.label, rcn.c_compound
            FROM race_compound_nominations rcn
            JOIN races r ON r.id = rcn.race_id
            WHERE r.track_id = :tid AND r.race_date = :rd
        """), {"tid": track_id, "rd": race_date}).mappings().all()
    if not rows:
        return None
    return {row["label"]: row["c_compound"] for row in rows}


def predict(track_id, race_date):
    conditions = predict_race_conditions(track_id, race_date)
    if conditions["status"] != "ok":
        return conditions

    fp2_available = has_fp2_data(track_id, race_date)
    target_era = get_regulation_era(race_date.year)
    compound_nominations = get_compound_nominations(track_id, race_date)

    if conditions["tier"] == "climatology":
        confidence_cap = 40
        tier_label = "far_out"
        tier_message = ("Indicative only, based on typical historical conditions for this "
                         "time of year. Will sharpen closer to race week when a real forecast is available.")
    elif not fp2_available:
        confidence_cap = 65
        tier_label = "race_week_pre_fp2"
        tier_message = ("Pre-weekend estimate, based on live forecast and historical strategy patterns. "
                         "Will refine after Friday practice.")
    else:
        confidence_cap = 65
        tier_label = "post_fp2_not_yet_integrated"
        tier_message = ("NOTE: this weekend's FP2 long-run pace exists but is not yet factored into "
                         "this prediction -- that refinement is still to be built. Currently using the "
                         "same historical-analog method as the pre-weekend tier.")

    result = get_strategy_recommendation(
        track_id=track_id,
        target_track_temp=conditions["target_track_temp"],
        target_rainfall=conditions["rain_expected"],
        target_rain_onset_lap=conditions["rain_onset_lap"],
        target_regulation_era=target_era,
    )

    era_fallback_used = False
    if result["status"] != "ok":
        result = get_strategy_recommendation(
            track_id=track_id,
            target_track_temp=conditions["target_track_temp"],
            target_rainfall=conditions["rain_expected"],
            target_rain_onset_lap=conditions["rain_onset_lap"],
            target_regulation_era=None,
        )
        era_fallback_used = True

    if result["status"] == "ok":
        result["confidence_pct"] = min(result["confidence_pct"], confidence_cap)
        if era_fallback_used:
            result["confidence_pct"] = max(10, result["confidence_pct"] - 20)

        # annotate each stint with the real C-compound if we have it sourced for this race;
        # left out (not guessed) when we don't have the data
        if compound_nominations:
            for s in result["strategy"]:
                s["c_compound"] = compound_nominations.get(s["compound"])

    result["tier"] = tier_label
    result["tier_message"] = tier_message
    result["days_out"] = conditions["days_out"]
    result["regulation_era"] = target_era
    result["era_fallback_used"] = era_fallback_used
    result["era_fallback_note"] = (
        f"No same-era ({target_era}) history found at this track -- fell back to cross-era data. "
        f"Treat this with extra caution: tire construction/car weight differs meaningfully across eras."
    ) if era_fallback_used else None
    result["compound_data_available"] = compound_nominations is not None
    result["conditions_used"] = {
        "track_temp": conditions["target_track_temp"],
        "rain_expected": conditions["rain_expected"],
        "rain_onset_lap": conditions["rain_onset_lap"],
    }
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--track_id", type=int, required=True)
    parser.add_argument("--race_date", type=str, required=True)
    args = parser.parse_args()

    result = predict(args.track_id, date.fromisoformat(args.race_date))
    print(f"\nTier: {result.get('tier')}")
    print(f"{result.get('tier_message')}\n")
    if result["status"] == "ok":
        print(f"Regulation era: {result.get('regulation_era')}")
        if result.get("era_fallback_note"):
            print(f"WARNING: {result['era_fallback_note']}")
        print(f"Confidence: {result['confidence_pct']}%")
        print(f"Conditions used: {result['conditions_used']}\n")
        if not result.get("compound_data_available"):
            print("(No sourced Pirelli C-compound data for this specific race yet -- showing relative labels only)")
        print("Recommended strategy:")
        for s in result["strategy"]:
            label = s["compound"]
            if s.get("c_compound"):
                label = f"{label} ({s['c_compound']})"
            print(f"  {label}: laps {s['avg_start_lap']}-{s['avg_end_lap']}")
        if result.get("safety_car_likelihood_pct") is not None:
            print(f"\nSafety Car/VSC historical likelihood: {result['safety_car_likelihood_pct']}%")
        if result.get("flexibility_note"):
            print(f"Note: {result['flexibility_note']}")
    else:
        print(result.get("message", result))
