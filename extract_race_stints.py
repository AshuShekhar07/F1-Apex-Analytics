import os
import pandas as pd
from sqlalchemy import create_engine, text

engine = create_engine(os.environ["DATABASE_URL"])

with engine.connect() as conn:
    df = pd.read_sql(text("""
        SELECT l.race_entry_id, l.lap_number, l.tire_compound,
               rr.finishing_position, l.session_id, s.race_id
        FROM laps l
        JOIN sessions s ON s.id = l.session_id
        JOIN race_results rr ON rr.race_entry_id = l.race_entry_id AND rr.session_id = l.session_id
        WHERE s.session_type = 'R'
          AND l.tire_compound IS NOT NULL
          AND l.tire_compound NOT IN ('None', 'nan')
        ORDER BY l.race_entry_id, l.lap_number
    """), conn)

print(f"Loaded {len(df)} laps with results attached")

df["stint_change"] = (
    (df["tire_compound"] != df.groupby("race_entry_id")["tire_compound"].shift())
).astype(int)
df["stint_number"] = df.groupby("race_entry_id")["stint_change"].cumsum()

stints = df.groupby(["race_id", "race_entry_id", "finishing_position", "stint_number", "tire_compound"]).agg(
    start_lap=("lap_number", "min"),
    end_lap=("lap_number", "max"),
).reset_index()
stints["stint_length"] = stints["end_lap"] - stints["start_lap"] + 1
stints = stints.rename(columns={"tire_compound": "compound"})

print(f"Extracted {len(stints)} stints across {stints['race_entry_id'].nunique()} race entries")

with engine.connect() as conn:
    conn.execute(text("DELETE FROM race_stints"))  # safe to fully rebuild, this is derived data
    conn.commit()
    stints.to_sql("race_stints", conn, if_exists="append", index=False,
                   method="multi", chunksize=500)
    conn.commit()

print("Done.")
