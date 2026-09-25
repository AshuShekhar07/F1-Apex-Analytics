from datetime import date

import pytest
from sqlalchemy import create_engine, text

from race_strategy_pit_loss_v2 import (
    LapRecord,
    PitLossObservation,
    classify_condition,
    estimate_pit_loss,
    load_observations,
    field_medians,
    stop_laps,
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


FLAT_FIELD = {n: 90.0 for n in range(1, 30)}


def test_green_stop_loss_uses_balanced_reference():
    # 1 s/lap slower than the field on old tyres, 1 s/lap faster on new ones
    times = [100, 91, 91, 91, 111, 110, 89, 89, 89, 89]
    laps = _race(times, pit_in={5}, pit_out={6})
    [(pit, condition, loss, reference_gap)] = stop_losses(laps, [5], FLAT_FIELD)
    assert (pit, condition, reference_gap) == (5, "green", 0.0)
    assert loss == (111 - 90) + (110 - 90)


def test_safety_car_stop_is_measured_against_the_slow_field():
    """Regression: comparing with green pace charged the SC's slowness to the stop."""
    field = dict(FLAT_FIELD) | {5: 125.0, 6: 128.0}   # whole field slow under SC
    times = [100, 91, 91, 91, 137, 140, 89, 89, 89, 89]
    laps = _race(times, statuses={5: "14", 6: "4"}, pit_in={5}, pit_out={6})
    [(_, condition, loss, _)] = stop_losses(laps, [5], field)
    assert condition == "sc"
    assert loss == (137 - 125) + (140 - 128)          # 24 s, not 97 s
    green_loss = stop_losses(_race([100, 91, 91, 91, 111, 110, 89, 89, 89, 89], pit_in={5}, pit_out={6}),
                             [5], FLAT_FIELD)[0][2]
    assert loss < green_loss


def test_neutralised_reference_laps_are_skipped():
    times = [100, 91, 91, 91, 111, 110, 89, 89, 89, 89]
    laps = _race(times, statuses={3: "4", 8: "6"}, pit_in={5}, pit_out={6})
    [(_, condition, loss, reference_gap)] = stop_losses(laps, [5], FLAT_FIELD)
    assert condition == "green" and reference_gap == 0.0 and loss == 41.0


def test_field_reference_absorbs_fuel_burn():
    # field gets 0.1 s/lap faster; the driver matches it except on in/out laps
    field = {n: 95.0 - 0.1 * n for n in range(1, 11)}
    times = [field[n] for n in range(1, 11)]
    times[4] += 20.0   # in-lap
    times[5] += 19.0   # out-lap
    laps = _race(times, pit_in={5}, pit_out={6})
    [(_, _, loss, _)] = stop_losses(laps, [5], field)
    assert loss == pytest.approx(39.0)


def test_statuses_and_missing_data_are_labelled():
    times = [100, 91, 91, 91, 111, 110, 89, 89, 89, 89]
    assert stop_losses(_race(times, statuses={5: None}, pit_in={5}, pit_out={6}), [5], FLAT_FIELD)[0][1] == "unknown"
    assert stop_losses(_race(times, statuses={5: "15"}, pit_in={5}, pit_out={6}), [5], FLAT_FIELD)[0][1] == "red_flag"
    no_field = {n: v for n, v in FLAT_FIELD.items() if n != 6}
    assert stop_losses(_race(times, pit_in={5}, pit_out={6}), [5], no_field)[0][1] == "no_timing"
    assert stop_losses(_race(times[:5], pit_in={5}), [5], FLAT_FIELD)[0][1] == "no_timing"


def test_too_little_clean_pace_gives_no_reference():
    laps = _race([100, 91, 111, 110, 89], pit_in={3}, pit_out={4})
    assert stop_losses(laps, [3], FLAT_FIELD) == [(3, "no_reference", None, None)]


def test_other_stop_laps_never_used_as_reference():
    times = [100, 91, 91, 91, 111, 110, 89, 112, 110, 88, 88, 88]
    laps = _race(times, pit_in={5, 8}, pit_out={6, 9})
    first, second = stop_losses(laps, [5, 8], FLAT_FIELD)
    # only lap 7 is clean between the two stops
    assert first[1] == "no_reference"
    assert second[1] == "no_reference"


def test_field_median_excludes_cars_stopping_on_that_lap():
    laps_by_entry = {
        1: _race([90, 110, 105], pit_in={2}, pit_out={3}),
        2: _race([91, 91, 91]),
        3: _race([92, 92, 92]),
        4: _race([93, 93, 93]),
    }
    field = field_medians(laps_by_entry, {1: stop_laps([2])})
    assert field == {1: 91.5, 2: 92.0, 3: 92.0}


def _obs(track, era, condition, loss, race_id, day):
    return PitLossObservation(race_id, date(2024, 1, day), track, era, 1, 10, condition, loss, 0.0)


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
    observations = [_obs(1, "e", "sc", 12.0, i, i) for i in range(1, 16)]   # 15 SC stops, 15 races
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
        for driver in (2, 3, 4):
            other = db.execute(text("SELECT id FROM race_entries WHERE race_id = 3 AND driver_id = :d"), {"d": driver}).scalar()
            for n in range(1, 11):
                db.execute(text("""
                    INSERT INTO laps (session_id, race_entry_id, lap_number, lap_time, track_status_code)
                    VALUES (:s, :e, :n, 90.0, '1')
                """), {"s": session, "e": other, "n": n})
        observations = load_observations(db, start_year=2022, end_year=2022)
        trans.rollback()
    engine.dispose()
    assert [(o.race_id, o.pit_lap, o.condition, o.loss_seconds) for o in observations] == [(3, 5, "green", 41.0)]
    assert observations[0].regulation_era == "era2_18inch_groundeffect"


def test_neutralised_stops_need_more_evidence_for_a_track_value():
    # 9 SC stops over 2 races at track 1: enough for green thresholds, not for SC
    observations = [_obs(1, "e", "sc", 3.0, 1 + i % 2, 1 + i % 2) for i in range(9)]
    observations += [_obs(2, "e", "sc", 14.0, 3 + i % 3, 3 + i % 3) for i in range(15)]
    est = estimate_pit_loss(observations, track_id=1, regulation_era="e", condition="sc")
    assert est.source == "era_fallback" and est.n_stops == 24
    track2 = estimate_pit_loss(observations, track_id=2, regulation_era="e", condition="sc")
    assert (track2.source, track2.median_seconds) == ("track_era", 14.0)
    loose = estimate_pit_loss(observations, track_id=1, regulation_era="e", condition="sc", min_stops=8, min_races=2)
    assert loose.source == "track_era"
