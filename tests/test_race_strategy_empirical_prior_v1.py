
import math
from datetime import date

from race_strategy_empirical_prior_v1 import (
    EmpiricalPriorConfig,
    StrategyResidualObservation,
    build_prior_weights,
    posterior_strategy_prior,
    strategy_family_label,
)

# strategy_family_label is imported above; this line is intentionally kept
# simple because the actual timing bucket logic lives in the feasibility audit.


def make_obs(
    race_id,
    race_date,
    family,
    residual,
    track_id=1,
    era="era2",
    grid_band="Top10",
    driver_id=1,
    team_id=1,
):
    return StrategyResidualObservation(
        race_id=race_id,
        race_date=race_date,
        track_id=track_id,
        regulation_era=era,
        grid_band=grid_band,
        driver_id=driver_id,
        team_id=team_id,
        strategy_family=tuple(family),
        pit_buckets=("middle",) if len(family) > 1 else (),
        finish_position=5,
        expected_pace_rank=5.0,
        finish_residual=float(residual),
    )


def test_strategy_family_label():
    assert strategy_family_label(("m", "h")) == "M → H"


def test_prior_weight_is_higher_for_same_track():
    target = make_obs(99, date(2025, 1, 1), ("M", "H"), 0.0)
    same_track = make_obs(1, date(2024, 1, 1), ("M", "H"), 0.0, track_id=1)
    other_track = make_obs(2, date(2024, 1, 1), ("M", "H"), 0.0, track_id=2)

    weights = dict(
        (row.race_id, weight)
        for row, weight in build_prior_weights(
            target,
            [same_track, other_track],
            {(1, "era2"): 3.0, (2, "era2"): 3.0},
        )
    )
    assert weights[1] > weights[2]


def test_posterior_shrinks_large_effect_toward_zero():
    target = make_obs(99, date(2025, 1, 1), ("M", "H"), 0.0)
    history = [
        make_obs(i, date(2024, 1, i), ("M", "H"), -4.0)
        for i in range(1, 5)
    ]
    posterior = posterior_strategy_prior(
        target,
        history,
        {(1, "era2"): 3.0},
        config=EmpiricalPriorConfig(
            track_same_weight=1.0,
            dirichlet_strength=0.0,
            residual_shrinkage_strength=8.0,
        ),
    )
    assert -4.0 < posterior[("M", "H")].predicted_residual < 0.0


def test_unseen_target_family_is_not_dropped():
    target = make_obs(99, date(2025, 1, 1), ("S", "H"), 0.0)
    history = [
        make_obs(i, date(2024, 1, i), ("M", "H"), 1.0)
        for i in range(1, 5)
    ]
    posterior = posterior_strategy_prior(
        target,
        history,
        {(1, "era2"): 3.0},
    )
    assert ("S", "H") in posterior
    assert posterior[("S", "H")].predicted_residual == 0.0
    assert posterior[("S", "H")].probability == 0.0


def test_future_history_is_excluded_by_date():
    target = make_obs(99, date(2025, 1, 1), ("M", "H"), 0.0)
    future = make_obs(1, date(2025, 2, 1), ("M", "H"), -5.0)
    assert build_prior_weights(
        target,
        [future],
        {(1, "era2"): 3.0},
    ) == []


def test_probabilities_sum_to_one():
    target = make_obs(99, date(2025, 1, 1), ("M", "H"), 0.0)
    history = [
        make_obs(1, date(2024, 1, 1), ("M", "H"), 0.5),
        make_obs(2, date(2024, 2, 1), ("S", "H"), -0.5),
    ]
    posterior = posterior_strategy_prior(
        target,
        history,
        {(1, "era2"): 3.0},
    )
    total = sum(p.probability for p in posterior.values())
    assert math.isclose(total, 1.0, rel_tol=1e-9, abs_tol=1e-9)
