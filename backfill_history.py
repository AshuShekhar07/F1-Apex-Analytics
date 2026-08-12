import os
import time
import fastf1
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()
engine = create_engine(os.getenv('DATABASE_URL'))

# IMPORTANT: use the absolute path you already confirmed works
CACHE_DIR = '/home/ashushekhar07/projects/F1-Apex-Analytics/cache'
fastf1.Cache.enable_cache(CACHE_DIR)

# Map FastF1's real session display names (from the event schedule
# itself) to our short DB codes. This is more robust than hardcoding a
# fixed list per format, because the exact name/order has changed
# across seasons (e.g. 2023 called it "Sprint Shootout" and ran
# Qualifying BEFORE it; 2024+ renamed it "Sprint Qualifying" and
# reordered it before Sprint).
SESSION_NAME_TO_CODE = {
    'Practice 1': 'FP1',
    'Practice 2': 'FP2',
    'Practice 3': 'FP3',
    'Qualifying': 'Q',
    'Sprint Qualifying': 'SQ',
    'Sprint Shootout': 'SQ',
    'Sprint': 'S',
    'Race': 'R',
}

def get_event_sessions(event):
    """Return [(fetch_identifier, db_code), ...] in the event's real order,
    reading directly from the schedule's Session1..Session5 fields."""
    result = []
    for i in range(1, 6):
        name = event.get(f'Session{i}')
        if not name or not isinstance(name, str):
            continue
        code = SESSION_NAME_TO_CODE.get(name)
        if code is None:
            print(f"  [WARN] Unrecognized session name '{name}', skipping")
            continue
        result.append((name, code))
    return result


# --- rate limit handling -----------------------------------------------
API_CALL_COUNT = 0
THROTTLE_EVERY = 400          # cool down before hitting the 500/h ceiling
THROTTLE_SECONDS = 15 * 60    # 15 min cooldown
MAX_RETRIES = 3
RETRY_SLEEP_SECONDS = 60 * 60  # 1 hour, matches the API's rolling window


def api_call(fn, *args, **kwargs):
    """Wrap any fastf1 call with retry-on-rate-limit + proactive throttle."""
    global API_CALL_COUNT
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            result = fn(*args, **kwargs)
            API_CALL_COUNT += 1
            if API_CALL_COUNT % THROTTLE_EVERY == 0:
                print(f"  [throttle] {API_CALL_COUNT} calls made, "
                      f"cooling down {THROTTLE_SECONDS // 60} min...")
                time.sleep(THROTTLE_SECONDS)
            return result
        except Exception as e:
            if "500 calls/h" in str(e) or "rate" in str(e).lower():
                print(f"  [rate limit] attempt {attempt}/{MAX_RETRIES}, "
                      f"sleeping {RETRY_SLEEP_SECONDS // 60} min...")
                time.sleep(RETRY_SLEEP_SECONDS)
            else:
                raise
    raise RuntimeError(f"Gave up after {MAX_RETRIES} retries: {fn}")


def to_seconds(value):
    return value.total_seconds() if pd.notna(value) else None


def get_or_create_track(conn, name, country):
    tid = conn.execute(text("SELECT id FROM tracks WHERE name = :name"), {"name": name}).scalar()
    if tid is None:
        tid = conn.execute(text("INSERT INTO tracks (name, country) VALUES (:name, :country) RETURNING id"),
                            {"name": name, "country": country}).scalar()
    return tid


def get_or_create_race(conn, track_id, year, round_num, race_date):
    rid = conn.execute(text("SELECT id FROM races WHERE season_year = :y AND round_number = :r"),
                        {"y": year, "r": round_num}).scalar()
    if rid is None:
        rid = conn.execute(text("""
            INSERT INTO races (track_id, season_year, round_number, race_date)
            VALUES (:t, :y, :r, :d) RETURNING id
        """), {"t": track_id, "y": year, "r": round_num, "d": race_date}).scalar()
    return rid


def get_or_create_team(conn, name):
    tid = conn.execute(text("SELECT id FROM teams WHERE name = :name"), {"name": name}).scalar()
    if tid is None:
        tid = conn.execute(text("INSERT INTO teams (name) VALUES (:name) RETURNING id"), {"name": name}).scalar()
    return tid


def get_or_create_driver(conn, name, number):
    did = conn.execute(text("SELECT id FROM drivers WHERE name = :name"), {"name": name}).scalar()
    if did is None:
        did = conn.execute(text("INSERT INTO drivers (name, permanent_number) VALUES (:n, :num) RETURNING id"),
                            {"n": name, "num": number}).scalar()
    return did


def get_or_create_entry(conn, race_id, driver_id, team_id, car_number):
    eid = conn.execute(text("""
        SELECT id FROM race_entries WHERE race_id = :r AND driver_id = :d
    """), {"r": race_id, "d": driver_id}).scalar()
    if eid is None:
        eid = conn.execute(text("""
            INSERT INTO race_entries (race_id, driver_id, team_id, car_number)
            VALUES (:r, :d, :t, :c) RETURNING id
        """), {"r": race_id, "d": driver_id, "t": team_id, "c": car_number}).scalar()
    return eid


def year_fully_done(conn, year, num_rounds):
    """Skip hitting the API for a year's schedule if every round/session
    combo is already recorded, so resumed runs don't waste calls."""
    count = conn.execute(text("""
        SELECT COUNT(*) FROM sessions s
        JOIN races r ON r.id = s.race_id
        WHERE r.season_year = :y
    """), {"y": year}).scalar()
    # rough heuristic: 5 session types * num_rounds expected rows
    return count is not None and num_rounds is not None and count >= num_rounds * 5  # approx avg sessions/round across formats


with engine.connect() as conn:
    for year in range(2018, 2027):
        print(f"\n=== {year} ===")
        try:
            schedule = api_call(fastf1.get_event_schedule, year)
        except Exception as e:
            print(f"  Could not get schedule: {e}")
            continue

        for _, event in schedule.iterrows():
            if event['EventFormat'] == 'testing':
                continue

            round_num = int(event['RoundNumber'])
            track_id = get_or_create_track(conn, event['Location'], event['Country'])
            race_id = get_or_create_race(conn, track_id, year, round_num, event['EventDate'].date())
            conn.commit()

            for sess_identifier, sess_type in get_event_sessions(event):
                existing = conn.execute(text("""
                    SELECT id FROM sessions WHERE race_id = :r AND session_type = :s
                """), {"r": race_id, "s": sess_type}).scalar()
                if existing:
                    continue  # already backfilled, skip without touching the API

                try:
                    session = api_call(fastf1.get_session, year, round_num, sess_identifier)
                    api_call(session.load, telemetry=False, weather=False, messages=False)
                except Exception as e:
                    print(f"  Round {round_num} {sess_type}: unavailable ({e})")
                    continue

                driver_id_map = {}
                for _, row in session.results.iterrows():
                    team_id = get_or_create_team(conn, row['TeamName'])
                    driver_id = get_or_create_driver(conn, row['FullName'], int(row['DriverNumber']))
                    entry_id = get_or_create_entry(conn, race_id, driver_id, team_id, int(row['DriverNumber']))
                    driver_id_map[row['Abbreviation']] = entry_id
                conn.commit()

                session_id = conn.execute(text("""
                    INSERT INTO sessions (race_id, session_type, start_time)
                    VALUES (:r, :s, :t) RETURNING id
                """), {"r": race_id, "s": sess_type, "t": session.date}).scalar()
                conn.commit()

                # --- laps ---------------------------------------------------
                lap_count = 0
                try:
                    laps_data = session.laps
                except Exception as e:
                    print(f"    Laps unavailable: {e}")
                    laps_data = None

                if laps_data is not None:
                    for _, lap in laps_data.iterrows():
                        entry_id = driver_id_map.get(lap['Driver'])
                        if entry_id is None:
                            continue
                        conn.execute(text("""
                            INSERT INTO laps (session_id, race_entry_id, lap_number, lap_time,
                                               sector_1_time, sector_2_time, sector_3_time, tire_compound)
                            VALUES (:s, :e, :n, :lt, :s1, :s2, :s3, :c)
                        """), {
                            "s": session_id, "e": entry_id, "n": int(lap['LapNumber']),
                            "lt": to_seconds(lap['LapTime']), "s1": to_seconds(lap['Sector1Time']),
                            "s2": to_seconds(lap['Sector2Time']), "s3": to_seconds(lap['Sector3Time']),
                            "c": lap.get('Compound')
                        })
                        lap_count += 1
                    conn.commit()

                # --- qualifying results (Q sessions only) -------------------
                if sess_type == 'Q':
                    for _, row in session.results.iterrows():
                        entry_id = driver_id_map.get(row['Abbreviation'])
                        if entry_id is None:
                            continue
                        conn.execute(text("""
                            INSERT INTO qualifying_results (session_id, race_entry_id, q1_time, q2_time, q3_time, final_position)
                            VALUES (:s, :e, :q1, :q2, :q3, :p)
                        """), {
                            "s": session_id, "e": entry_id,
                            "q1": to_seconds(row.get('Q1')), "q2": to_seconds(row.get('Q2')),
                            "q3": to_seconds(row.get('Q3')),
                            "p": int(row['Position']) if pd.notna(row['Position']) else None
                        })
                    conn.commit()

                # --- race results (R sessions only) -------------------------
                if sess_type == 'R':
                    for _, row in session.results.iterrows():
                        entry_id = driver_id_map.get(row['Abbreviation'])
                        if entry_id is None:
                            continue
                        conn.execute(text("""
                            INSERT INTO race_results (session_id, race_entry_id, finishing_position, points, status)
                            VALUES (:s, :e, :p, :pts, :st)
                        """), {
                            "s": session_id, "e": entry_id,
                            "p": int(row['Position']) if pd.notna(row['Position']) else None,
                            "pts": float(row['Points']), "st": row['Status']
                        })
                    conn.commit()

                print(f"  Round {round_num} {sess_type}: {len(driver_id_map)} entries, {lap_count} laps")
