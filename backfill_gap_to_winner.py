"""
One-time backfill: fills gap_to_winner_seconds / gap_to_winner_display
for race_results rows that already exist but predate the gap-tracking patch.

Re-fetches only session.results (no laps, no telemetry) -- fast and light
on the API compared to a full re-backfill.
"""

import os
import time
import fastf1
import pandas as pd
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
    sessions_to_fix = conn.execute(text("""
        SELECT s.id AS session_id, r.season_year, r.round_number, s.session_type
        FROM sessions s
        JOIN races r ON r.id = s.race_id
        WHERE s.session_type IN ('R', 'S')
        ORDER BY r.season_year, r.round_number, s.session_type
    """)).mappings().all()

    print(f"Found {len(sessions_to_fix)} race/sprint sessions to backfill gap data for.\n")

    session_name_map = {'R': 'Race', 'S': 'Sprint'}

    for row in sessions_to_fix:
        year, rnd, sess_type = row['season_year'], row['round_number'], row['session_type']
        session_id = row['session_id']

        already_has_gap = conn.execute(text("""
            SELECT COUNT(*) FROM race_results
            WHERE session_id = :sid AND gap_to_winner_display IS NOT NULL
        """), {"sid": session_id}).scalar()

        total_results = conn.execute(text("""
            SELECT COUNT(*) FROM race_results WHERE session_id = :sid
        """), {"sid": session_id}).scalar()

        if total_results == 0:
            continue  # no race_results at all for this session, skip

        if already_has_gap and already_has_gap == total_results:
            continue  # already fully backfilled, skip without touching the API

        try:
            session = api_call(fastf1.get_session, year, rnd, session_name_map[sess_type])
            api_call(session.load, laps=False, telemetry=False, weather=False, messages=False)
        except Exception as e:
            print(f"  {year} R{rnd} {sess_type}: could not load -- {e}")
            continue

        winners = session.results[session.results['Position'] == 1]

        updated = 0
        for _, res_row in session.results.iterrows():
            gap_seconds = None
            gap_display = None
            if res_row['Position'] == 1:
                gap_seconds = 0.0
                gap_display = 'Winner'
            elif pd.notna(res_row['Time']):
                gap_seconds = res_row['Time'].total_seconds()
                gap_display = f"+{gap_seconds:.3f}s"
            elif pd.notna(res_row.get('Status')):
                gap_display = res_row['Status']

            result = conn.execute(text("""
                UPDATE race_results rr
                SET gap_to_winner_seconds = :gs, gap_to_winner_display = :gd
                FROM race_entries re, drivers d
                WHERE rr.race_entry_id = re.id
                  AND re.driver_id = d.id
                  AND rr.session_id = :sid
                  AND d.name = :name
            """), {
                "gs": gap_seconds, "gd": gap_display,
                "sid": session_id, "name": res_row['FullName']
            })
            updated += result.rowcount

        conn.commit()
        print(f"  {year} R{rnd} {sess_type}: updated {updated}/{total_results} results")

    print("\nDone.")
