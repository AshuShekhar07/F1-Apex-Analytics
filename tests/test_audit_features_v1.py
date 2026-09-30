"""Audit follow-ups: rain from tyres when weather is missing, sprint pace, car-pace team form,
driver form, the error breakdown and the data audit's SQL (run on the real schema)."""

from datetime import date

import pytest
from sqlalchemy import text

from race_form_signal_v1 import (
    DriverForm,
    build_rows,
    sprint_beta,
    with_car_form,
    with_driver_form,
    with_sprint_correction,
)
from race_pace_from_quali_v1 import load_observations
from test_race_strategy_backtest_v2 import synthetic_db  # noqa: F401  (fixture)


def _r(driver, implied=0.0, residual=None, sprint=None, form=0.0, form_car=None, driver_form=None, race=1):
    return DriverForm(race, 2024, driver, driver, driver, True, implied, form, residual, driver, None, None, False,
                      form_car, driver_form, sprint)


def test_car_form_and_driver_form_replace_or_add():
    rows = [_r(1, form=0.01, form_car=-0.002, driver_form=0.001), _r(2, form=0.01)]
    assert [r.form for r in with_car_form(rows)] == [-0.002, 0.01]          # no car form: unchanged
    assert [r.form for r in with_driver_form(rows)] == [pytest.approx(0.011), 0.01]


def test_sprint_correction_fitted_on_history_and_centred():
    # history: race residual = 0.5 * sprint surprise -> beta 0.5
    history = []
    for race in range(1, 6):
        for d in range(1, 7):
            surprise = 0.002 * (d - 3.5)
            history.append(_r(d, residual=0.5 * surprise, sprint=surprise, race=race))
    assert sprint_beta(history) == pytest.approx(0.5, abs=1e-6)
    rows = [_r(d, sprint=0.002 * (d - 3.5)) for d in range(1, 7)] + [_r(7)]
    out = with_sprint_correction(rows, 0.5)
    assert out[-1].implied_gap == 0.0                                     # no sprint lap: unchanged
    assert out[5].implied_gap > out[0].implied_gap                        # slower sprint -> slower race pace
    assert sprint_beta([_r(d, residual=0.0) for d in range(1, 7)]) == 0.0  # no sprint data


def test_error_breakdown_counts_each_driver_once():
    from prediction_scoreboard_v1 import DriverScore, RaceMeta, error_categories

    meta = {1: RaceMeta(1, date(2024, 1, 1), 2024, "e", False, "T"), 2: RaceMeta(2, date(2024, 2, 1), 2024, "e", True, "W")}
    scores = [
        DriverScore(1, "blend", 1, 1, 1, 20, False, 0, 0),     # retired: error 19
        DriverScore(1, "blend", 2, 15, 10, 8, True, 0, 0),     # started 15th from quali P5 -> penalty: 2
        DriverScore(1, "blend", 3, 3, 3, 9, True, 0, 0),       # lap 1 from P3 to P10: 6
        DriverScore(2, "blend", 4, 4, 4, 7, True, 0, 0),       # wet race: 3
        DriverScore(1, "blend", 5, 5, 5, 5, True, 0, 0),       # else: 0
        DriverScore(1, "grid", 5, 5, 5, 5, True, 0, 0),        # other models ignored
    ]
    facts = {(1, 2): (5, 12), (1, 3): (3, 10), (2, 4): (4, 4), (1, 5): (5, 5)}
    out = {k.split(" ")[0]: (n, share, avg) for k, n, share, avg in error_categories(scores, meta, facts)}
    assert out["retired"] == (1, pytest.approx(19 / 30), 19.0)
    assert out["grid"][2] == 2.0 and out["lap"][2] == 6.0 and out["wet"][2] == 3.0 and out["everything"][2] == 0.0
    assert sum(v[0] for v in out.values()) == 5


def test_rain_from_tyres_when_weather_is_missing(synthetic_db):
    from race_practice_pace_v1 import session_is_wet

    with synthetic_db.connect() as db:
        tx = db.begin()
        try:
            race6 = db.execute(text("SELECT id FROM sessions WHERE race_id = 6 AND session_type = 'R'")).scalar()
            db.execute(text("DELETE FROM session_weather WHERE session_id = :s"), {"s": race6})
            # no weather row, dry tyres: race 6 is still used (it used to vanish)
            obs = load_observations(db, start_year=2018, end_year=2024)
            assert 6 in {o.race_id for o in obs}
            assert not session_is_wet(db, race6, None)
            db.execute(text("UPDATE laps SET tire_compound = 'INTERMEDIATE' WHERE session_id = :s"), {"s": race6})
            assert session_is_wet(db, race6, None)
            assert 6 not in {o.race_id for o in load_observations(db, start_year=2018, end_year=2024)}
            dry_rows = build_rows(db, obs, start_year=2024, end_year=2024)
            assert 6 not in {r.race_id for r in dry_rows}
            assert 6 in {r.race_id for r in build_rows(db, obs, start_year=2024, end_year=2024, include_wet=True)}
        finally:
            tx.rollback()


def test_sprint_pace_is_loaded_and_skipped_when_wet(synthetic_db):
    from race_practice_pace_v1 import load_sprint_gaps

    with synthetic_db.connect() as db:
        tx = db.begin()
        try:
            s = db.execute(text("INSERT INTO sessions (race_id, session_type) VALUES (6, 'S') RETURNING id")).scalar()
            entries = db.execute(text("SELECT id, driver_id FROM race_entries WHERE race_id = 6 ORDER BY driver_id")).all()
            for entry, driver in entries:
                for lap in range(1, 18):
                    db.execute(text("""INSERT INTO laps (session_id, race_entry_id, lap_number, lap_time, tire_compound)
                                       VALUES (:s, :e, :n, :t, 'MEDIUM')"""),
                               {"s": s, "e": entry, "n": lap, "t": 90 + 0.1 * driver + (5 if lap == 1 else 0)})
            gaps = load_sprint_gaps(db, 6)
            assert len(gaps) == 12 and gaps[1] == 0.0 and gaps[12] > gaps[2] > 0
            rows = build_rows(db, load_observations(db, start_year=2018, end_year=2024), start_year=2024,
                              end_year=2024, with_sprint=True)
            assert {r.driver_id for r in rows if r.race_id == 6 and r.sprint_gap is not None} == set(range(1, 13))
            assert all(r.form_car is not None and r.driver_form is not None for r in rows)
            db.execute(text("UPDATE laps SET tire_compound = 'WET' WHERE session_id = :s"), {"s": s})
            assert load_sprint_gaps(db, 6) == {}
        finally:
            tx.rollback()


def test_data_audit_sql_runs_on_the_real_schema(synthetic_db):
    from data_audit_v1 import run

    with synthetic_db.connect() as db:
        seasons, shares, counts = run(db, 2022, 2024)
    assert seasons == [2022, 2023, 2024]
    errors = [name for name, by_year in shares + counts if "error" in by_year]
    assert errors == []
    shares = dict(shares)
    assert shares["* qualifying results"][2024] == (2, 2)
    assert shares["* starters with race laps"][2024] == (24, 24)
    assert shares["* weekends with FP2 laps (practice pace)"][2024] == (0, 2)


def test_forecast_form_histories_match_the_scoreboard_rows(synthetic_db):
    """The live forecast rebuilds car-pace team form and driver form from observations; it
    must give exactly what build_rows (scored by the scoreboard) gives."""
    from race_form_signal_v1 import DRIVER_FORM_RACES, DRIVER_SHRINK, car_form_histories, team_form

    with synthetic_db.connect() as db:
        obs = load_observations(db, start_year=2018, end_year=2024)
        rows = build_rows(db, obs, start_year=2024, end_year=2024)
        when = {r: d for r, d in db.execute(text("SELECT id, race_date FROM races"))}
        for r in rows:
            teams, drivers = car_form_histories(db, [o for o in obs if o.race_date < when[r.race_id]],
                                                era="e", before=when[r.race_id])
            assert team_form(teams.get(r.team_id, []), r.team_id, when[r.race_id]) == pytest.approx(r.form_car)
            assert team_form(drivers.get(r.driver_id, []), r.driver_id, when[r.race_id], races=DRIVER_FORM_RACES,
                             shrink=DRIVER_SHRINK) == pytest.approx(r.driver_form)


def test_partly_loaded_sprint_is_not_used_and_is_flagged(synthetic_db):
    from data_quality_v1 import evaluate, load_facts, season_races
    from race_practice_pace_v1 import load_sprint_gaps
    from repair_session_laps_v1 import incomplete_sessions

    with synthetic_db.connect() as db:
        tx = db.begin()
        try:
            s = db.execute(text("INSERT INTO sessions (race_id, session_type) VALUES (6, 'S') RETURNING id")).scalar()
            entries = db.execute(text("SELECT id, driver_id FROM race_entries WHERE race_id = 6 ORDER BY driver_id")).all()
            for entry, driver in entries:                       # 12 sprint results ...
                db.execute(text("""INSERT INTO race_results (session_id, race_entry_id, finishing_position, status)
                                   VALUES (:s, :e, :p, 'Finished')"""), {"s": s, "e": entry, "p": driver})
            for entry, driver in entries[:4]:                   # ... but laps for only 4 (as 2026 R12)
                for lap in range(1, 18):
                    db.execute(text("""INSERT INTO laps (session_id, race_entry_id, lap_number, lap_time)
                                       VALUES (:s, :e, :n, :t)"""), {"s": s, "e": entry, "n": lap, "t": 90 + 0.1 * driver})
            assert load_sprint_gaps(db, 6) == {}
            sprint = [x for x in incomplete_sessions(db, 2024) if x["session_type"] == "S"]
            assert [(x["race_id"], x["with_laps"], x["starters"]) for x in sprint] == [(6, 4, 12)]
            race = next(r for r in season_races(db, 2024) if r["id"] == 6)
            check = evaluate(load_facts(db, race))
            assert any("sprint laps for only 4/12" in w for w in check.warns)
        finally:
            tx.rollback()


def test_practice_from_both_sessions_covers_drivers_either_session_missed(synthetic_db):
    from race_practice_pace_v1 import load_practice_gaps, load_practice_gaps_all

    with synthetic_db.connect() as db:
        tx = db.begin()
        try:
            entries = db.execute(text("SELECT id, driver_id FROM race_entries WHERE race_id = 6 ORDER BY driver_id")).all()
            for session_type, drivers in (("FP1", range(1, 7)), ("FP2", range(4, 13))):
                s = db.execute(text("INSERT INTO sessions (race_id, session_type) VALUES (6, :t) RETURNING id"),
                               {"t": session_type}).scalar()
                for entry, driver in entries:
                    if driver not in drivers:
                        continue
                    for lap in range(1, 9):
                        db.execute(text("""INSERT INTO laps (session_id, race_entry_id, lap_number, lap_time, tire_compound)
                                           VALUES (:s, :e, :n, :t, 'MEDIUM')"""),
                                   {"s": s, "e": entry, "n": lap, "t": 95 + 0.1 * driver})
            only_fp2, _ = load_practice_gaps(db, 6)
            both = load_practice_gaps_all(db, 6)
            assert set(only_fp2) == set(range(4, 13))
            assert set(both) == set(range(1, 13)) and both[1] == 0.0 and both[12] > both[6] > both[1]
        finally:
            tx.rollback()
