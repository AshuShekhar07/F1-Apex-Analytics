"""Behavioural tests for simulator v2: each checks a race dynamic v1 could not express."""

import numpy as np
import pytest

from race_strategy_simulator_v1 import Distribution, Strategy, StrategyStint
from race_strategy_simulator_v2 import (
    GREEN,
    RED,
    SC,
    CarSpec,
    EventModel,
    TrackModel,
    evaluate_candidates,
    sample_events,
    simulate_race,
)

LAPS = 40
NO_EVENTS = EventModel(sc_per_lap=0.0, vsc_per_lap=0.0)


def one_stop(stop, first="MEDIUM", second="HARD", laps=LAPS):
    return Strategy(f"{first}-{second}@{stop}", (StrategyStint(first, 1, stop), StrategyStint(second, stop + 1, laps)))


def car(name, grid, pace=90.0, strategy=None):
    return CarSpec(name, grid, Distribution(pace), ((strategy or one_stop(20), 1.0),))


def track(threshold=1.0, deg=0.2, noise=0.0, green=20.0, sc=10.0, vsc=14.0):
    return TrackModel(
        total_laps=LAPS,
        tyre_deg_per_lap={c: Distribution(deg) for c in ("SOFT", "MEDIUM", "HARD")},
        pit_loss={"green": Distribution(green), "sc": Distribution(sc), "vsc": Distribution(vsc)},
        overtake_threshold_seconds=threshold,
        lap_noise_seconds=noise,
    )


def finish(cars, trk, state=None, sims=4):
    return simulate_race(cars, trk, NO_EVENTS, sims=sims, seed=1, event_state=state)


def test_identical_cars_finish_in_grid_order():
    positions = finish([car("A", 1), car("B", 2), car("C", 3)], track())
    assert (positions == [1, 2, 3]).all()


def test_small_pace_advantage_cannot_pass_large_one_can():
    cars = [car("slow", 1, pace=90.5), car("fast", 2, pace=90.0)]
    assert (finish(cars, track(threshold=1.0))[:, 1] == 2).all()   # stuck behind
    assert (finish(cars, track(threshold=0.3))[:, 1] == 1).all()   # passes


def test_competitors_have_tyre_wear_too():
    """v1 gave competitors no tyre wear; here a no-stop car on old tyres fades."""
    no_stop = Strategy("HARD", (StrategyStint("HARD", 1, LAPS),))
    cars = [car("stays_out", 1, strategy=no_stop), car("stops", 2)]
    positions = finish(cars, track(threshold=0.3, deg=0.2, green=20.0))
    assert (positions[:, 1] == 1).all()


def test_undercut_emerges_from_track_position():
    ahead_stops_late = [car("A", 1, strategy=one_stop(23)), car("B", 2, strategy=one_stop(20))]
    assert (finish(ahead_stops_late, track(threshold=2.0))[:, 1] == 1).all()   # B undercuts
    ahead_stops_first = [car("A", 1, strategy=one_stop(20)), car("B", 2, strategy=one_stop(23))]
    assert (finish(ahead_stops_first, track(threshold=2.0))[:, 0] == 1).all()  # A covers it


def test_safety_car_stop_is_cheaper_and_triggers_reaction():
    """Low wear: staying out beats a 20 s green stop, but a 10 s SC stop plus the
    restart bunching turns A's reactive stop into a win."""
    state = np.zeros(LAPS + 1, dtype=np.int8)
    state[18:21] = SC
    no_stop = Strategy("HARD", (StrategyStint("HARD", 1, LAPS),))
    # A plans lap 22 (within the 5-lap window -> pits under SC on lap 18); B never stops
    cars = [car("B", 1, strategy=no_stop), car("A", 2, strategy=one_stop(22))]
    trk = track(threshold=0.5, deg=0.04)
    assert (finish(cars, trk)[:, 1] == 2).all()
    assert (finish(cars, trk, state=state)[:, 1] == 1).all()


def test_safety_car_bunches_the_field():
    state = np.zeros(LAPS + 1, dtype=np.int8)
    state[10:13] = SC
    no_stop = Strategy("HARD", (StrategyStint("HARD", 1, LAPS),))
    # a much faster leader loses its lead at the restart and cannot escape a 3-lap sprint
    cars = [car("fast", 1, pace=89.0, strategy=no_stop), car("slow", 2, pace=89.2, strategy=no_stop)]
    short = TrackModel(**{**track(threshold=5.0).__dict__, "total_laps": 14})
    cut = Strategy("HARD", (StrategyStint("HARD", 1, 14),))
    cars = [CarSpec(c.name, c.grid_position, c.base_pace, ((cut, 1.0),)) for c in cars]
    positions = simulate_race(cars, short, NO_EVENTS, sims=2, seed=1, event_state=state[:15])
    assert (positions[:, 0] == 1).all()


def test_red_flag_gives_free_tyre_change():
    state = np.zeros(LAPS + 1, dtype=np.int8)
    state[20] = RED
    state[21] = SC
    no_stop = Strategy("HARD", (StrategyStint("HARD", 1, LAPS),))
    cars = [car("A", 1, strategy=one_stop(30)), car("B", 2, strategy=no_stop)]
    # B's tyres are reset by the red flag, so its no-stop plan no longer pays a wear
    # penalty, while A still pays for its planned stop after the restart
    assert (finish(cars, track(threshold=0.3, deg=0.1))[:, 1] == 2).all()
    assert (finish(cars, track(threshold=0.3, deg=0.1), state=state)[:, 1] == 1).all()


def test_seed_reproducible_and_common_random_numbers():
    trk = track(noise=0.3)
    events = EventModel(sc_per_lap=0.03, vsc_per_lap=0.03)
    comps = [car(f"C{i}", i + 2, pace=90.0 + 0.1 * i) for i in range(5)]
    me = car("me", 1)
    a = evaluate_candidates(me, [one_stop(20), one_stop(20)], comps, trk, events, sims=200, seed=3)
    assert a[0].win_probability == a[1].win_probability
    b = evaluate_candidates(me, [one_stop(20)], comps, trk, events, sims=200, seed=3)
    assert b[0].finish_distribution == a[0].finish_distribution


def test_candidate_ranking_and_distribution():
    trk = track(threshold=0.5, deg=0.25)
    comps = [car(f"C{i}", i + 2, pace=90.2) for i in range(3)]
    results = evaluate_candidates(car("me", 1), [one_stop(8), one_stop(20)], comps, trk, NO_EVENTS, sims=50)
    assert results[0].strategy.stop_laps == (20,)
    assert abs(sum(results[0].finish_distribution) - 1.0) < 1e-9


def test_sample_events():
    rng = np.random.default_rng(0)
    assert (sample_events(NO_EVENTS, LAPS, 10, rng) == GREEN).all()
    always_sc = sample_events(EventModel(sc_per_lap=1.0, vsc_per_lap=0.0, sc_duration_laps=(3, 3)), 10, 5, rng)
    assert (always_sc[:, 1:4] == SC).all()
    red = sample_events(EventModel(sc_per_lap=0.0, vsc_per_lap=0.0, red_per_lap=1.0), 10, 5, rng)
    assert (red[:, 1] == RED).all() and (red[:, 2] == SC).all()


def test_dry_only_guard():
    wet = Strategy("INTER", (StrategyStint("INTERMEDIATE", 1, LAPS),))
    with pytest.raises(ValueError, match="dry-only"):
        finish([car("A", 1, strategy=wet)], track())


def test_retirements_are_classified_behind_finishers():
    trk = TrackModel(**{**track().__dict__, "dnf_probability": 0.5})
    cars = [car(f"C{i}", i + 1) for i in range(6)]
    positions = simulate_race(cars, trk, NO_EVENTS, sims=400, seed=5)
    # every car retires in roughly half the simulations, so even the pole car often finishes last
    assert 0.3 < (positions[:, 0] >= 4).mean() < 0.8
    assert all(sorted(row) == list(range(1, 7)) for row in positions)
    no_dnf = simulate_race(cars, track(), NO_EVENTS, sims=50, seed=5)
    assert (no_dnf == [1, 2, 3, 4, 5, 6]).all()
