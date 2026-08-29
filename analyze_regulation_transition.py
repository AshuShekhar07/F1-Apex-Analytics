import os
import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

engine = create_engine(os.environ["DATABASE_URL"])

with engine.connect() as conn:
    df = pd.read_sql(text("""
        SELECT l.session_id, l.race_entry_id, l.lap_number, l.lap_time,
               l.tire_compound, r.track_id, t.name AS track_name, r.season_year
        FROM laps l
        JOIN sessions s ON s.id = l.session_id
        JOIN races r ON r.id = s.race_id
        JOIN tracks t ON t.id = r.track_id
        WHERE s.session_type = 'R'
          AND r.season_year IN (2021, 2022)
          AND l.is_valid = true
          AND l.lap_time IS NOT NULL
          AND l.tire_compound IN ('SOFT', 'MEDIUM', 'HARD')
        ORDER BY l.session_id, l.race_entry_id, l.lap_number
    """), conn)

# only tracks raced in both years
tracks_both = df.groupby("track_name")["season_year"].nunique()
common = tracks_both[tracks_both == 2].index.tolist()
df = df[df["track_name"].isin(common)]
print(f"Common tracks: {common}\n")

df["stint_change"] = (df["tire_compound"] != df.groupby(["session_id", "race_entry_id"])["tire_compound"].shift()).astype(int)
df["stint_id"] = df.groupby(["session_id", "race_entry_id"])["stint_change"].cumsum()
df["stint_lap_index"] = df.groupby(["session_id", "race_entry_id", "stint_id"]).cumcount() + 1
df = df[df["stint_lap_index"] > 1].copy()
df["stint_lap_index"] -= 1

def clip_group(g):
    lo, hi = g["lap_time"].quantile([0.05, 0.95])
    return g[(g["lap_time"] >= lo) & (g["lap_time"] <= hi)]
df = df.groupby(["track_name", "tire_compound", "season_year"], group_keys=False).apply(clip_group)

results = []
for (track, compound, year), g in df.groupby(["track_name", "tire_compound", "season_year"]):
    if len(g) < 15:
        continue
    X = np.column_stack([g["stint_lap_index"].values.astype(float), g["lap_number"].values.astype(float), np.ones(len(g))])
    y = g["lap_time"].values.astype(float)
    coeffs, _, rank, _ = np.linalg.lstsq(X, y, rcond=None)
    if rank < 3:
        continue
    results.append({"track": track, "compound": compound, "year": year, "degradation_rate": round(coeffs[0], 4), "n": len(g)})

res_df = pd.DataFrame(results)
pivot = res_df.pivot_table(index=["track", "compound"], columns="year", values="degradation_rate")
pivot = pivot.dropna()
pivot["shift"] = pivot[2022] - pivot[2021]

print(pivot)
print(f"\nAverage shift (2022 minus 2021): {pivot['shift'].mean():.4f} sec/lap")
print(f"Median shift: {pivot['shift'].median():.4f} sec/lap")
