"""
Apex21 backfill audit script.

Compares what's actually in the PostgreSQL DB against the expected F1 calendar
(2018-2026), accounting for sprint-format weekends which don't have FP2/FP3.
"""

import os
from collections import defaultdict

import fastf1
from sqlalchemy import create_engine, text

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://user:password@localhost:5432/apex21"
)

SEASONS = range(2018, 2027)

STANDARD_SESSIONS = ["FP1", "FP2", "FP3", "Q", "R"]
SPRINT_SESSIONS = ["FP1", "SQ", "S", "Q", "R"]
SPRINT_SESSIONS_LEGACY = ["FP1", "FP2", "Q", "S", "R"]


def get_expected_sessions(year, round_num, event):
    event_format = getattr(event, "EventFormat", "conventional")
    if event_format in ("sprint_shootout", "sprint"):
        if year <= 2022:
            return SPRINT_SESSIONS_LEGACY
        return SPRINT_SESSIONS
    return STANDARD_SESSIONS


def build_expected_calendar():
    expected = {}
    for year in SEASONS:
        try:
            schedule = fastf1.get_event_schedule(year, include_testing=False)
        except Exception as e:
            print(f"  [WARN] Could not fetch schedule for {year}: {e}")
            continue
        for _, event in schedule.iterrows():
            round_num = int(event["RoundNumber"])
            if round_num == 0:
                continue
            expected[(year, round_num)] = {
                "name": event.get("EventName", "Unknown"),
                "sessions": get_expected_sessions(year, round_num, event),
            }
    return expected


def get_actual_sessions(engine):
    query = text("""
        SELECT
            r.season_year,
            r.round_number,
            s.session_type,
            COUNT(l.id) AS lap_count
        FROM races r
        JOIN sessions s ON s.race_id = r.id
        LEFT JOIN laps l ON l.session_id = s.id
        GROUP BY r.season_year, r.round_number, s.session_type
        ORDER BY r.season_year, r.round_number, s.session_type
    """)
    actual = defaultdict(dict)
    with engine.connect() as conn:
        rows = conn.execute(query).fetchall()
        for row in rows:
            season, round_num, session_type, lap_count = row
            actual[(season, round_num)][session_type] = lap_count
    return actual


def main():
    print("Building expected F1 calendar from FastF1 schedule data...")
    expected = build_expected_calendar()

    print("Querying your database for actual stored sessions...")
    engine = create_engine(DATABASE_URL)
    actual = get_actual_sessions(engine)

    print("\n" + "=" * 70)
    print("AUDIT REPORT")
    print("=" * 70)

    missing_report = defaultdict(list)
    empty_report = defaultdict(list)
    present_count = 0
    missing_count = 0
    empty_count = 0

    for (year, round_num), info in sorted(expected.items()):
        event_name = info["name"]
        for session_type in info["sessions"]:
            key = (year, round_num)
            db_sessions = actual.get(key, {})

            if session_type not in db_sessions:
                missing_report[year].append(f"R{round_num} {session_type} ({event_name})")
                missing_count += 1
            elif db_sessions[session_type] == 0:
                empty_report[year].append(f"R{round_num} {session_type} ({event_name}) - 0 laps stored")
                empty_count += 1
            else:
                present_count += 1

    print(f"\nPresent with data: {present_count} sessions")
    print(f"Present but 0 laps stored: {empty_count} sessions")
    print(f"Missing entirely: {missing_count} sessions")

    print("\n--- MISSING SESSIONS BY YEAR ---")
    for year in sorted(missing_report.keys()):
        print(f"\n{year}: {len(missing_report[year])} missing")
        for item in missing_report[year][:20]:
            print(f"  - {item}")
        if len(missing_report[year]) > 20:
            print(f"  ... and {len(missing_report[year]) - 20} more")

    print("\n--- SESSIONS WITH 0 LAPS STORED (potential silent failures) ---")
    for year in sorted(empty_report.keys()):
        if empty_report[year]:
            print(f"\n{year}: {len(empty_report[year])} empty")
            for item in empty_report[year][:20]:
                print(f"  - {item}")

    print("\n" + "=" * 70)
    print("Done. Cross-check 'MISSING' entries against sprint-weekend logic")
    print("before assuming they're real gaps.")
    print("=" * 70)


if __name__ == "__main__":
    main()
