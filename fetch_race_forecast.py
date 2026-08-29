import os
import sys
import argparse
import requests
from datetime import date, timedelta
from sqlalchemy import create_engine, text

engine = create_engine(os.environ["DATABASE_URL"])

RAIN_PROB_THRESHOLD = 40  # percent, above which we treat it as "expected to rain"

def get_track_info(track_id):
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT latitude, longitude, total_race_laps, avg_race_duration_minutes
            FROM tracks WHERE id = :id
        """), {"id": track_id}).mappings().first()
    return row

def get_typical_start_hour_utc(track_id):
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT s.start_time
            FROM sessions s
            JOIN races r ON r.id = s.race_id
            WHERE r.track_id = :tid AND s.session_type = 'R'
            ORDER BY r.season_year DESC LIMIT 1
        """), {"tid": track_id}).mappings().first()
    if row is None:
        return 14  # generic fallback
    return row["start_time"].hour + row["start_time"].minute / 60

def get_track_temp_offset(track_id):
    """Real offset between track and air temp at this specific track, from our own historical data."""
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT AVG(sw.track_temp_avg - sw.air_temp_avg) AS offset
            FROM session_weather sw
            JOIN sessions s ON s.id = sw.session_id
            JOIN races r ON r.id = s.race_id
            WHERE r.track_id = :tid
        """), {"tid": track_id}).mappings().first()
    return float(row["offset"]) if row and row["offset"] is not None else 12.0  # generic fallback

def fetch_forecast(lat, lon, race_date):
    r = requests.get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": lat, "longitude": lon,
        "start_date": race_date.isoformat(), "end_date": race_date.isoformat(),
        "hourly": "temperature_2m,precipitation_probability",
        "timezone": "UTC",
    })
    r.raise_for_status()
    return r.json()["hourly"]

def fetch_climatology(lat, lon, race_date, years_back=5):
    """For races too far out for a real forecast: average the same calendar date
    across past years at this location, using Open-Meteo's historical archive."""
    temps, rain_probs_by_hour = [], {}
    for y in range(race_date.year - years_back, race_date.year):
        try:
            hist_date = race_date.replace(year=y)
        except ValueError:
            continue  # Feb 29 edge case, skip
        r = requests.get("https://archive-api.open-meteo.com/v1/archive", params={
            "latitude": lat, "longitude": lon,
            "start_date": hist_date.isoformat(), "end_date": hist_date.isoformat(),
            "hourly": "temperature_2m,precipitation",
            "timezone": "UTC",
        })
        if r.status_code != 200:
            continue
        data = r.json().get("hourly", {})
        if not data.get("temperature_2m"):
            continue
        temps.extend([t for t in data["temperature_2m"] if t is not None])
        for h, precip in zip(data["time"], data["precipitation"]):
            hour = int(h.split("T")[1].split(":")[0])
            rain_probs_by_hour.setdefault(hour, []).append(1 if precip and precip > 0 else 0)

    avg_temp = sum(temps) / len(temps) if temps else None
    # convert historical rain occurrence rate into a pseudo "probability" per hour
    precip_prob_by_hour = {h: round(100 * sum(v) / len(v)) for h, v in rain_probs_by_hour.items()}
    return avg_temp, precip_prob_by_hour

def predict_race_conditions(track_id, race_date):
    info = get_track_info(track_id)
    if info is None or info["latitude"] is None:
        return {"status": "error", "message": "Track missing lat/lon or race data."}

    lat, lon = float(info["latitude"]), float(info["longitude"])
    total_laps = info["total_race_laps"]
    duration_min = float(info["avg_race_duration_minutes"]) if info["avg_race_duration_minutes"] else 95.0
    start_hour = get_typical_start_hour_utc(track_id)
    temp_offset = get_track_temp_offset(track_id)

    days_out = (race_date - date.today()).days
    tier = "forecast" if 0 <= days_out <= 16 else "climatology"

    if tier == "forecast":
        hourly = fetch_forecast(lat, lon, race_date)
        hours = [h.split("T")[1].split(":")[0] for h in hourly["time"]]
        temp_by_hour = {int(h): t for h, t in zip(hours, hourly["temperature_2m"])}
        precip_by_hour = {int(h): p for h, p in zip(hours, hourly["precipitation_probability"])}
    else:
        avg_temp, precip_by_hour = fetch_climatology(lat, lon, race_date)
        temp_by_hour = {int(start_hour): avg_temp} if avg_temp is not None else {}

    race_end_hour = start_hour + duration_min / 60
    hours_in_race = [h for h in range(int(start_hour), int(race_end_hour) + 1)]

    air_temps_in_window = [temp_by_hour[h] for h in hours_in_race if h in temp_by_hour and temp_by_hour[h] is not None]
    avg_air_temp = sum(air_temps_in_window) / len(air_temps_in_window) if air_temps_in_window else None
    target_track_temp = round(avg_air_temp + temp_offset, 1) if avg_air_temp is not None else None

    rain_expected = False
    rain_onset_lap = None
    for h in hours_in_race:
        prob = precip_by_hour.get(h)
        if prob is not None and prob >= RAIN_PROB_THRESHOLD:
            rain_expected = True
            hours_elapsed = h - start_hour
            frac_through_race = max(0, min(1, hours_elapsed / (duration_min / 60)))
            rain_onset_lap = max(1, round(frac_through_race * total_laps)) if total_laps else None
            break

    return {
        "status": "ok",
        "tier": tier,
        "days_out": days_out,
        "target_track_temp": target_track_temp,
        "rain_expected": rain_expected,
        "rain_onset_lap": rain_onset_lap,
    }

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--track_id", type=int, required=True)
    parser.add_argument("--race_date", type=str, required=True, help="YYYY-MM-DD")
    args = parser.parse_args()

    race_date = date.fromisoformat(args.race_date)
    result = predict_race_conditions(args.track_id, race_date)
    print(result)
