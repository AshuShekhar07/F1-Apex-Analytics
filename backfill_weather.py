import os
import time
import fastf1
import pandas as pd
from sqlalchemy import create_engine, text

engine = create_engine(os.environ["DATABASE_URL"])

with engine.connect() as conn:
    sessions = conn.execute(text("""
        SELECT s.id AS session_id, r.season_year, r.round_number, s.session_type
        FROM sessions s
        JOIN races r ON r.id = s.race_id
        LEFT JOIN session_weather sw ON sw.session_id = s.id
        WHERE s.session_type = 'R'
          AND sw.session_id IS NULL
          AND r.race_date <= CURRENT_DATE
        ORDER BY r.season_year, r.round_number
    """)).mappings().all()

print(f"{len(sessions)} race sessions need weather backfill")

with engine.connect() as conn:
    for i, s in enumerate(sessions):
        year, rnd, sess_type = s["season_year"], s["round_number"], s["session_type"]
        try:
            session = fastf1.get_session(year, rnd, sess_type)
            session.load(laps=False, telemetry=False, weather=True, messages=False)
            wdata = session.weather_data
            if wdata is None or len(wdata) == 0:
                print(f"  [{i+1}/{len(sessions)}] {year} R{rnd}: no weather data available, skipping")
                continue

            air_temp = float(wdata["AirTemp"].mean())
            track_temp = float(wdata["TrackTemp"].mean())
            humidity = float(wdata["Humidity"].mean())
            wind_speed = float(wdata["WindSpeed"].mean())
            rainfall = bool(wdata["Rainfall"].any())

            conn.execute(text("""
                INSERT INTO session_weather (session_id, air_temp_avg, track_temp_avg, humidity_avg, rainfall, wind_speed_avg)
                VALUES (:s, :a, :t, :h, :r, :w)
                ON CONFLICT (session_id) DO UPDATE SET
                    air_temp_avg = EXCLUDED.air_temp_avg,
                    track_temp_avg = EXCLUDED.track_temp_avg,
                    humidity_avg = EXCLUDED.humidity_avg,
                    rainfall = EXCLUDED.rainfall,
                    wind_speed_avg = EXCLUDED.wind_speed_avg
            """), {"s": s["session_id"], "a": round(air_temp,1), "t": round(track_temp,1),
                   "h": round(humidity,1), "r": rainfall, "w": round(wind_speed,1)})
            conn.commit()
            print(f"  [{i+1}/{len(sessions)}] {year} R{rnd}: OK (track {track_temp:.1f}C, rain={rainfall})")
        except Exception as e:
            print(f"  [{i+1}/{len(sessions)}] {year} R{rnd}: FAILED ({e})")
        time.sleep(0.3)

print("Done.")
