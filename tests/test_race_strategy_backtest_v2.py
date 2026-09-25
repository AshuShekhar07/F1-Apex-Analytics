"""Tests for the layered strategy stack: quali pace, precedent, all-driver backtest."""

import math
import uuid

import numpy as np
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from race_pace_from_quali_v1 import PaceObservation, clean_race_pace, fit_model, quali_gaps, spearman
from race_strategy_backtest_v2 import (
    DriverRow,
    brier,
    grid_slot_rates,
    race_clustered_ci,
    run_backtest,
    strategy_metrics,
    summarise,
)
from race_strategy_precedent_v1 import (
    HistoricalStrategy,
    PrecedentRecord,
    candidates_from_options,
    historical_strategy_options,
    load_precedents,
    precedent_options,
)
from race_strategy_simulator_v1 import Strategy, StrategyStint

MH = ("MEDIUM", "HARD")


def _s(sequence, stops, laps=50):
    bounds = [0, *stops, laps]
    return Strategy("x", tuple(StrategyStint(sequence[i], bounds[i] + 1, bounds[i + 1]) for i in range(len(sequence))))


# --- qualifying pace ------------------------------------------------------------

def test_quali_gaps_put_missing_and_outlier_times_at_the_back():
    gaps = quali_gaps({1: 80.0, 2: 80.8, 3: None, 4: 90.0})
    assert gaps[1] == 0.0 and gaps[2] == pytest.approx(0.01)
    assert gaps[3] == gaps[4] == pytest.approx(0.015)      # beyond 7% or missing -> behind the valid field


def test_clean_race_pace_drops_lap_one_pit_and_sc_laps():
    laps = [(1, 100.0)] + [(n, 90.0) for n in range(2, 14)] + [(14, 112.0), (15, 130.0)]
    assert clean_race_pace(laps) == 90.0
    assert clean_race_pace([(n, 90.0) for n in range(2, 6)]) is None   # too few laps


def test_fit_model_recovers_linear_relation():
    rng = np.random.default_rng(0)
    rows = [PaceObservation(i // 10, 2022, "e", i, g, 0.001 + 0.6 * g + rng.normal(0, 0.0005), 80.0, 84.0)
            for i, g in enumerate(rng.uniform(0, 0.03, 200))]
    model = fit_model(rows)
    assert model.slope == pytest.approx(0.6, abs=0.05)
    assert model.race_to_pole_ratio == pytest.approx(1.05)
    with pytest.raises(ValueError):
        fit_model(rows[:10])
    assert spearman([1, 2, 3], [10, 20, 30]) == pytest.approx(1.0)


# --- precedent ------------------------------------------------------------------

def test_options_rank_by_frequency_with_median_stops():
    history = (
        [HistoricalStrategy(MH, (0.40,)), HistoricalStrategy(MH, (0.50,)), HistoricalStrategy(MH, (0.44,))]
        + [HistoricalStrategy(("SOFT", "HARD"), (0.30,))]
        + [HistoricalStrategy(("HARD",), ())]
        + [HistoricalStrategy(("INTERMEDIATE", "MEDIUM"), (0.2,))]
        + [HistoricalStrategy(("MEDIUM", "MEDIUM"), (0.5,))]
    )
    assert [(s.sequence, s.stop_laps, w) for s, w in historical_strategy_options(history, 50)] == [
        (MH, (22,), 3.0), (("SOFT", "HARD"), (15,), 1.0)]


def test_candidates_shift_historical_stops_and_dedupe():
    candidates = candidates_from_options([(_s(MH, [22]), 3.0), (_s(MH, [22]), 1.0)], 50, offsets=(-3, 0, 3))
    assert [c.stop_laps for c in candidates] == [(19,), (22,), (25,)]


def _rec(year, track, grid, sequence, fractions, era="e"):
    return PrecedentRecord(year, track, era, grid, HistoricalStrategy(sequence, fractions))


def test_precedent_hierarchy_prefers_specific_evidence():
    records = (
        [_rec(2023, 1, 3, MH, (0.4,)) for _ in range(20)]                     # track 1 front runners
        + [_rec(2023, 1, 15, ("HARD", "MEDIUM"), (0.6,)) for _ in range(20)]  # track 1 back of grid
        + [_rec(2023, 2, 5, ("SOFT", "MEDIUM", "HARD"), (0.2, 0.6)) for _ in range(50)]
        + [_rec(2024, 1, 3, ("SOFT", "HARD"), (0.3,)) for _ in range(99)]     # target season: excluded
        + [_rec(2023, 1, 3, ("SOFT", "HARD"), (0.3,), era="old") for _ in range(99)]  # other era: excluded
    )
    front, source = precedent_options(records, track_id=1, regulation_era="e", grid=2, before_year=2024, total_laps=50)
    assert front[0][0].sequence == MH and source.startswith("track_band")
    back, _ = precedent_options(records, track_id=1, regulation_era="e", grid=16, before_year=2024, total_laps=50)
    assert back[0][0].sequence == ("HARD", "MEDIUM")
    new_track, source = precedent_options(records, track_id=9, regulation_era="e", grid=2, before_year=2024, total_laps=50)
    assert new_track[0][0].sequence == ("SOFT", "MEDIUM", "HARD") and source.startswith("era_band")
    assert precedent_options(records, track_id=1, regulation_era="none", grid=1, before_year=2024, total_laps=50)[1] == "none"


def test_load_precedents_is_dry_full_distance_and_before_target_year(fixture_db_url):
    engine = create_engine(fixture_db_url)
    with engine.connect() as db:
        trans = db.begin()
        for race_id, driver, stints in [
            (3, 1, [("MEDIUM", 1, 20), ("HARD", 21, 50)]),
            (3, 2, [("SOFT", 1, 10)]),                          # retired: < 90% distance
            (4, 1, [("SOFT", 1, 15), ("HARD", 16, 50)]),        # 2023: not before a 2023 target
        ]:
            entry = db.execute(text("SELECT id FROM race_entries WHERE race_id = :r AND driver_id = :d"),
                               {"r": race_id, "d": driver}).scalar()
            for n, (compound, start, end) in enumerate(stints, start=1):
                db.execute(text("""
                    INSERT INTO race_stints (race_id, race_entry_id, stint_number, compound, start_lap, end_lap, stint_length)
                    VALUES (:r, :e, :n, :c, :a, :b, :b - :a + 1)
                """), {"r": race_id, "e": entry, "n": n, "c": compound, "a": start, "b": end})
        records = load_precedents(db, before_year=2023)
        trans.rollback()
    engine.dispose()
    assert records == [PrecedentRecord(2022, 1, "era2_18inch_groundeffect", 2, HistoricalStrategy(MH, (0.4,)))]


# --- scoring --------------------------------------------------------------------

def test_strategy_metrics_and_brier():
    actual = _s(MH, [20])
    assert strategy_metrics(_s(MH, [24]), actual) == (1, 1, 4.0)
    assert strategy_metrics(_s(("SOFT", "MEDIUM", "HARD"), [15, 32]), actual) == (0, 0, 5.0)
    assert strategy_metrics(None, actual) == (None, None, None)
    assert abs(brier(0.8, 1) - 0.04) < 1e-12


def test_grid_slot_rates():
    rates = grid_slot_rates([(1, 1), (1, 1), (1, 2), (2, 1), (10, 5)])
    assert rates[1][0] == pytest.approx((2 + 0.05) / 4)
    assert rates[10][0] == pytest.approx(0.05 / 2)
    assert rates[15] == pytest.approx((0.05, 0.15))           # no history: weak prior only


def _row(race_id, grid, finish, expected, p_win=0.1, seq=1, era_seq=0):
    return DriverRow(race_id, 2024, grid, grid, finish, "Finished", expected, p_win, 0.3, 0.1, 0.3,
                     "MEDIUM → HARD", "p", "track", seq, 1, 2.0, "e", era_seq, 1, 4.0)


def test_race_clustered_ci_weights_races_equally():
    rows = [_row(1, g, g, g) for g in range(1, 21)] + [_row(2, 1, 5, 1)]
    value, low, high = race_clustered_ci(rows, lambda r: abs(r.expected_finish - r.actual_finish))
    assert value == pytest.approx((0 + 4) / 2)                # race means 0 and 4, not a 21-driver mean
    assert low <= value <= high
    assert all(math.isnan(x) for x in race_clustered_ci([], lambda r: 1))


def test_summary_reports_model_baseline_and_ci():
    rows = [_row(r, g, g, g + 0.5) for r in range(1, 6) for g in range(1, 6)]
    s = summarise(rows)
    assert (s["races"], s["drivers"]) == (5, 25)
    model, base, (diff, low, high) = s["finish_mae"]
    assert (model, base, diff) == (0.5, 0.0, 0.5) and low > 0     # clearly worse than grid
    model, base, (diff, *_ ) = s["sequence_miss"]
    assert (model, base, diff) == (0.0, 1.0, -1.0)
    model, base, (diff, *_ ) = s["first_stop_error_laps"]
    assert (model, base, diff) == (2.0, 4.0, -2.0)


# --- end to end on a synthetic multi-season database ----------------------------

def _seed_synthetic_seasons(conn, rng, race_bonus=None):
    """SYNTHETIC: 3 seasons x 2 dry races x 12 drivers; driver d is 0.1 s/lap slower than d-1.

    race_bonus: {team_id: seconds/lap} a team is faster in the race than its qualifying implies.
    """
    race_bonus = race_bonus or {}
    for tid in (1, 2):
        conn.execute(text("INSERT INTO tracks (id, name, country, total_race_laps) VALUES (:i, :n, 'X', 30)"),
                     {"i": tid, "n": f"Synthetic {tid}"})
    for team in range(1, 7):
        conn.execute(text("INSERT INTO teams (id, name) VALUES (:i, :n)"), {"i": team, "n": f"Team {team}"})
    for d in range(1, 13):
        conn.execute(text("INSERT INTO drivers (id, name) VALUES (:i, :n)"), {"i": d, "n": f"Driver {d}"})
    race_id = 0
    for season in (2022, 2023, 2024):
        for track in (1, 2):
            race_id += 1
            conn.execute(text("""
                INSERT INTO races (id, track_id, season_year, round_number, race_date, regulation_era)
                VALUES (:r, :t, :s, :t, make_date(:s, 3 + :t, 1), 'e')
            """), {"r": race_id, "t": track, "s": season})
            q = conn.execute(text("INSERT INTO sessions (race_id, session_type, start_time) VALUES (:r, 'Q', now()) RETURNING id"), {"r": race_id}).scalar()
            rs = conn.execute(text("INSERT INTO sessions (race_id, session_type, start_time) VALUES (:r, 'R', now()) RETURNING id"), {"r": race_id}).scalar()
            for s in (q, rs):
                conn.execute(text("INSERT INTO session_weather (session_id, rainfall) VALUES (:s, false)"), {"s": s})
            quali = {d: 80 + 0.1 * (d - 1) + rng.normal(0, 0.05) for d in range(1, 13)}
            grid = {d: i + 1 for i, d in enumerate(sorted(quali, key=quali.get))}
            totals = {}
            for d in range(1, 13):
                entry = conn.execute(text("""
                    INSERT INTO race_entries (race_id, driver_id, team_id, role, car_number)
                    VALUES (:r, :d, :t, 'race_driver', :d) RETURNING id
                """), {"r": race_id, "d": d, "t": (d + 1) // 2}).scalar()
                conn.execute(text("""
                    INSERT INTO qualifying_results (session_id, race_entry_id, q1_time, final_position)
                    VALUES (:s, :e, :t, :p)
                """), {"s": q, "e": entry, "t": quali[d], "p": grid[d]})
                stop = 12 + d % 6
                total, start = 0.0, 100.0
                for lap in range(1, 31):
                    age = lap - 1 if lap <= stop else lap - stop - 1
                    t = 90 + 0.12 * (d - 1) + 0.05 * age + rng.normal(0, 0.1) + (5 if lap == 1 else 0)
                    t -= race_bonus.get((d + 1) // 2, 0.0)
                    t += 18 if lap == stop else (4 if lap == stop + 1 else 0)
                    conn.execute(text("""
                        INSERT INTO laps (session_id, race_entry_id, lap_number, lap_time, tire_compound,
                                          track_status_code, lap_start_time_seconds, pit_in_time_seconds, pit_out_time_seconds)
                        VALUES (:s, :e, :n, :t, :c, '1', :st, :pi, :po)
                    """), {"s": rs, "e": entry, "n": lap, "t": t, "c": "MEDIUM" if lap <= stop else "HARD", "st": start,
                           "pi": start + t if lap == stop else None, "po": start if lap == stop + 1 else None})
                    total += t
                    start += t
                totals[(d, entry)] = total
                for n, (compound, a, b) in enumerate([("MEDIUM", 1, stop), ("HARD", stop + 1, 30)], start=1):
                    conn.execute(text("""
                        INSERT INTO race_stints (race_id, race_entry_id, stint_number, compound, start_lap, end_lap, stint_length)
                        VALUES (:r, :e, :n, :c, :a, :b, :b - :a + 1)
                    """), {"r": race_id, "e": entry, "n": n, "c": compound, "a": a, "b": b})
            for pos, ((d, entry), _) in enumerate(sorted(totals.items(), key=lambda kv: kv[1]), start=1):
                conn.execute(text("""
                    INSERT INTO race_results (session_id, race_entry_id, finishing_position, starting_grid_position, points, status)
                    VALUES (:s, :e, :p, :g, 0, 'Finished')
                """), {"s": rs, "e": entry, "p": pos, "g": grid[d]})


@pytest.fixture(scope="module")
def synthetic_db():
    import os

    from schema_migrations import apply_schema

    server_url = os.getenv("APEX21_TEST_DATABASE_URL")
    if not server_url:
        pytest.skip("APEX21_TEST_DATABASE_URL not set")
    name = f"apex21_bt_{uuid.uuid4().hex[:8]}"
    admin = create_engine(server_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    engine = create_engine(make_url(server_url).set(database=name))
    try:
        with engine.begin() as conn:
            apply_schema(conn)
            _seed_synthetic_seasons(conn, np.random.default_rng(4))
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def test_backtest_scores_every_driver_end_to_end(synthetic_db):
    with synthetic_db.connect() as db:
        rows = run_backtest(db, start_year=2024, end_year=2024, sims=300, seed=1,
                            overtake_threshold=0.8, deg_mode="pooled")
    assert len(rows) == 24                                  # 12 drivers x 2 races, not just pole sitters
    assert {r.race_id for r in rows} == {5, 6}
    by_race = {}
    for r in rows:
        by_race.setdefault(r.race_id, []).append(r)
    for race_rows in by_race.values():
        fastest = min(race_rows, key=lambda r: r.grid)
        slowest = max(race_rows, key=lambda r: r.grid)
        assert fastest.expected_finish < slowest.expected_finish
        assert sum(r.p_win for r in race_rows) == pytest.approx(1.0, abs=1e-3)  # 4-dp rounding
    assert all(r.precedent_source.startswith("track") for r in rows)
    assert all(r.precedent_strategy.startswith("MEDIUM → HARD") for r in rows)
    s = summarise(rows)
    assert s["drivers"] == 24 and s["sequence_miss"][0] == 0.0
