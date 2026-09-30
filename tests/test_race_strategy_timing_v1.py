import pytest
from datetime import date

from race_strategy_timing_v1 import (
    CarStrategy,
    LapTyre,
    Stint,
    predict_strategy,
    stints_from_laps,
)


def _laps(spec):
    """spec: list of (compound, tyre_life, pit_out) per lap starting at lap 1."""
    return [LapTyre(i + 1, c, t, p) for i, (c, t, p) in enumerate(spec)]


def test_same_compound_stop_is_a_stop():
    spec = ([("MEDIUM", i + 1, False) for i in range(10)]
            + [("HARD", 1, True)] + [("HARD", i + 2, False) for i in range(9)]
            + [("HARD", 1, True)] + [("HARD", i + 2, False) for i in range(9)])
    assert stints_from_laps(_laps(spec)) == [Stint("MEDIUM", 1, 10), Stint("HARD", 11, 20), Stint("HARD", 21, 30)]


def test_mislabelled_lap_is_not_a_stop_and_red_flag_change_is():
    spec = [("MEDIUM", i + 1, False) for i in range(10)]
    spec[4] = ("SOFT", 5, False)                               # glitch: one lap labelled SOFT
    assert stints_from_laps(_laps(spec)) == [Stint("MEDIUM", 1, 10)]
    red = [("MEDIUM", i + 1, False) for i in range(6)] + [("HARD", 1, False), ("HARD", 2, False)]
    assert stints_from_laps(_laps(red)) == [Stint("MEDIUM", 1, 6), Stint("HARD", 7, 8)]


def test_compound_change_used_only_without_tyre_life_and_lap1_pit_out_ignored():
    spec = [("SOFT", None, True)] + [("SOFT", None, False)] * 4 + [("HARD", None, False)] * 5
    assert stints_from_laps(_laps(spec)) == [Stint("SOFT", 1, 5), Stint("HARD", 6, 10)]
    unknown = [("MEDIUM", 1, False), (None, 2, False), ("MEDIUM", 3, False)]
    assert stints_from_laps(_laps(unknown)) == [Stint("MEDIUM", 1, 3)]


def _car(track, grid, stints, laps=50, era="e", wet=False, year=2023):
    return CarStrategy(1, date(year, 5, 1), year, track, era, grid, wet, laps,
                       tuple(Stint(c, a, b) for c, a, b in stints))


MH = [("MEDIUM", 1, 20), ("HARD", 21, 50)]
SMH = [("SOFT", 1, 12), ("MEDIUM", 13, 30), ("HARD", 31, 50)]
HM = [("HARD", 1, 32), ("MEDIUM", 33, 50)]


def test_prediction_uses_track_and_grid_band_then_start_compound():
    history = ([_car(1, 3, MH) for _ in range(20)] + [_car(1, 15, HM) for _ in range(16)]
               + [_car(2, 3, SMH) for _ in range(40)])
    front = predict_strategy(history, track_id=1, regulation_era="e", grid=2, total_laps=50)
    assert front.strategy.sequence == ("MEDIUM", "HARD") and front.strategy.stop_laps == (20,)
    assert front.source.startswith("track_band")
    back = predict_strategy(history, track_id=1, regulation_era="e", grid=16, total_laps=50)
    assert back.strategy.sequence == ("HARD", "MEDIUM") and back.strategy.stop_laps == (32,)
    started_soft = predict_strategy(history, track_id=1, regulation_era="e", grid=2, total_laps=50,
                                    start_compound="soft")
    assert started_soft.strategy.sequence == ("SOFT", "MEDIUM", "HARD")
    assert started_soft.source.endswith("start=SOFT")
    assert abs(sum(w for _, w in front.alternatives) - 1.0) < 1e-9


def test_wet_history_is_separate():
    wet = [_car(1, 3, [("INTERMEDIATE", 1, 25), ("MEDIUM", 26, 50)], wet=True) for _ in range(20)]
    dry = [_car(1, 3, MH) for _ in range(20)]
    assert predict_strategy(wet + dry, track_id=1, regulation_era="e", grid=3, total_laps=50,
                            wet=True).strategy.sequence == ("INTERMEDIATE", "MEDIUM")
    assert predict_strategy(wet + dry, track_id=1, regulation_era="e", grid=3,
                            total_laps=50).strategy.sequence == ("MEDIUM", "HARD")


def test_no_history_gives_none():
    assert predict_strategy([], track_id=1, regulation_era="e", grid=1, total_laps=50).strategy is None


# --- prediction backtest -------------------------------------------------------

from race_strategy_prediction_backtest_v1 import run, score, summarise  # noqa: E402
from race_strategy_precedent_v1 import build_strategy  # noqa: E402
from test_race_strategy_backtest_v2 import synthetic_db  # noqa: E402,F401  (fixture)


def test_score_counts_hits_and_first_stop_error():
    actual = _car(1, 3, [("MEDIUM", 1, 20), ("HARD", 21, 50)])
    exact = score(1, "m", build_strategy(("MEDIUM", "HARD"), [20], 50), actual)
    assert (exact.sequence_hit, exact.stop_count_hit, exact.first_stop_error, exact.first_stop_within_3) == (1, 1, 0, 1)
    late = score(1, "m", build_strategy(("MEDIUM", "HARD", "HARD"), [25, 40], 50), actual)
    assert (late.sequence_hit, late.stop_count_hit, late.first_stop_error, late.first_stop_within_3) == (0, 0, 5, 0)
    missing = score(1, "m", None, actual)
    assert (missing.sequence_hit, missing.first_stop_error) == (0, None)


def test_prediction_backtest_end_to_end(synthetic_db):
    with synthetic_db.connect() as db:
        scores = run(db, start_year=2024, end_year=2024, wet=False)
    methods = {s.method for s in scores}
    assert methods == {"A old production", "B era mode (real stints)", "C hierarchy",
                       "D pre-start (known start tyre)", "E track-adjusted", "F last race here",
                       "H era mode + track timing", "G CEILING (hindsight)", "I practice wear",
                       "J practice wear + compounds", "K era stops + compounds"}
    assert len([s for s in scores if s.method == "B era mode (real stints)"]) == 24     # every classified car
    # every synthetic car runs MEDIUM -> HARD, so every method gets the sequence right
    assert all(s.sequence_hit == 1 for s in scores)
    rows = summarise(scores)
    assert {r[0] for r in rows} == {"sequence_hit", "stop_count_hit", "first_stop_error", "first_stop_within_3"}
    baseline = [r for r in rows if r[1] == "B era mode (real stints)"]
    assert len(baseline) == 4 and all(r[3] is None for r in baseline)


def _hist(race, when, track, era, seqs_stops, laps=50):
    """seqs_stops: list of (sequence, stop laps) -> one car each."""
    out = []
    for seq, stops in seqs_stops:
        bounds = [0, *stops, laps]
        out.append(CarStrategy(race, when, when.year, track, era, 5, False, laps,
                               tuple(Stint(c, bounds[i] + 1, bounds[i + 1]) for i, c in enumerate(seq))))
    return out


def test_track_adjusted_moves_toward_the_track_only_with_evidence():
    from race_strategy_timing_v1 import predict_track_adjusted

    one = (("MEDIUM", "HARD"), [20])
    two = (("MEDIUM", "HARD", "HARD"), [15, 32])
    # era: 60 one-stoppers at other tracks; track 7: 40 two-stoppers stopping at lap 15
    history = (_hist(1, date(2024, 3, 1), 1, "e", [one] * 30) + _hist(2, date(2024, 4, 1), 2, "e", [one] * 30)
               + _hist(3, date(2024, 5, 1), 7, "e", [two] * 20) + _hist(4, date(2025, 5, 1), 7, "e", [two] * 20))
    p = predict_track_adjusted(history, track_id=7, regulation_era="e", total_laps=50)
    assert p.strategy.sequence == ("MEDIUM", "HARD", "HARD")
    # 40 track cars at lap 15 vs k=20 cars of the era median (lap 15 for this sequence at... only track)
    assert p.strategy.stop_laps[0] == 15
    # a new track: pure era answer
    new = predict_track_adjusted(history, track_id=99, regulation_era="e", total_laps=50)
    assert new.strategy.sequence == ("MEDIUM", "HARD") and new.strategy.stop_laps == (20,)
    # a single car at the track is not enough to overturn 60 era cars
    few = predict_track_adjusted(history[:60] + _hist(5, date(2024, 6, 1), 8, "e", [two]),
                                 track_id=8, regulation_era="e", total_laps=50)
    assert few.strategy.sequence == ("MEDIUM", "HARD")
    # timing: one-stop track evidence at lap 30 pulls the era's lap 20 toward it
    late = predict_track_adjusted(history[:60] + _hist(6, date(2024, 7, 1), 9, "e", [(("MEDIUM", "HARD"), [30])] * 20),
                                  track_id=9, regulation_era="e", total_laps=50)
    assert late.strategy.stop_laps == (25,)


def test_last_race_at_track_and_fallback():
    from race_strategy_timing_v1 import predict_last_race_at_track

    old = _hist(1, date(2023, 5, 1), 7, "e", [(("MEDIUM", "HARD"), [20])] * 10)
    recent = _hist(2, date(2024, 5, 1), 7, "e", [(("HARD", "MEDIUM"), [30])] * 6 + [(("SOFT", "HARD"), [10])] * 4)
    p = predict_last_race_at_track(old + recent, track_id=7, regulation_era="e", total_laps=50)
    assert p.strategy.sequence == ("HARD", "MEDIUM") and p.strategy.stop_laps == (30,)
    assert predict_last_race_at_track(old, track_id=99, regulation_era="e", total_laps=50).strategy.sequence == ("MEDIUM", "HARD")


def test_track_timing_keeps_the_era_sequence_and_times_it_at_the_track():
    from race_strategy_timing_v1 import predict_era_mode_track_timing

    history = (_hist(1, date(2024, 3, 1), 1, "e", [(("MEDIUM", "HARD"), [20])] * 30)
               + _hist(2, date(2024, 5, 1), 7, "e", [(("MEDIUM", "HARD"), [35])] * 6
                       + [(("SOFT", "HARD"), [10])] * 10))
    p = predict_era_mode_track_timing(history, track_id=7, regulation_era="e", grid=5, total_laps=50)
    # sequence is the era's (MEDIUM-HARD, 36 cars) even though SOFT-HARD led at this track
    assert p.strategy.sequence == ("MEDIUM", "HARD") and p.strategy.stop_laps == (35,)
    # too few examples at the track (< 5): era timing
    few = _hist(1, date(2024, 3, 1), 1, "e", [(("MEDIUM", "HARD"), [20])] * 30) + \
        _hist(2, date(2024, 5, 1), 7, "e", [(("MEDIUM", "HARD"), [35])] * 3)
    assert predict_era_mode_track_timing(few, track_id=7, regulation_era="e", grid=5,
                                         total_laps=50).strategy.stop_laps == (20,)


def test_hindsight_ceiling_is_the_race_mode():
    from race_strategy_prediction_backtest_v1 import hindsight_mode

    cars = _hist(1, date(2024, 3, 1), 1, "e", [(("MEDIUM", "HARD"), [20])] * 3 + [(("HARD", "MEDIUM"), [30])] * 2)
    assert hindsight_mode(cars, 50).sequence == ("MEDIUM", "HARD")


def test_stop_count_from_practice_wear_votes_among_closest_races():
    from race_strategy_timing_v1 import predict_from_wear, race_modes, stops_from_wear

    one, two = (("MEDIUM", "HARD"), [25]), (("MEDIUM", "HARD", "HARD"), [15, 32])
    history, wear = [], {}
    for race in range(1, 11):                       # low wear -> 1 stop, high wear -> 2 stops
        high = race > 5
        history += _hist(race, date(2024, race, 1), race, "e", [two if high else one] * 6)
        wear[race] = 0.004 if high else 0.001
    modes = race_modes(history)
    assert modes[1][2] == 1 and modes[10][2] == 2
    assert stops_from_wear(modes, wear, wear=0.0038, regulation_era="e", k=3) == 2
    assert stops_from_wear(modes, wear, wear=0.0009, regulation_era="e", k=3) == 1
    assert stops_from_wear(modes, wear, wear=0.004, regulation_era="e", k=30) is None   # too few races
    p = predict_from_wear(history, wear=0.0041, wear_by_race=wear, regulation_era="e", total_laps=50)
    assert p.strategy.sequence == ("MEDIUM", "HARD", "HARD") and p.strategy.stop_laps == (15, 32)
    # no practice wear: the era mode (B), unchanged
    assert predict_from_wear(history, wear=None, wear_by_race=wear, regulation_era="e",
                             total_laps=50).source.startswith("era")


def test_run_wear_is_the_relative_slope():
    from race_practice_pace_v1 import run_wear

    run = [(n, 90.0 + 0.09 * i) for i, n in enumerate(range(10, 18))]
    assert run_wear(run) == pytest.approx(0.09 / 90.315, rel=1e-3)
    assert run_wear(run[:5]) is None


def test_sequence_follows_the_compounds_teams_long_ran():
    from race_strategy_timing_v1 import pick_sequence_from_practice, predict_from_practice

    candidates = [(("MEDIUM", "HARD"), 0.5), (("SOFT", "HARD"), 0.3), (("HARD", "MEDIUM"), 0.2)]
    assert pick_sequence_from_practice(candidates, {"SOFT": 0.5, "HARD": 0.5}) == ("SOFT", "HARD")
    assert pick_sequence_from_practice(candidates, {"MEDIUM": 0.5, "HARD": 0.5}) == ("MEDIUM", "HARD")
    history = (_hist(1, date(2024, 3, 1), 1, "e", [(("MEDIUM", "HARD"), [20])] * 10)
               + _hist(2, date(2024, 4, 1), 2, "e", [(("SOFT", "HARD"), [12])] * 6))
    p = predict_from_practice(history, wear=None, wear_by_race={}, shares={"SOFT": 0.6, "HARD": 0.4},
                              regulation_era="e", total_laps=50, use_wear=False)
    assert p.strategy.sequence == ("SOFT", "HARD") and p.strategy.stop_laps == (12,)
    # no practice data: unchanged era mode
    assert predict_from_practice(history, wear=None, wear_by_race={}, shares={}, regulation_era="e",
                                 total_laps=50, use_wear=False).strategy.sequence == ("MEDIUM", "HARD")
