from datetime import date

from sqlalchemy import create_engine, text

import strategy_model_v5 as model
from strategy_production_v6 import (
    find_race,
    get_nominations,
    get_stint_plan,
)

engine = create_engine(__import__("os").environ["DATABASE_URL"])

TARGET_DATE = date(2024, 7, 7)


def q(sql, params=None):
    with engine.connect() as c:
        return c.execute(
            text(sql),
            params or {},
        ).mappings().all()


# ------------------------------------------------------------
# Exact Silverstone race
# ------------------------------------------------------------
target = q("""
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
    JOIN tracks t
      ON t.id = r.track_id
    WHERE r.race_date = :d
      AND LOWER(t.name) LIKE '%silverstone%'
    LIMIT 1
""", {"d": TARGET_DATE})[0]

print("TARGET:", dict(target))


# ------------------------------------------------------------
# Actual race weather from DB
# ------------------------------------------------------------
weather = q("""
    SELECT
        sw.air_temp_avg,
        sw.track_temp_avg,
        sw.humidity_avg,
        sw.wind_speed_avg,
        sw.rainfall,
        sw.rain_onset_lap
    FROM sessions s
    JOIN session_weather sw
      ON sw.session_id = s.id
    WHERE s.race_id = :rid
      AND s.session_type = 'R'
    LIMIT 1
""", {"rid": target["id"]})[0]

print("\nACTUAL RACE WEATHER:", dict(weather))


# ------------------------------------------------------------
# Actual winner strategy
# ------------------------------------------------------------
actual_rows = q("""
    SELECT
        rs.stint_number,
        rs.compound,
        rs.start_lap,
        rs.end_lap
    FROM race_stints rs
    JOIN sessions s
      ON s.race_id = rs.race_id
     AND s.session_type = 'R'
    JOIN race_results rr
      ON rr.race_entry_id = rs.race_entry_id
     AND rr.session_id = s.id
    WHERE rs.race_id = :rid
      AND rs.finishing_position = 1
      AND rr.status IN (
          'Finished',
          '+1 Lap',
          '+2 Laps',
          '+3 Laps',
          '+4 Laps',
          '+5 Laps',
          '+6 Laps'
      )
    ORDER BY rs.stint_number
""", {"rid": target["id"]})

print("\nACTUAL WINNER STRATEGY:")
print([
    {
        "compound": str(x["compound"]).upper(),
        "start": x["start_lap"],
        "end": x["end_lap"],
    }
    for x in actual_rows
])


# ------------------------------------------------------------
# Load model data
# ------------------------------------------------------------
races = model.load_races()
nominations = model.load_nominations()
strategies = model.load_winner_strategies()

fp2 = model.build_fp2_features(
    model.load_fp2()
)

features = {
    rid: model.race_features(
        race,
        nominations,
        fp2,
    )
    for rid, race in races.items()
}


# ------------------------------------------------------------
# IMPORTANT:
# For this historical sanity test, use actual race weather.
# This is NOT a production forecast test.
# ------------------------------------------------------------
target_weather = {
    "track_temp": float(
        weather["track_temp_avg"]
    )
    if weather["track_temp_avg"] is not None
    else None,

    "air_temp": float(
        weather["air_temp_avg"]
    )
    if weather["air_temp_avg"] is not None
    else None,

    "humidity": float(
        weather["humidity_avg"]
    )
    if weather["humidity_avg"] is not None
    else None,

    "wind_speed": float(
        weather["wind_speed_avg"]
    )
    if weather["wind_speed_avg"] is not None
    else None,

    "rain_expected": bool(
        weather["rainfall"]
    ),

    "rain_onset_lap": (
        float(weather["rain_onset_lap"])
        if weather["rain_onset_lap"] is not None
        else None
    ),
}

features[target["id"]] = model.race_features(
    target,
    nominations,
    fp2,
    weather_override=target_weather,
)


# ------------------------------------------------------------
# STRICT PRE-TARGET HISTORY
# ------------------------------------------------------------
prior_ids = [
    rid
    for rid, race in races.items()
    if (
        race["race_date"] < TARGET_DATE
        and race["regulation_era"]
        == target["regulation_era"]
        and rid in strategies
    )
]

prior = {
    rid: races[rid]
    for rid in prior_ids
}


scales = {
    key: model.robust_scale([
        f.get(key)
        for f in features.values()
    ])
    for key in model.FP2_NUMERIC
}


ranked, analogs, stop_evidence = (
    model.candidate_strategies(
        target,
        prior,
        features,
        nominations,
        strategies,
        scales,
    )
)


print("\n================================")
print("HISTORICAL V6 SANITY CHECK")
print("================================")

print(
    "prior historical races:",
    len(prior_ids)
)

print(
    "condition-matched analogs:",
    len(analogs)
)

print("\nTOP STRATEGIES:")

for x in ranked[:8]:
    print(
        x["sequence"],
        "score=",
        round(x["score"], 4),
        "races=",
        x["races"],
    )


print("\nTOP ANALOGS:")

for x in analogs[:10]:
    print({
        "race_id": x["race_id"],
        "sequence": x["sequence"],
        "distance": round(x["distance"], 3),
        "weight": round(x["weight"], 4),
    })


actual_sequence = tuple(
    str(x["compound"]).upper()
    for x in actual_rows
)

ranking = [
    tuple(x["sequence"])
    for x in ranked
]

try:
    actual_rank = ranking.index(
        actual_sequence
    ) + 1
except ValueError:
    actual_rank = None

print("\nACTUAL SEQUENCE:", actual_sequence)
print("ACTUAL RANK:", actual_rank)

primary = (
    tuple(ranked[0]["sequence"])
    if ranked
    else None
)

print("PRIMARY:", primary)
print(
    "TOP-1 MATCH:",
    primary == actual_sequence,
)
print(
    "TOP-3 CONTAINS ACTUAL:",
    actual_rank is not None
    and actual_rank <= 3,
)


# ------------------------------------------------------------
# Exact supporting races for actual strategy
# ------------------------------------------------------------
print("\nSUPPORTING RACES FOR ACTUAL STRATEGY:")

for rid, race in prior.items():
    if rid not in strategies:
        continue

    seq = tuple(
        x["compound"]
        for x in strategies[rid]
    )

    if seq == actual_sequence:
        print(
            rid,
            race["race_date"],
            "track=",
            race["track_id"],
            strategies[rid],
        )
