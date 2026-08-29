import os
import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

engine = create_engine(os.environ["DATABASE_URL"])

MIN_SAMPLES = 15  # multivariate fit needs more data than the single-variable version did

with engine.connect() as conn:
    df = pd.read_sql(text("""
        SELECT l.session_id, l.race_entry_id, l.lap_number, l.lap_time,
               l.tire_compound, r.track_id
        FROM laps l
        JOIN sessions s ON s.id = l.session_id
        JOIN races r ON r.id = s.race_id
        WHERE s.session_type = 'R'
          AND l.is_valid = true
          AND l.lap_time IS NOT NULL
          AND l.tire_compound IS NOT NULL
          AND l.tire_compound NOT IN ('None', 'nan')
        ORDER BY l.session_id, l.race_entry_id, l.lap_number
    """), conn)

print(f"Loaded {len(df)} valid race laps")

df["stint_change"] = (
    (df["tire_compound"] != df.groupby(["session_id", "race_entry_id"])["tire_compound"].shift())
).astype(int)
df["stint_id"] = df.groupby(["session_id", "race_entry_id"])["stint_change"].cumsum()

df["stint_lap_index"] = df.groupby(["session_id", "race_entry_id", "stint_id"]).cumcount() + 1
df = df[df["stint_lap_index"] > 1].copy()
df["stint_lap_index"] = df["stint_lap_index"] - 1

def clip_group(g):
    lo, hi = g["lap_time"].quantile([0.05, 0.95])
    return g[(g["lap_time"] >= lo) & (g["lap_time"] <= hi)]

df = df.groupby(["track_id", "tire_compound"], group_keys=False).apply(clip_group, include_groups=False).join(
    df[["track_id", "tire_compound"]], how="left"
) if False else df.groupby(["track_id", "tire_compound"], group_keys=False).apply(clip_group)

results = []
for (track_id, compound), g in df.groupby(["track_id", "tire_compound"]):
    if len(g) < MIN_SAMPLES:
        continue
    # two-variable OLS: lap_time ~ stint_lap_index + race_lap_number + intercept
    # stint_lap_index isolates pure tire wear; race_lap_number absorbs fuel burn
    # and track evolution together, whatever their combined true shape is,
    # instead of us guessing a fixed constant for either.
    X = np.column_stack([
        g["stint_lap_index"].values.astype(float),
        g["lap_number"].values.astype(float),
        np.ones(len(g)),
    ])
    y = g["lap_time"].values.astype(float)
    coeffs, residuals, rank, sv = np.linalg.lstsq(X, y, rcond=None)
    stint_coef, race_coef, intercept = coeffs

    if rank < 3:
        continue  # not enough independent variation to separate the two effects safely

    mean_race_lap = g["lap_number"].mean()
    baseline_pace = intercept + stint_coef * 1 + race_coef * mean_race_lap

    results.append({
        "track_id": int(track_id),
        "compound": compound,
        "baseline_pace_seconds": round(float(baseline_pace), 3),
        "degradation_rate_seconds_per_lap": round(float(stint_coef), 4),
        "sample_size": len(g),
    })

print(f"Fit {len(results)} (track, compound) curves")

with engine.connect() as conn:
    for r in results:
        conn.execute(text("""
            INSERT INTO tire_degradation_curves
                (track_id, compound, baseline_pace_seconds, degradation_rate_seconds_per_lap, sample_size)
            VALUES (:t, :c, :b, :d, :n)
            ON CONFLICT (track_id, compound) DO UPDATE SET
                baseline_pace_seconds = EXCLUDED.baseline_pace_seconds,
                degradation_rate_seconds_per_lap = EXCLUDED.degradation_rate_seconds_per_lap,
                sample_size = EXCLUDED.sample_size
        """), {"t": r["track_id"], "c": r["compound"], "b": r["baseline_pace_seconds"],
               "d": r["degradation_rate_seconds_per_lap"], "n": r["sample_size"]})
    conn.commit()

print("Done.")
