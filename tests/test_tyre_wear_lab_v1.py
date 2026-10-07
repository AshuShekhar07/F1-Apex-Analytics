from datetime import date, timedelta

import pytest

from tyre_wear_lab_v1 import (
    FUEL_SECONDS_PER_LAP, Pair, PracticeRun, RaceInfo, add_calibrated, add_stacked, build_pairs, c_compound,
    clustered_spearman, era_compound, practice_current, practice_pooled, practice_v2, race_truth, report,
    track_history,
)
from race_tyre_model_v3 import TyreLapObs
from test_race_strategy_backtest_v2 import synthetic_db  # noqa: F401  (fixture)

START = date(2023, 3, 1)


def _run(session, compound, wear, laps=10, first=5, warm=1.0, base=90.0):
    """Practice run: true wear `wear` s/lap, hidden by fuel burn, first lap `warm` s slow."""
    return PracticeRun(session, compound, tuple(
        (first + i, base + (wear - FUEL_SECONDS_PER_LAP) * i + (warm if i == 0 else 0.0)) for i in range(laps)))


def test_practice_measures_recover_the_wear():
    runs = [_run("FP2", "MEDIUM", 0.08), _run("FP2", "MEDIUM", 0.08, base=91.0), _run("FP1", "MEDIUM", 0.30)]
    assert practice_v2(runs, "MEDIUM") == pytest.approx(0.08)            # FP2 only, fuel and warm-up handled
    assert practice_current(runs, "MEDIUM") < 0.08 - FUEL_SECONDS_PER_LAP + 0.01   # fuel hides the wear
    assert practice_pooled(runs, "MEDIUM") > 0.08                        # pools FP1's high-wear run too
    assert practice_pooled(runs[:2], "MEDIUM") == pytest.approx(0.08)
    assert practice_v2(runs, "HARD") is None and practice_pooled(runs[:1], "MEDIUM") is None


def _info(race, track, era="e", c=None, weeks=None, season=None):
    d = START + timedelta(weeks=weeks if weeks is not None else race)
    return RaceInfo(race, d, season or d.year, track, era, c or {})


def test_history_measures_are_walk_forward():
    info = {1: _info(1, 7), 2: _info(2, 8), 3: _info(3, 7, era="old", weeks=-60), 4: _info(4, 7)}
    truth = {1: {"MEDIUM": 0.10}, 2: {"MEDIUM": 0.04}, 3: {"MEDIUM": 0.20}, 4: {"MEDIUM": 0.99}}
    target = _info(4, 7)
    assert era_compound(truth, info, target, "MEDIUM") == pytest.approx(0.07)      # races 1 and 2, not 4 itself
    assert track_history(truth, info, target, "MEDIUM") == pytest.approx((0.10 + 0.5 * 0.20) / 1.5)
    assert track_history(truth, info, _info(9, 99), "MEDIUM") is None


def test_c_compound_matches_the_same_rubber():
    info = {r: _info(r, 100 + r, c={"MEDIUM": 3, "HARD": 2}) for r in range(1, 5)}
    truth = {r: {"MEDIUM": 0.05 * r, "HARD": 0.01} for r in range(1, 5)}
    target = _info(9, 50, c={"SOFT": 3})                  # C3 is this weekend's SOFT
    assert c_compound(truth, info, target, "SOFT") == pytest.approx(0.125)
    assert c_compound(truth, info, _info(9, 50, c={"SOFT": 5}), "SOFT") is None
    assert c_compound(truth, info, _info(9, 50, c={"SOFT": 3}, season=2022), "SOFT") is None   # redefined in 2023


def test_truth_is_each_races_own_fitted_wear():
    obs = []
    for car in range(10):
        for age in range(1, 20):
            for compound, slope, offset in (("MEDIUM", 0.06, 0.0), ("HARD", 0.03, 0.4)):
                obs.append(TyreLapObs(1, START, 7, "e", car, compound, float(age), 0.1 * car + offset + slope * age))
    truth = race_truth({1: obs, 2: obs[:10]})
    assert truth[1]["MEDIUM"] == pytest.approx(0.06, abs=1e-6) and truth[1]["HARD"] == pytest.approx(0.03, abs=1e-6)
    assert 2 not in truth                                  # too few laps


def test_pairs_calibration_stacking_and_report(capsys):
    info, truth, practice = {}, {}, {}
    for r in range(1, 61):
        wear = 0.02 + 0.002 * (r % 25)
        info[r] = _info(r, r % 6)
        truth[r] = {"MEDIUM": wear}
        practice[r] = [_run("FP2", "MEDIUM", wear * 1.5 + 0.01), _run("FP2", "MEDIUM", wear * 1.5 + 0.01, base=91)]
    pairs = build_pairs(truth, info, practice)
    add_stacked(pairs)
    add_calibrated(pairs)
    late = pairs[-1]
    assert late.values["practice_v2"] == pytest.approx(late.truth * 1.5 + 0.01)
    assert late.calibrated["practice_v2"] == pytest.approx(late.truth, abs=1e-6)     # line learned on earlier races
    assert late.values["stacked"] is not None and pairs[0].values["stacked"] is None
    rho, lo, hi, n = clustered_spearman(pairs, lambda p: p.values["practice_v2"])
    assert rho == pytest.approx(1.0) and n == 60
    report(pairs, "test")
    out = capsys.readouterr().out
    assert "TYRE WEAR LAB" in out and "error vs era_compound" in out


def test_lab_runs_on_the_database(synthetic_db):
    from tyre_wear_lab_v1 import run

    with synthetic_db.connect() as db:
        pairs = run(db)
    assert all(isinstance(p, Pair) for p in pairs)
