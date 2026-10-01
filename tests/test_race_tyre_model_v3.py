from datetime import date

import numpy as np
import pytest

from race_strategy_prediction_backtest_v1 import Score
from race_tyre_model_backtest_v3 import RaceFit, gates
from race_tyre_model_v3 import (
    TyreLapObs,
    TyreModel,
    clean_relative_laps,
    fit_for_race,
    fit_tyre_model,
    optimise,
    residuals,
)

TRUE = {"SOFT": (-0.6, 0.12), "MEDIUM": (0.0, 0.06), "HARD": (0.4, 0.03)}


def _synthetic(rng, races=6, track=1, era="e", year=2023):
    """SYNTHETIC: 10 cars per race, each runs two compounds with a known offset + wear."""
    obs = []
    for r in range(races):
        for car in range(10):
            level = rng.normal(0, 0.5)
            first, second = [("SOFT", "HARD"), ("MEDIUM", "HARD"), ("SOFT", "MEDIUM")][car % 3]
            for compound, ages in ((first, range(1, 21)), (second, range(1, 31))):
                o, s = TRUE[compound]
                for a in ages:
                    obs.append(TyreLapObs(r, date(year, 3 + r, 1), track, era, car, compound, float(a),
                                          level + o + s * a + rng.normal(0, 0.2)))
    return obs


def test_fit_recovers_offsets_and_wear_despite_car_levels():
    model = fit_tyre_model(_synthetic(np.random.default_rng(0)))
    for compound, (o, s) in TRUE.items():
        assert model.params[compound][0] == pytest.approx(o, abs=0.08)
        assert model.params[compound][1] == pytest.approx(s, abs=0.01)


def test_fit_needs_medium_and_two_compounds():
    only_soft = [o for o in _synthetic(np.random.default_rng(1)) if o.compound == "SOFT"]
    assert fit_tyre_model(only_soft) is None


def test_walk_forward_fit_uses_only_earlier_races():
    obs = _synthetic(np.random.default_rng(2))
    assert fit_for_race(obs, track_id=1, regulation_era="e", before_date=date(2023, 3, 1)) is None
    model = fit_for_race(obs, track_id=1, regulation_era="e", before_date=date(2023, 6, 1))
    assert model.n_races == 3 and model.source.startswith("track_era")  # 3 x 500 laps meets the track minimum
    other_track = fit_for_race(obs, track_id=2, regulation_era="e", before_date=date(2023, 6, 1))
    assert other_track.source.startswith("era(")                         # no track history -> era pool


def test_model_beats_flat_on_its_own_kind_of_data():
    obs = _synthetic(np.random.default_rng(3))
    model = fit_tyre_model(obs)
    flat = residuals(lambda o: 0.0, obs)
    full = residuals(lambda o: model.shape(o.compound, o.tyre_age), obs)
    assert np.sqrt(np.mean(np.square(full))) < 0.5 * np.sqrt(np.mean(np.square(flat)))


def test_optimiser_trades_wear_against_pit_loss():
    model = TyreModel({"SOFT": (-0.5, 0.10), "MEDIUM": (0.0, 0.05), "HARD": (0.3, 0.02)}, 0, 0, "test")
    cheap = optimise(model, total_laps=60, pit_loss=5.0)
    dear = optimise(model, total_laps=60, pit_loss=60.0)
    assert len(cheap[0][0].stop_laps) == 2 and len(dear[0][0].stop_laps) == 1
    assert cheap[0][1] == 0.0 and all(gap >= 0 for _, gap in cheap)
    assert all(len(set(s.sequence)) >= 2 for s, _ in cheap + dear)


def test_negative_fitted_wear_is_treated_as_zero():
    model = TyreModel({"MEDIUM": (0.0, -0.05), "HARD": (0.2, 0.0)}, 0, 0, "test")
    best = optimise(model, total_laps=50, pit_loss=20.0)[0][0]
    assert len(best.stop_laps) == 1


def test_clean_laps_drop_pit_sc_lap1_and_outliers():
    race = {"id": 1, "race_date": date(2024, 1, 1), "track_id": 1, "regulation_era": "e"}
    rows = []
    for car in range(6):
        for lap in range(1, 9):
            status = "4" if lap == 5 else "1"
            t = 90.0 + 0.1 * car + (20.0 if (car == 0 and lap == 6) else 0.0)
            rows.append((car, lap, t, "medium", lap, status, lap == 7 and car == 1, False))
    obs = clean_relative_laps(race, rows)
    laps = {(o.car, o.tyre_age) for o in obs}
    assert not any(a in (1, 5) for _, a in laps)                      # lap 1 and SC lap dropped
    assert (0, 6) not in laps and (1, 7) not in laps                  # outlier and in-lap dropped
    assert all(o.compound == "MEDIUM" for o in obs)


def test_gates():
    fits = [RaceFit(r, "era", 1.0, 0.9, 0.8) for r in range(5)]
    scores = [Score(r, m, 1, 1, 0, 1) for r in range(5) for m in ("optimiser", "hierarchy")]
    g = gates(fits, scores)
    assert g["gate1"] and g["gate2"] and g["enable_optimiser"]
    worse = [Score(r, m, 0, int(m == "hierarchy"), None, None) for r in range(5) for m in ("optimiser", "hierarchy")]
    assert not gates(fits, worse)["enable_optimiser"]
