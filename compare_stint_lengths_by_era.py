import os
import pandas as pd
from sqlalchemy import create_engine, text

engine = create_engine(os.environ["DATABASE_URL"])

with engine.connect() as conn:
    df = pd.read_sql(text("""
        SELECT t.name AS track, r.season_year, rs.compound, rs.stint_length
        FROM race_stints rs
        JOIN races r ON r.id = rs.race_id
        JOIN tracks t ON t.id = r.track_id
        WHERE r.season_year IN (2021, 2022)
          AND rs.compound IN ('SOFT', 'MEDIUM', 'HARD')
    """), conn)

tracks_both = df.groupby("track")["season_year"].nunique()
common = tracks_both[tracks_both == 2].index.tolist()
df = df[df["track"].isin(common)]

summary = df.groupby(["compound", "season_year"])["stint_length"].median().unstack()
summary["shift"] = summary[2022] - summary[2021]
summary["pct_change"] = round(100 * summary["shift"] / summary[2021], 1)

print("Median real stint length by compound, 2021 (old regs) vs 2022 (new regs):")
print(summary)
