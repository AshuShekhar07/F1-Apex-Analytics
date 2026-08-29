import os
import time
import fastf1
import pandas as pd
from sqlalchemy import create_engine, text

engine = create_engine(os.environ["DATABASE_URL"])

with engine.connect() as conn:
    wet_sessions = conn.execute(text("""
        SELECT sw.session_id, r.season_year, r.round_number
        FROM session_weather sw
        JOIN sessions s ON s.id = sw.session_id
        JOIN races r ON r.id = s.race_id
        WHERE sw.rainfall = true AND sw.rain_onset_lap IS NULL
        ORDER BY r.season_year, r.round_number
    """)).mappings().all()

print(f"{len(wet_sessions)} wet sessions need rain-onset-lap backfill")

with engine.connect() as conn:
    for i, ws in enumerate(wet_sessions):
        year, rnd = ws["season_year"], ws["round_number"]
        try:
            session = fastf1.get_session(year, rnd, "R")
            session.load(laps=True, telemetry=False, weather=True, messages=False)

            wdata = session.weather_data
            rain_samples = wdata[wdata["Rainfall"] == True]
            if len(rain_samples) == 0:
                print(f"  [{i+1}/{len(wet_sessions)}] {year} R{rnd}: no rain samples found despite rainfall flag, skipping")
                continue
            rain_onset_time = rain_samples.iloc[0]["Time"]  # timedelta since session start

            # build lap_number -> average cumulative time mapping across the field
            laps = session.laps
            laps = laps.dropna(subset=["LapTime", "LapNumber"])
            laps = laps.sort_values(["Driver", "LapNumber"])
            laps["CumTime"] = laps.groupby("Driver")["LapTime"].cumsum()
            avg_cum_by_lap = laps.groupby("LapNumber")["CumTime"].mean().sort_index()

            # find the first lap whose average cumulative time has passed the rain onset time
            onset_lap = None
            for lap_num, cum_time in avg_cum_by_lap.items():
                if cum_time >= rain_onset_time:
                    onset_lap = int(lap_num)
                    break
            if onset_lap is None:
                onset_lap = int(avg_cum_by_lap.index.max())  # rain started very late, near race end

            conn.execute(text("""
                UPDATE session_weather SET rain_onset_lap = :lap WHERE session_id = :sid
            """), {"lap": onset_lap, "sid": ws["session_id"]})
            conn.commit()
            print(f"  [{i+1}/{len(wet_sessions)}] {year} R{rnd}: rain onset ~lap {onset_lap}")
        except Exception as e:
            print(f"  [{i+1}/{len(wet_sessions)}] {year} R{rnd}: FAILED ({e})")
        time.sleep(0.5)

print("Done.")
