import os
import pandas as pd
from sqlalchemy import create_engine, text

engine = create_engine(os.environ["DATABASE_URL"])

MIN_LONG_RUN_LAPS = 5


def _is_session_wet(track_id, race_date):
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT sw.rainfall
            FROM sessions s
            JOIN races r ON r.id = s.race_id
            JOIN session_weather sw ON sw.session_id = s.id
            WHERE r.track_id = :tid AND r.race_date = :rd AND s.session_type = 'FP2'
        """), {"tid": track_id, "rd": race_date}).mappings().first()
    if row is None:
        return None  # unknown -- no weather data for this session
    return bool(row["rainfall"])


def get_fp2_compound_pace(track_id, race_date):
    wet = _is_session_wet(track_id, race_date)
    if wet is True:
        return {
            "status": "fp2_was_wet",
            "message": "This weekend's FP2 was rain-affected -- compound pace comparison would be "
                       "meaningless (a damp track makes any compound look far slower, unrelated to "
                       "the tire itself). Skipping this comparison rather than reporting misleading numbers.",
        }

    with engine.connect() as conn:
        df = pd.read_sql(text("""
            SELECT l.race_entry_id, l.lap_number, l.lap_time, l.tire_compound
            FROM laps l
            JOIN sessions s ON s.id = l.session_id
            JOIN races r ON r.id = s.race_id
            WHERE r.track_id = :tid AND r.race_date = :rd AND s.session_type = 'FP2'
              AND l.is_valid = true AND l.lap_time IS NOT NULL
              AND l.tire_compound IS NOT NULL AND l.tire_compound NOT IN ('None', 'nan')
            ORDER BY l.race_entry_id, l.lap_number
        """), conn, params={"tid": track_id, "rd": race_date})

    if len(df) == 0:
        return {"status": "no_fp2_data"}

    df["stint_change"] = (df["tire_compound"] != df.groupby("race_entry_id")["tire_compound"].shift()).astype(int)
    df["stint_id"] = df.groupby("race_entry_id")["stint_change"].cumsum()
    df["stint_lap_count"] = df.groupby(["race_entry_id", "stint_id"])["lap_number"].transform("count")

    long_runs = df[df["stint_lap_count"] >= MIN_LONG_RUN_LAPS].copy()
    if len(long_runs) == 0:
        return {"status": "no_long_runs", "message": "No stints of 5+ laps found in FP2 -- teams may not have run race-representative long runs this session."}

    long_runs["lap_rank_in_stint"] = long_runs.groupby(["race_entry_id", "stint_id"]).cumcount()
    long_runs = long_runs[long_runs["lap_rank_in_stint"] > 0]

    def clip_outliers(g):
        lo, hi = g["lap_time"].quantile([0.05, 0.95])
        return g[(g["lap_time"] >= lo) & (g["lap_time"] <= hi)]
    long_runs = long_runs.groupby("tire_compound", group_keys=False).apply(clip_outliers)

    pace_by_compound = long_runs.groupby("tire_compound")["lap_time"].median().sort_values()

    return {
        "status": "ok",
        "compound_pace_ranking": pace_by_compound.to_dict(),
        "fastest_to_slowest": list(pace_by_compound.index),
        "sample_size": len(long_runs),
        "wet_session_data_known": wet is not None,
    }


def compare_to_historical_expectation(fp2_result, track_id, race_date):
    """Sanity-check: does this weekend's FP2 compound ordering match PAST YEARS'
    FP2 long-run ordering at this same track? We deliberately compare FP2-to-FP2
    (not FP2-to-race-baseline) because tire_degradation_curves.baseline_pace_seconds
    is confounded by fuel load / stint timing across compounds. Also excludes any
    prior-year FP2 sessions that were themselves wet, for the same reason."""
    if fp2_result["status"] != "ok":
        return {"consistent": None, "note": "No usable FP2 data to compare."}

    with engine.connect() as conn:
        df = pd.read_sql(text("""
            SELECT l.race_entry_id, l.lap_number, l.lap_time, l.tire_compound, r.season_year
            FROM laps l
            JOIN sessions s ON s.id = l.session_id
            JOIN races r ON r.id = s.race_id
            LEFT JOIN session_weather sw ON sw.session_id = s.id
            WHERE r.track_id = :tid AND r.race_date != :rd AND s.session_type = 'FP2'
              AND l.is_valid = true AND l.lap_time IS NOT NULL
              AND l.tire_compound IN ('SOFT', 'MEDIUM', 'HARD')
              AND COALESCE(sw.rainfall, false) = false
            ORDER BY l.race_entry_id, l.lap_number
        """), conn, params={"tid": track_id, "rd": race_date})

    if len(df) == 0:
        return {"consistent": None, "note": "No prior years' dry FP2 data at this track to compare against."}

    df["stint_change"] = (df["tire_compound"] != df.groupby("race_entry_id")["tire_compound"].shift()).astype(int)
    df["stint_id"] = df.groupby("race_entry_id")["stint_change"].cumsum()
    df["stint_lap_count"] = df.groupby(["race_entry_id", "stint_id"])["lap_number"].transform("count")
    long_runs = df[df["stint_lap_count"] >= MIN_LONG_RUN_LAPS].copy()
    long_runs["lap_rank_in_stint"] = long_runs.groupby(["race_entry_id", "stint_id"]).cumcount()
    long_runs = long_runs[long_runs["lap_rank_in_stint"] > 0]

    if len(long_runs) == 0:
        return {"consistent": None, "note": "No prior years' dry FP2 long runs at this track to compare against."}

    def clip_outliers(g):
        lo, hi = g["lap_time"].quantile([0.05, 0.95])
        return g[(g["lap_time"] >= lo) & (g["lap_time"] <= hi)]
    long_runs = long_runs.groupby("tire_compound", group_keys=False).apply(clip_outliers)

    MIN_MEANINGFUL_MARGIN_SECONDS = 0.5

    hist_pace = long_runs.groupby("tire_compound")["lap_time"].median().to_dict()
    weekend_pace = fp2_result["compound_pace_ranking"]

    compounds_in_both = [c for c in ('SOFT', 'MEDIUM', 'HARD') if c in hist_pace and c in weekend_pace]

    disagreements = []
    checked_pairs = 0
    for i in range(len(compounds_in_both)):
        for j in range(i + 1, len(compounds_in_both)):
            a, b = compounds_in_both[i], compounds_in_both[j]
            hist_gap = hist_pace[b] - hist_pace[a]
            weekend_gap = weekend_pace[b] - weekend_pace[a]
            if abs(hist_gap) < MIN_MEANINGFUL_MARGIN_SECONDS or abs(weekend_gap) < MIN_MEANINGFUL_MARGIN_SECONDS:
                continue
            checked_pairs += 1
            if (hist_gap > 0) != (weekend_gap > 0):
                disagreements.append((a, b))

    if checked_pairs == 0:
        return {
            "consistent": None,
            "note": "All compound pace gaps are within noise margin (<0.5s) both historically and this "
                    "weekend -- too close to call a meaningful ordering either way.",
        }

    consistent = len(disagreements) == 0
    return {
        "consistent": consistent,
        "historical_fp2_pace": hist_pace,
        "this_weekend_fp2_pace": weekend_pace,
        "meaningful_pairs_checked": checked_pairs,
        "note": "Matches prior years' dry FP2 pattern at this track (checked pairs with a real, non-noise margin)." if consistent else
                f"Disagreement on {disagreements} -- a genuinely meaningful (>0.5s) pace-order flip vs. "
                f"prior years, worth a closer look rather than dismissing as noise.",
    }


if __name__ == "__main__":
    import argparse
    from datetime import date
    parser = argparse.ArgumentParser()
    parser.add_argument("--track_id", type=int, required=True)
    parser.add_argument("--race_date", type=str, required=True)
    args = parser.parse_args()

    fp2_result = get_fp2_compound_pace(args.track_id, date.fromisoformat(args.race_date))
    print(fp2_result)
    if fp2_result["status"] == "ok":
        comparison = compare_to_historical_expectation(fp2_result, args.track_id, date.fromisoformat(args.race_date))
        print(comparison)
