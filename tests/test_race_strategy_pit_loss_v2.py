from datetime import date

import pytest
from sqlalchemy import create_engine, text

from race_strategy_pit_loss_v2 import (
    LapRecord,
    PitLossObservation,
    classify_condition,
    estimate_pit_loss,
    load_observations,
    stop_losses,
)


def _race(lap_times, statuses=None, pit_in=(), pit_out=()):
    statuses = statuses or {}
    return [
        LapRecord(n, t, statuses.get(n, "1"), n in pit_in, n in pit_out)
        for n, t in enumerate(lap_times, start=1)
    ]


@pytest.mark.parametrize("statuses,expected", [
    (("1", "1"), "green"),
    (("12", "1"), "green"),
    (("14", "4"), "sc"),
    (("1", "671"), "vsc"),
    (("45", "1"), "red_flag"),
    (("1", None), "unknown"),
])
def test_classify_condition(statuses, expected):
    assert classify_condition(*statuses) == expected


def test_green_stop_loss_uses_balanced_reference():
    # old tyres at 91s before, new tyres at 89s after -> reference 90s
    times = [100, 91, 91, 91, 111, 110, 89, 89, 89, 89]
    laps = _race(times, pit_in={5}, pit_out={6})
    [(pit, condition, loss, reference)] = stop_losses(laps, [5])
    assert (pit, condition, reference) == (5, "green", 90.0)
    assert loss == 111 + 110 - 180


def test_neutralised_reference_laps_are_skipped():
    times = [100, 91, 91, 91, 111, 110, 89, 89, 89, 89]
    laps = _race(times, statuses={3: "4", 8: "6"}, pit_in={5}, pit_out={6})
    [(_, condition, loss, reference)] = stop_losses(laps, [5])
    assert condition == "green" and reference == 90.0 and loss == 41.0


def test_stop_under_safety_car_is_labelled_sc():
    times = [100, 91, 91, 91, 125, 128, 89, 89, 89, 89]
    laps = _race(times, statuses={5: "14", 6: "4"}, pit_in={5}, pit_out={6})
    [(_, condition, loss, _)] = stop_losses(laps, [5])
    assert condition == "sc" and loss == 125 + 128 - 180


def test_too_little_clean_pace_gives_no_reference():
    laps = _race([100, 91, 111, 110, 89], pit_in={3}, pit_out={4})
    assert stop_losses(laps, [3]) == [(3, "no_reference", None, None)]


def test_other_stop_laps_never_used_as_reference():
    times = [100, 91, 91, 91, 111, 110, 89, 112, 110, 88, 88, 88]
    laps = _race(times, pit_in={5, 8}, pit_out={6, 9})
    first, second = stop_losses(laps, [5, 8])
    # after the first stop only lap 7 is clean before the second stop's in-lap
    assert first[1] == "no_reference"
    assert second[1] == "no_reference"


def _obs(track, era, condition, loss, race_id, day):
    return PitLossObservation(race_id, date(2024, 1, day), track, era, 1, 10, condition, loss, 90.0)


def test_estimate_prefers_track_and_falls_back_to_era():
    observations = (
        [_obs(1, "e", "green", 20.0 + i % 2, 1 + i % 2, 1 + i % 2) for i in range(8)]
        + [_obs(2, "e", "green", 25.0, 3, 3) for _ in range(3)]
    )
    track = estimate_pit_loss(observations, track_id=1, regulation_era="e")
    assert (track.source, track.n_stops, track.n_races, track.median_seconds) == ("track_era", 8, 2, 20.5)
    thin = estimate_pit_loss(observations, track_id=2, regulation_era="e")
    assert (thin.source, thin.n_stops) == ("era_fallback", 11)


def test_estimate_is_walk_forward():
    observations = [_obs(1, "e", "green", 20.0, i, i) for i in range(1, 11)]
    est = estimate_pit_loss(observations, track_id=1, regulation_era="e", before_date=date(2024, 1, 9))
    assert est.n_stops == 8  # races on days 9 and 10 excluded
    assert estimate_pit_loss(observations, track_id=1, regulation_era="e", before_date=date(2024, 1, 5)) is None


def test_estimate_ignores_other_conditions_and_eras():
    observations = [_obs(1, "e", "sc", 12.0, i, i) for i in range(1, 11)]
    observations += [_obs(1, "other", "green", 20.0, i, i) for i in range(1, 11)]
    assert estimate_pit_loss(observations, track_id=1, regulation_era="e") is None
    assert estimate_pit_loss(observations, track_id=1, regulation_era="e", condition="sc").median_seconds == 12.0


# --- database adapter -------------------------------------------------------

def test_load_observations_from_fixture_db(fixture_db_url):
    engine = create_engine(fixture_db_url)
    with engine.connect() as db:
        trans = db.begin()
        session = db.execute(text("SELECT id FROM sessions WHERE race_id = 3 AND session_type = 'R'")).scalar()
        entry = db.execute(text("SELECT id FROM race_entries WHERE race_id = 3 AND driver_id = 1")).scalar()
        for n, t in enumerate([100, 91, 91, 91, 111, 110, 89, 89, 89, 89], start=1):
            db.execute(text("""
                INSERT INTO laps (session_id, race_entry_id, lap_number, lap_time, track_status_code,
                                  pit_in_time_seconds, pit_out_time_seconds)
                VALUES (:s, :e, :n, :t, '1', :pi, :po)
            """), {"s": session, "e": entry, "n": n, "t": t,
                   "pi": 500.0 if n == 5 else None, "po": 520.0 if n == 6 else None})
        observations = load_observations(db, start_year=2022, end_year=2022)
        trans.rollback()
    engine.dispose()
    assert [(o.race_id, o.pit_lap, o.condition, o.loss_seconds) for o in observations] == [(3, 5, "green", 41.0)]
    assert observations[0].regulation_era == "era2_18inch_groundeffect"
