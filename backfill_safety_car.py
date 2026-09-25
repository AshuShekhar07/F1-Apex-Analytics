"""
One-time backfill: fills races.safety_car_periods, vsc_periods, red_flags
using FastF1's session.track_status data.

FastF1 status codes:
  1 = AllClear
  2 = Yellow flag
  4 = Safety Car deployed
  5 = Red flag
  6 = Virtual Safety Car deployed
  7 = VSC ending
"""

import os
import time
import fastf1
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from fastf1_cache import fastf1_cache_dir

load_dotenv()
engine = create_engine(os.getenv('DATABASE_URL'))
fastf1.Cache.enable_cache(fastf1_cache_dir())

API_CALL_COUNT = 0
THROTTLE_EVERY = 400
THROTTLE_SECONDS = 15 * 60
MAX_RETRIES = 3
RETRY_SLEEP_SECONDS = 60 * 60


def api_call(fn, *args, **kwargs):
    global API_CALL_COUNT
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            result = fn(*args, **kwargs)
            API_CALL_COUNT += 1
            if API_CALL_COUNT % THROTTLE_EVERY == 0:
                print(f"  [throttle] {API_CALL_COUNT} calls made, cooling down {THROTTLE_SECONDS//60} min...")
                time.sleep(THROTTLE_SECONDS)
            return result
        except Exception as e:
            if attempt < MAX_RETRIES:
                print(f"  [rate limit] attempt {attempt}/{MAX_RETRIES}, sleeping {RETRY_SLEEP_SECONDS//60} min...")
                time.sleep(RETRY_SLEEP_SECONDS)
            else:
                raise


with engine.connect() as conn:
    races_to_process = conn.execute(text("""
        SELECT r.id AS race_id, r.season_year, r.round_number
        FROM races r
        JOIN sessions s ON s.race_id = r.id AND s.session_type = 'R'
        WHERE r.safety_car_periods IS NULL
        ORDER BY r.season_year, r.round_number
    """)).mappings().all()

    print(f"Found {len(races_to_process)} races needing safety car data.\n")

    for row in races_to_process:
        year, rnd, race_id = row['season_year'], row['round_number'], row['race_id']

        try:
            session = api_call(fastf1.get_session, year, rnd, 'Race')
            api_call(session.load, laps=True, telemetry=False, weather=False, messages=False)
        except Exception as e:
            print(f"  {year} R{rnd}: could not load -- {e}")
            continue

        try:
            status_log = session.track_status
        except Exception as e:
            print(f"  {year} R{rnd}: no track_status available -- {e}")
            continue

        if status_log is None or len(status_log) == 0:
            print(f"  {year} R{rnd}: empty track_status, skipping")
            continue

        sc_count = (status_log['Status'] == '4').sum()
        vsc_count = (status_log['Status'] == '6').sum()
        red_flag_count = (status_log['Status'] == '5').sum()

        conn.execute(text("""
            UPDATE races
            SET safety_car_periods = :sc, vsc_periods = :vsc, red_flags = :rf
            WHERE id = :rid
        """), {"sc": int(sc_count), "vsc": int(vsc_count), "rf": int(red_flag_count), "rid": race_id})
        conn.commit()

        print(f"  {year} R{rnd}: SC={sc_count}, VSC={vsc_count}, RedFlags={red_flag_count}")

    print("\nDone.")
