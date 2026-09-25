"""Synthetic fixture rows for API contract tests.

EVERYTHING HERE IS SYNTHETIC. Names ("Driver A", "Team Alpha", "Test Circuit
One") and numbers are invented to exercise SQL logic (session filters, sprint
vs race separation, gaps-and-islands, ranking) and are never loaded into the
real database. Real-world facts are checked separately in tests/real_db/.

Layout:
  2021 R1  Test Circuit One  conventional   Alpha: A, B   Beta: C, D   (+ reserve R in FP1)
  2021 R2  Test Circuit Two  sprint_legacy  wet race, dry sprint
  2022 R1  Test Circuit One  conventional   Alpha: A, C   Beta: B, D
  2023 R1  Test Circuit Two  conventional   Alpha: A, B   Beta: C, D
  2022 R24 Test Circuit Two  conventional   scheduled, no entries/results
"""

from datetime import date, datetime

from sqlalchemy import text

TRACKS = [
    (1, "Test Circuit One", "Testland", 5.0),
    (2, "Test Circuit Two", "Testland", 4.0),
]
TEAMS = [(1, "Team Alpha", "#111111"), (2, "Team Beta", "#222222")]
DRIVERS = [(1, "Driver A"), (2, "Driver B"), (3, "Driver C"), (4, "Driver D"), (5, "Driver R")]

# race_id: (track_id, season, round, date, weekend_format, era)
RACES = {
    1: (1, 2021, 1, date(2021, 3, 28), "conventional", "era1_13inch"),
    2: (2, 2021, 2, date(2021, 4, 18), "sprint_legacy", "era1_13inch"),
    3: (1, 2022, 1, date(2022, 3, 20), "conventional", "era2_18inch_groundeffect"),
    4: (2, 2023, 1, date(2023, 3, 5), "conventional", "era2_18inch_groundeffect"),
    5: (2, 2022, 24, date(2022, 11, 20), "conventional", "era2_18inch_groundeffect"),
}

# race_id: {driver_id: (team_id, role)}
ENTRIES = {
    1: {1: (1, "race_driver"), 2: (1, "race_driver"), 3: (2, "race_driver"), 4: (2, "race_driver"), 5: (1, "reserve")},
    2: {1: (1, "race_driver"), 2: (1, "race_driver"), 3: (2, "race_driver"), 4: (2, "race_driver")},
    3: {1: (1, "race_driver"), 3: (1, "race_driver"), 2: (2, "race_driver"), 4: (2, "race_driver")},
    4: {1: (1, "race_driver"), 2: (1, "race_driver"), 3: (2, "race_driver"), 4: (2, "race_driver")},
}

# (race_id, session_type): {driver_id: (finish, grid, points, status)}
RESULTS = {
    (1, "R"): {1: (1, 2, 25, "Finished"), 2: (2, 1, 18, "Finished"), 4: (3, 4, 15, "+1 Lap"), 3: (4, 3, 0, "Engine")},
    (2, "S"): {3: (1, 3, 3, "Finished"), 4: (2, 4, 2, "Finished"), 1: (3, 1, 1, "Finished"), 2: (4, 2, 0, "Finished")},
    (2, "R"): {1: (1, 3, 25, "Finished"), 3: (2, 1, 18, "Finished"), 2: (3, 2, 15, "Finished"), 4: (4, 4, 12, "Finished")},
    (3, "R"): {2: (1, 1, 25, "Finished"), 1: (2, 2, 18, "Finished"), 3: (3, 3, 15, "Finished"), 4: (4, 4, 12, "Finished")},
    (4, "R"): {1: (1, 1, 25, "Finished"), 3: (2, 2, 18, "Finished"), 2: (3, 3, 15, "Finished"), 4: (4, 4, 12, "Finished")},
}

# (race_id): {driver_id: (q1, q2, q3, final_position)}
QUALIFYING = {
    1: {1: (90.5, 90.0, 89.5, 1), 2: (90.6, 90.1, 89.7, 2), 3: (90.7, 90.2, 89.9, 3), 4: (90.8, 90.3, 90.0, 4)},
    2: {2: (80.5, 80.0, 79.5, 1), 1: (80.6, 80.1, 79.6, 2), 3: (80.7, 80.2, 79.7, 3), 4: (80.8, 80.3, 79.8, 4)},
    # positions deliberately sparse to exercise elimination labels:
    # B reached Q3 (P10) but set no Q3 time; C out in Q2; D out in Q1.
    3: {1: (88.0, 87.5, 87.0, 1), 2: (88.1, 87.6, None, 10), 3: (88.2, 87.9, None, 12), 4: (88.9, None, None, 17)},
    4: {1: (81.0, 80.5, 80.0, 1), 2: (81.1, 80.6, 80.1, 2), 3: (81.2, 80.7, 80.2, 3), 4: (81.3, 80.8, 80.3, 4)},
}

# (race_id, session_type): {driver_id: [(lap_number, lap_time, position, is_deleted)]}
LAPS = {
    (1, "R"): {
        1: [(1, 95.0, 2, None), (2, 94.5, 1, None), (3, 94.8, 1, None)],
        2: [(1, 95.1, 1, None), (2, 94.9, 2, None), (3, 95.0, 2, None)],
        4: [(1, 96.0, 4, None), (2, 95.8, 3, None)],
        3: [(1, 95.5, 3, None)],
    },
    (1, "FP1"): {
        1: [(1, 91.0, None, False), (2, 90.2, None, False)],
        2: [(1, 89.9, None, True), (2, 90.4, None, False)],  # fastest lap deleted
        5: [(1, 90.3, None, False)],  # reserve driver
        3: [(1, 91.5, None, None)],  # not enriched: is_deleted NULL still counts
        4: [(1, 92.0, None, None)],
    },
}

WEATHER = {(1, "R"): False, (2, "S"): False, (2, "R"): True, (3, "R"): False, (4, "R"): False}

SESSION_TYPES = {1: ["FP1", "Q", "R"], 2: ["Q", "S", "R"], 3: ["Q", "R"], 4: ["Q", "R"], 5: []}

# (as_of_round, driver_id, pct): next_race rows for 2021.
# as_of_round=1 is the prediction for round 2; as_of_round=2 would be round 3.
NEXT_RACE_PREDICTIONS = [(1, 1, 60.0), (1, 3, 40.0), (2, 3, 70.0), (2, 1, 30.0)]


def seed(conn) -> dict:
    """Insert all fixture rows; returns lookup ids used by the tests."""
    for tid, name, country, length in TRACKS:
        conn.execute(text("INSERT INTO tracks (id, name, country, length_km) VALUES (:i, :n, :c, :l)"),
                     {"i": tid, "n": name, "c": country, "l": length})
    for tid, name, color in TEAMS:
        conn.execute(text("INSERT INTO teams (id, name, color_hex) VALUES (:i, :n, :c)"),
                     {"i": tid, "n": name, "c": color})
    for did, name in DRIVERS:
        conn.execute(text("INSERT INTO drivers (id, name, nationality, permanent_number) VALUES (:i, :n, 'Testish', :i)"),
                     {"i": did, "n": name})
    for rid, (track, season, rnd, rdate, fmt, era) in RACES.items():
        conn.execute(text("""
            INSERT INTO races (id, track_id, season_year, round_number, race_date, weekend_format, regulation_era, winner_time_seconds)
            VALUES (:i, :t, :s, :r, :d, :f, :e, 5400.123)
        """), {"i": rid, "t": track, "s": season, "r": rnd, "d": rdate, "f": fmt, "e": era})

    entry_ids = {}
    for rid, drivers in ENTRIES.items():
        for did, (team, role) in drivers.items():
            entry_ids[(rid, did)] = conn.execute(text("""
                INSERT INTO race_entries (race_id, driver_id, team_id, role, car_number)
                VALUES (:r, :d, :t, :role, :d) RETURNING id
            """), {"r": rid, "d": did, "t": team, "role": role}).scalar()

    session_ids = {}
    for rid, types in SESSION_TYPES.items():
        for hour, stype in enumerate(types):
            session_ids[(rid, stype)] = conn.execute(text("""
                INSERT INTO sessions (race_id, session_type, start_time) VALUES (:r, :s, :t) RETURNING id
            """), {"r": rid, "s": stype, "t": datetime(RACES[rid][3].year, 1, 1, 10 + hour)}).scalar()

    for (rid, stype), rows in RESULTS.items():
        for did, (finish, grid, points, status) in rows.items():
            conn.execute(text("""
                INSERT INTO race_results (session_id, race_entry_id, finishing_position, starting_grid_position, points, status)
                VALUES (:s, :e, :f, :g, :p, :st)
            """), {"s": session_ids[(rid, stype)], "e": entry_ids[(rid, did)], "f": finish, "g": grid, "p": points, "st": status})

    for rid, rows in QUALIFYING.items():
        for did, (q1, q2, q3, pos) in rows.items():
            conn.execute(text("""
                INSERT INTO qualifying_results (session_id, race_entry_id, q1_time, q2_time, q3_time, final_position)
                VALUES (:s, :e, :q1, :q2, :q3, :p)
            """), {"s": session_ids[(rid, "Q")], "e": entry_ids[(rid, did)], "q1": q1, "q2": q2, "q3": q3, "p": pos})

    for (rid, stype), by_driver in LAPS.items():
        for did, laps in by_driver.items():
            for lap_number, lap_time, position, deleted in laps:
                conn.execute(text("""
                    INSERT INTO laps (session_id, race_entry_id, lap_number, lap_time, sector_1_time,
                                      sector_2_time, sector_3_time, tire_compound, position, is_deleted)
                    VALUES (:s, :e, :n, :t, :t / 3, :t / 3, :t / 3, 'SOFT', :p, :del)
                """), {"s": session_ids[(rid, stype)], "e": entry_ids[(rid, did)], "n": lap_number,
                       "t": lap_time, "p": position, "del": deleted})

    for (rid, stype), wet in WEATHER.items():
        conn.execute(text("INSERT INTO session_weather (session_id, rainfall) VALUES (:s, :w)"),
                     {"s": session_ids[(rid, stype)], "w": wet})

    names = dict(DRIVERS)
    for as_of_round, did, pct in NEXT_RACE_PREDICTIONS:
        conn.execute(text("""
            INSERT INTO season_predictions (season_year, prediction_type, entity_id, entity_name, probability_pct, as_of_round)
            VALUES (2021, 'next_race', :d, :n, :p, :r)
        """), {"d": did, "n": names[did], "p": pct, "r": as_of_round})

    return {"entry_ids": entry_ids, "session_ids": session_ids}
