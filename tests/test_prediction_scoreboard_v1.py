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
    assert {s.model for s in scores} == {"grid", "blend", "blend_practice", "blend_dnf"}
    assert {s.race_id for s in scores} == {5, 6} and len(scores) == 4 * 24
    # synthetic weekends have no practice sessions: the candidate equals production
    assert summarise(scores, "blend_practice")["mae"] == summarise(scores, "blend")["mae"]
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
