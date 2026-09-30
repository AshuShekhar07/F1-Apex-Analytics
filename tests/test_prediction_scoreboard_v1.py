import pytest

from prediction_scoreboard_v1 import (
    DriverScore,
    calibration,
    compare,
    load_finishes,
    load_meta,
    model_scores,
    paired,
    race_metrics,
    slot_rate_function,
    summarise,
)
from race_form_signal_v1 import build_rows
from race_pace_from_quali_v1 import load_observations
from test_race_strategy_backtest_v2 import synthetic_db  # noqa: F401  (fixture)


def _s(driver, predicted, finish, classified=True, p_win=0.0, p_podium=0.0, race=1, model="m", grid=None):
    return DriverScore(race, model, driver, grid or predicted, predicted, finish, classified, p_win, p_podium)


def test_race_metrics_perfect_and_imperfect():
    perfect = [_s(d, d, d, p_win=float(d == 1), p_podium=float(d <= 3)) for d in range(1, 13)]
    m = race_metrics(perfect)
    assert m["winner_hit"] == 1 and m["podium_overlap"] == 1 and m["top10_overlap"] == 1
    assert m["rank_corr"] == pytest.approx(1) and m["mae"] == 0 and m["brier_win"] == 0 and m["brier_podium"] == 0

    # predicted driver 1 to win but driver 3 won; driver 12 (predicted P2) retired and is classified last
    rows = ([_s(1, 1, 2), _s(12, 2, 12, classified=False), _s(3, 3, 1)]
            + [_s(d, d, d - 1) for d in range(4, 12)] + [_s(2, 12, 11)])
    m = race_metrics(rows)
    assert m["winner_hit"] == 0
    assert m["podium_overlap"] == pytest.approx(2 / 3)       # named drivers 1 and 3 of the real top 3 {3, 1, 4}
    assert m["bad_miss_finishers"] == 0                      # the 10-place miss was a retirement
    assert m["mae"] > m["mae_finishers"]


def test_summary_paired_and_calibration():
    a = [_s(d, d, d, race=r, model="a", p_podium=0.9 if d <= 3 else 0.0) for r in (1, 2) for d in range(1, 11)]
    b = [_s(d, 11 - d, d, race=r, model="b") for r in (1, 2) for d in range(1, 11)]
    scores = a + b
    assert summarise(scores, "a")["mae"][0] == 0 and summarise(scores, "a")["races"] == (2,)
    d, lo, _ = paired(scores, "b", "a", "mae")
    assert d > 0 and lo > 0                                   # b clearly worse
    [(_, _, said, happened)] = [c for c in calibration(scores, "a", "p_podium") if c[1] == 6]
    assert said == pytest.approx(0.9) and happened == 1.0


def test_regression_gate_flags_only_real_drops():
    base = {"blend": {"mae": [3.0, 2.9, 3.1], "winner_hit": [0.4, 0.3, 0.5]}}
    assert compare({"blend": {"mae": [3.02, 0, 0], "winner_hit": [0.39, 0, 0]}}, base) == []
    assert sorted(compare({"blend": {"mae": [3.2, 0, 0], "winner_hit": [0.30, 0, 0]}}, base)) == [
        "blend.mae", "blend.winner_hit"]


def test_scoreboard_end_to_end(synthetic_db):
    with synthetic_db.connect() as db:
        meta = load_meta(db)
        rates = slot_rate_function(load_finishes(db))
        rows = build_rows(db, load_observations(db, start_year=2018, end_year=2024), start_year=2023,
                          end_year=2024, include_wet=True, with_practice=True)
        from prediction_scoreboard_v1 import reliability_adjuster, reliability_signal
        from race_reliability_v1 import load_starts

        adjust, used = reliability_adjuster(load_starts(db), meta)
        scores = model_scores(rows, meta, rates, start_year=2024, adjusters={"blend_dnf": adjust})
    assert {s.model for s in scores} == {"grid", "blend", "blend_wetq", "blend_track", "blend_wetqw",
                                         "diag_wetrace", "blend_dnf", "blend_pass", "blend_practice_all",
                                         "blend_driver_all"}
    assert {s.race_id for s in scores} == {5, 6} and len(scores) == 10 * 24
    # synthetic: no practice laps, one era -> both candidates equal production
    assert summarise(scores, "blend_practice_all")["mae"] == summarise(scores, "blend")["mae"]
    assert summarise(scores, "blend_driver_all")["mae"] == summarise(scores, "blend")["mae"]
    # synthetic qualifying is dry: the wet-qualifying guard and weight change nothing
    assert summarise(scores, "blend_wetq")["mae"] == summarise(scores, "blend")["mae"]
    assert summarise(scores, "blend_wetqw")["mae"] == summarise(scores, "blend")["mae"]
    # too little history to rate tracks: the track candidate falls back to production
    assert summarise(scores, "blend_track")["mae"] == summarise(scores, "blend")["mae"]
    # synthetic: every car finishes, so every team has the same rate and the order is unchanged
    assert summarise(scores, "blend_dnf")["mae"] == summarise(scores, "blend")["mae"]
    assert len({round(p, 9) for p in used.values()}) <= 2
    assert reliability_signal(used, scores)[4] == 24
    grid = summarise(scores, "grid")
    # synthetic: driver d is slower than d-1 and qualifies in that order, so grid order ~ finish order
    assert grid["rank_corr"][0] > 0.8
    assert all(0 <= s.p_win <= 1 and 0 <= s.p_podium <= 1 for s in scores)
    slot = rates(meta[5].race_date, "e", 12)
    assert all(slot[g][0] >= slot[g + 1][0] for g in range(1, 12))   # production's monotone rule


def test_car_pace_fires_only_on_real_anomalies_within_the_same_session():
    from race_form_signal_v1 import TEAMMATE_MARGIN, car_quali_gaps, off_teammate_pace

    # normal teammate gaps (0.2-0.6%) never fire, even a Q1 exit vs the teammate's Q3 lap
    assert not off_teammate_pace((80.16, None, None), (80.0, 79.6, 79.2))
    assert not off_teammate_pace((80.5, None, None), (80.0, 79.6, 79.2))       # 0.6% in Q1: normal
    assert off_teammate_pace((81.2, None, None), (80.0, 79.6, 79.2))           # 1.5% in Q1: anomaly
    assert not off_teammate_pace((80.0, 79.7, 79.3), (80.0, 79.6, 79.2))       # Q3 vs Q3
    assert off_teammate_pace((None, None, None), (80.0, None, None))           # no time at all
    assert not off_teammate_pace((None, None, None), (None, None, None))

    teams = {1: 10, 2: 10, 3: 20, 4: 20, 5: 30}
    gaps = {1: 0.000, 2: 0.020, 3: 0.010, 4: 0.020, 5: 0.030}
    sessions = {1: (80.0, 79.6, 79.2), 2: (81.2, None, None),      # 2: 1.5% off in Q1 -> car pace
                3: (80.8, 80.0, None), 4: (81.2, None, None),      # 4: 0.5% off in Q1 -> kept
                5: (82.0, None, None)}                              # no teammate
    car = car_quali_gaps(gaps, teams, sessions)
    assert car[1] == 0.0 and car[2] == TEAMMATE_MARGIN
    assert car[3] == 0.010 and car[4] == 0.020 and car[5] == 0.030


def test_practice_correction_is_fitted_on_history_and_centred_per_race():
    from race_form_signal_v1 import DriverForm, practice_beta, with_practice_correction

    def row(race, d, implied, practice, residual):
        return DriverForm(race, 2023, d, d, d, True, implied, 0.0, residual, None, None, practice)

    # history: a driver 1% faster in long runs than quali implies is ~0.5% faster in the race
    history = [row(r, d, 0.001 * d, 0.001 * d + (0.01 if d == 1 else 0.0) * (-1),
                   -0.005 if d == 1 else 0.0) for r in range(1, 6) for d in range(1, 7)]
    beta = practice_beta(history)
    assert 0.3 < beta < 0.7
    target = [row(9, d, 0.001 * d, (0.001 * d - 0.01) if d == 3 else 0.001 * d, None) for d in range(1, 7)]
    adjusted = {r.driver_id: r.implied_gap for r in with_practice_correction(target, beta)}
    assert adjusted[3] < 0.003 - 0.003                        # moved ahead by ~beta * 1%
    assert all(adjusted[d] == 0.001 * d for d in (1, 2, 4, 5, 6))
    assert practice_beta([]) == 0.0


def test_rank_probabilities_learn_from_earlier_races_only():
    from datetime import date, timedelta

    from prediction_scoreboard_v1 import RaceMeta, rank_probability_scores

    meta = {r: RaceMeta(r, date(2024, 1, 1) + timedelta(days=7 * r), 2024, "e", False, "T") for r in range(1, 41)}
    flat = lambda as_of, era, field: {g: (0.25, 0.75) if g <= 3 else (0.0, 0.0) for g in range(1, field + 1)}
    # the model's predicted P1 wins every race, although the grid prior only gives 25%
    scores = [_s(d, d, d, race=r, model="blend") for r in range(1, 41) for d in range(1, 11)]
    probs = {(s.race_id, s.driver_id): s for s in rank_probability_scores(scores, meta, flat)}
    assert probs[(1, 1)].p_win == pytest.approx(0.25)          # first race: nothing learned yet
    assert probs[(40, 1)].p_win > 0.75                          # 39 earlier wins from predicted P1
    assert all(probs[(40, d)].p_win >= probs[(40, d + 1)].p_win for d in range(1, 10))
    assert all(s.predicted == s.finish for s in probs.values())  # the order itself is unchanged


def test_wet_qualifying_guard_keeps_own_lap_only_in_the_rain():
    from race_form_signal_v1 import DriverForm, with_car_pace_dry_quali

    dry = DriverForm(1, 2024, 1, 5, 5, True, 0.02, 0.0, None, 10, 0.01, None, False)
    wet = DriverForm(2, 2024, 1, 5, 5, True, 0.02, 0.0, None, 10, 0.01, None, True)
    assert [r.implied_gap for r in with_car_pace_dry_quali([dry, wet])] == [0.01, 0.02]


def _row(race, season, driver, grid, finish, wet_q=False):
    from race_form_signal_v1 import DriverForm
    return DriverForm(race, season, driver, grid, finish, True, 0.01 * driver, 0.0, None, driver, None, None, wet_q)


def _meta(race, season, name, wet=False):
    from datetime import date
    from prediction_scoreboard_v1 import RaceMeta
    return RaceMeta(race, date(season, 1 + race % 12, 1), season, "e", wet, name)


def test_hard_to_pass_tracks_rated_from_earlier_results_only():
    from prediction_scoreboard_v1 import hard_to_pass_tracks, track_hold

    meta, rows = {}, []
    tracks = ["Monaco", "Baku", "Monza", "Spa", "Suzuka", "Miami"]
    race = 0
    for season in (2021, 2022):
        for t, name in enumerate(tracks):
            race += 1
            meta[race] = _meta(race, season, name)
            # Monaco and Baku: finish = grid; the others reshuffle more the further down the list
            order = list(range(1, 11)) if t < 2 else [((g * (t + 2)) % 10) + 1 for g in range(10)]
            rows += [_row(race, season, g + 1, g + 1, order[g]) for g in range(10)]
    hold = track_hold(rows, meta)
    assert hold["Monaco"] == pytest.approx(1.0) and hold["Baku"] == pytest.approx(1.0)
    assert hard_to_pass_tracks(hold) >= {"Monaco", "Baku"}
    assert "Monza" not in hard_to_pass_tracks(hold)
    # one race at a track is not enough to rate it
    assert "Monaco" not in track_hold([r for r in rows if meta[r.race_id].season_year == 2022], meta)


def test_group_weight_uses_only_earlier_seasons_of_the_group():
    from prediction_scoreboard_v1 import group_weight

    # group races: grid reversed, finish = pace order (implied gap ~ driver id) -> pace wins (> 0.5)
    # other races: finish = grid -> weight 0.0 would be chosen if they leaked in
    rows = []
    for race in range(1, 13):
        season = 2020 + (race - 1) // 4
        wet = race % 2 == 0
        rows += [_row(race, season, d, 11 - d if wet else d, d, wet_q=wet) for d in range(10, 0, -1)]
    in_group = lambda race_id, rs: any(r.quali_wet for r in rs)
    assert group_weight(rows, 2023, lambda rs: list(rs), in_group, 0.3) > 0.5
    # the season being scored never feeds its own weight
    assert group_weight([r for r in rows if r.season_year < 2021], 2021, lambda rs: list(rs), in_group, 0.3) == 0.3


def test_recovery_weight_only_moves_out_of_position_drivers():
    from race_form_signal_v1 import DriverForm, choose_recovery_weight, predictions

    def race(rid):
        # driver 1 is fastest but starts 10th after a penalty (qualified 1st) and finishes 1st
        rows = [DriverForm(rid, 2024, 1, 10, 1, True, 0.000, 0.0, None, quali_pos=1)]
        rows += [DriverForm(rid, 2024, d, d - 1, d, True, 0.001 * d, 0.0, None, quali_pos=d - 1) for d in range(2, 11)]
        return rows
    rows = race(1)
    assert predictions(rows, 0.0)["blend"][0] == 10                       # grid order: 10th
    assert predictions(rows, 0.0, recovery_weight=1.0)["blend"][0] == 1    # own weight: recovers
    races = {k: race(k) for k in range(1, 25)}
    assert choose_recovery_weight(races, 0.0) == 1.0
    assert choose_recovery_weight({1: race(1)}, 0.0) is None               # too few such drivers


def test_pass_model_only_passes_clearly_slower_cars():
    from race_form_signal_v1 import DriverForm, choose_pass_params, pass_predictions

    def row(d, grid, pace, finish):
        return DriverForm(1, 2024, d, grid, finish, True, pace, 0.0, None)
    # driver 1 starts 3rd but is 1% faster than the two cars ahead; driver 4 is marginally faster than 3
    rows = [row(1, 3, 0.000, 1), row(2, 1, 0.010, 2), row(3, 2, 0.011, 3), row(4, 4, 0.0105, 4)]
    assert pass_predictions(rows, 1.0, 0.002) == [1, 2, 3, 4]        # passes both; 0.05% is not enough
    assert pass_predictions(rows, 1.0, 0.02) == [3, 1, 2, 4]         # margin too big: grid order
    races = {k: [row(1, 3, 0.000, 1), row(2, 1, 0.010, 2), row(3, 2, 0.011, 3), row(4, 4, 0.0105, 4)] for k in range(3)}
    strength, margin = choose_pass_params(races)
    assert margin < 0.01
