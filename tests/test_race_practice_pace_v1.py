import pytest
from sqlalchemy import text

from race_practice_pace_v1 import MIN_RUN_LAPS, load_practice_gaps, long_run_laps, practice_gaps
from test_race_strategy_backtest_v2 import synthetic_db  # noqa: F401  (fixture)


def test_heavy_fuel_runs_8_percent_off_the_quali_lap_count_and_traffic_laps_are_trimmed():
    laps = ([(1, 110.0, "SOFT"), (2, 88.0, "SOFT"), (3, 115.0, "SOFT")]      # quali sim
            + [(n, 95.0, "MEDIUM") for n in range(4, 10)]                      # 8% off the quali lap
            + [(10, 95.3, "MEDIUM"), (11, 96.5, "MEDIUM")])                    # +1.6%: within 2%, kept
    runs = long_run_laps(laps)
    assert len(runs["MEDIUM"]) == 8
    trimmed = long_run_laps([(n, 95.0, "HARD") for n in range(1, 7)] + [(7, 97.5, "HARD")])
    assert len(trimmed["HARD"]) == 6                                          # +2.6%: traffic, dropped


def test_long_runs_keep_consecutive_race_trim_laps_only():
    laps = ([(1, 95.0, "SOFT"), (2, 88.0, "SOFT"), (3, 99.0, "SOFT")]          # out, quali sim, in
            + [(4, 100.0, None)]                                              # slow out-lap
            + [(n, 91.0 + 0.05 * (n - 5), "MEDIUM") for n in range(5, 13)]    # 8-lap long run
            + [(13, 101.0, "MEDIUM")]
            + [(14, 91.5, "HARD"), (15, 91.6, "HARD")])                      # too short
    runs = long_run_laps(laps)
    assert set(runs) == {"MEDIUM"} and len(runs["MEDIUM"]) == 8
    # a compound change inside consecutive laps splits the run (3 + 4 laps: both too short)
    assert MIN_RUN_LAPS == 5
    assert long_run_laps([(n, 90.0, "SOFT" if n < 4 else "MEDIUM") for n in range(1, 8)]) == {}


def test_practice_gaps_compare_within_compound_and_rebase():
    runs = {
        1: {"MEDIUM": [90.0] * 6},
        2: {"MEDIUM": [90.9] * 6},                  # 1% off on mediums
        3: {"HARD": [91.0] * 6, "MEDIUM": [90.45] * 6},
        4: {"HARD": [91.91] * 6},                   # 1% off the best hard
        5: {"HARD": [91.2] * 6},
        6: {"HARD": [91.1] * 6, "MEDIUM": [99.0] * 6},   # 10% off: unrepresentative, dropped
    }
    gaps = practice_gaps(runs)
    assert gaps[1] == 0.0
    assert gaps[2] == pytest.approx(0.01, abs=1e-6)
    assert gaps[4] == pytest.approx(0.01, abs=1e-6)
    assert gaps[3] == pytest.approx(0.0025, abs=1e-6)   # mean of 0% (hard) and 0.5% (medium)
    assert 6 not in gaps


def test_compounds_with_too_few_drivers_are_ignored():
    assert practice_gaps({1: {"SOFT": [80.0] * 5}, 2: {"SOFT": [81.0] * 5}}) == {}


def test_load_practice_gaps_from_the_database(synthetic_db):
    with synthetic_db.connect() as db:
        tx = db.begin()
        try:
            fp2 = db.execute(text("INSERT INTO sessions (race_id, session_type, start_time) "
                                  "VALUES (5, 'FP2', now()) RETURNING id")).scalar()
            db.execute(text("INSERT INTO session_weather (session_id, rainfall) VALUES (:s, false)"), {"s": fp2})
            entries = dict(db.execute(text("SELECT driver_id, id FROM race_entries WHERE race_id = 5")).all())
            for driver, entry in entries.items():
                for n in range(1, 9):   # 8-lap long run, driver d is 0.1 s/lap slower than d-1
                    db.execute(text("""INSERT INTO laps (session_id, race_entry_id, lap_number, lap_time, tire_compound)
                                       VALUES (:s, :e, :n, :t, 'MEDIUM')"""),
                               {"s": fp2, "e": entry, "n": n, "t": 92.0 + 0.1 * (driver - 1)})
            gaps, session = load_practice_gaps(db, 5)
            assert session == "FP2" and len(gaps) == len(entries)
            assert gaps[1] == 0.0 and gaps[2] == pytest.approx(0.1 / 92.0)
            db.execute(text("UPDATE session_weather SET rainfall = true WHERE session_id = :s"), {"s": fp2})
            assert load_practice_gaps(db, 5) == ({}, None)          # wet practice is not used
        finally:
            tx.rollback()


def test_weekend_wear_can_ignore_the_rain_flag(synthetic_db):
    from race_practice_pace_v1 import load_weekend_wear

    with synthetic_db.connect() as db:
        tx = db.begin()
        try:
            fp2 = db.execute(text("INSERT INTO sessions (race_id, session_type, start_time) "
                                  "VALUES (5, 'FP2', now()) RETURNING id")).scalar()
            db.execute(text("INSERT INTO session_weather (session_id, rainfall) VALUES (:s, true)"), {"s": fp2})
            entries = dict(db.execute(text("SELECT driver_id, id FROM race_entries WHERE race_id = 5")).all())
            for driver, entry in entries.items():
                for n in range(1, 11):   # 10-lap run, 0.05 s/lap slower each lap
                    db.execute(text("""INSERT INTO laps (session_id, race_entry_id, lap_number, lap_time, tire_compound)
                                       VALUES (:s, :e, :n, :t, 'MEDIUM')"""),
                               {"s": fp2, "e": entry, "n": n, "t": 92.0 + 0.05 * n + 0.01 * driver})
            assert load_weekend_wear(db, 5) is None                         # rain flag: skipped
            assert load_weekend_wear(db, 5, use_rain_flag=False) > 0         # slicks only: measured
            db.execute(text("UPDATE laps SET tire_compound = 'INTERMEDIATE' WHERE session_id = :s"), {"s": fp2})
            assert load_weekend_wear(db, 5, use_rain_flag=False) is None     # wet tyres: still skipped
        finally:
            tx.rollback()


def test_theil_sen_ignores_one_outlier():
    from race_practice_pace_v1 import theil_sen

    xs = [1, 2, 3, 4, 5, 6, 7]
    ys = [90.0 + 0.1 * x for x in xs]
    ys[3] += 2.0                                     # traffic lap
    assert theil_sen(xs, ys) == pytest.approx(0.1)
    assert theil_sen([1, 1], [90.0, 91.0]) is None


def test_run_wear_v2_drops_warm_up_and_adds_back_fuel():
    from race_practice_pace_v1 import FUEL_SECONDS_PER_LAP, run_wear, run_wear_v2

    # true tyre wear 0.08 s/lap, hidden by fuel burn; first lap 1.5 s slow (tyres warming)
    run = [(n, 90.0 + (0.08 - FUEL_SECONDS_PER_LAP) * n + (1.5 if n == 1 else 0.0)) for n in range(1, 11)]
    v2 = run_wear_v2("SOFT", run)
    assert v2 == pytest.approx(0.08 / 90.3, rel=0.01)
    assert run_wear(run) < v2                        # least squares: warm-up lap and fuel pull it down
    assert run_wear_v2("MEDIUM", run[:6]) is None     # 2 warm-up laps dropped -> 4 left, too short


def test_per_compound_wear_compares_like_with_like():
    from race_practice_pace_v1 import compound_baselines, weekend_wear_per_compound, weekend_wear_robust

    history = {r: [("SOFT", 0.002)] * 3 + [("HARD", 0.0005)] * 3 for r in range(1, 9)}
    baselines = compound_baselines(history, range(1, 9))
    assert baselines == {"SOFT": pytest.approx(0.002), "HARD": pytest.approx(0.0005)}
    assert compound_baselines(history, [1]) == {}                       # too few runs for a baseline
    soft_weekend = [("SOFT", 0.002)] * 5                                 # a normal weekend, on softs
    hard_weekend = [("HARD", 0.001)] * 5                                 # hards wearing twice as fast as usual
    assert weekend_wear_robust(soft_weekend) > weekend_wear_robust(hard_weekend)
    assert weekend_wear_per_compound(soft_weekend, baselines) == pytest.approx(1.0)
    assert weekend_wear_per_compound(hard_weekend, baselines) == pytest.approx(2.0)
    assert weekend_wear_per_compound([("SOFT", 0.002)] * 2, baselines) is None
