import math

from race_strategy_pace_calibration_v3 import (
    EnrichedPaceObservation,
    PaceResidualObservation,
    build_residual_observations,
    predict_target_pace,
    walk_forward_validate,
)


def _obs(race, year, track, team, driver, base, ref):
    return [
        EnrichedPaceObservation(race, track, year, 'era2_18inch_groundeffect', driver, lap, base + lap * 0.01)
        for lap in range(1, 6)
    ]


def test_build_residuals_produces_one_row_per_driver_race():
    rows = _obs(1, 2023, 10, '1', '44', 90.0, 90.0) + _obs(1, 2023, 10, '2', '55', 91.0, 90.0)
    residuals = build_residual_observations([r.__class__(**{**r.__dict__, 'team_key': team}) for r, team in zip(rows, ['1']*5 + ['2']*5)])
    assert len(residuals) == 2
    assert {r.driver_key for r in residuals} == {'44', '55'}


def test_prediction_uses_only_prior_years():
    rows = [
        PaceResidualObservation(1, 10, 2023, 'era2_18inch_groundeffect', '44', '1', 90.0, 0.5),
        PaceResidualObservation(2, 10, 2024, 'era2_18inch_groundeffect', '44', '1', 90.2, 0.4),
    ]
    pred = predict_target_pace(rows, target_track_id=10, target_era='era2_18inch_groundeffect', target_driver_key='44', target_team_key='1', as_of_year=2025)
    assert pred.mean_seconds < 91.0
    assert pred.team_history_n == 2
    assert pred.driver_history_n == 2


def test_prediction_warns_when_history_is_sparse():
    rows = [PaceResidualObservation(1, 10, 2024, 'era2_18inch_groundeffect', '44', '1', 90.0, 0.5)]
    pred = predict_target_pace(rows, target_track_id=10, target_era='era2_18inch_groundeffect', target_driver_key='44', target_team_key='1', as_of_year=2025)
    assert pred.warnings


def test_walk_forward_has_no_future_leakage_and_returns_metrics():
    rows = []
    for year, ref in [(2021, 90.0), (2022, 90.2), (2023, 90.1), (2024, 90.3)]:
        rows.append(PaceResidualObservation(year, 10, year, 'era2_18inch_groundeffect', '44', '1', ref, 0.4))
    result = walk_forward_validate(rows, min_train_races=1)
    assert result['predictions_attempted'] == 4
    assert result['predictions_scored'] == 3
    assert 0.0 <= result['coverage_rate'] <= 1.0
    assert math.isfinite(result['mae_seconds'])
