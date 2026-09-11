from concurrent.futures import ThreadPoolExecutor
from datetime import date
import os

# Ranking tests use no database access, but the module creates its engine on
# import. Keep this test independent from a developer's local dotenv file.
os.environ.setdefault("DATABASE_URL", "sqlite://")

import strategy_model_v5 as strategy_model


ERA_A = "era2_18inch_groundeffect"
ERA_B = "era3_2026regs"


def _race(race_id, race_date, era):
    return {
        "id": race_id,
        "track_id": 1,
        "race_date": race_date,
        "regulation_era": era,
    }


def _feature():
    return {
        "track_temp": None,
        "rain": 0,
        "weekend_format": "standard",
        "round_progress": 0.5,
    }


def _rank_for_era(era):
    target = _race(99, date(2026, 1, 1), era)
    races = {
        1: _race(1, date(2025, 1, 1), ERA_A),
        2: _race(2, date(2025, 1, 1), ERA_B),
    }
    features = {99: _feature(), 1: _feature(), 2: _feature()}
    strategies = {
        1: [{"compound": "MEDIUM"}, {"compound": "HARD"}],
        2: [{"compound": "SOFT"}, {"compound": "HARD"}],
    }

    ranked, _, _ = strategy_model.candidate_strategies(
        target,
        races,
        features,
        {},
        strategies,
        {},
        era,
    )
    return ranked


def test_strategy_v5_has_no_mutable_global_era():
    assert not hasattr(strategy_model, "ERA")


def test_candidate_ranking_uses_explicit_era():
    assert _rank_for_era(ERA_A)[0]["sequence"] == ["MEDIUM", "HARD"]
    assert _rank_for_era(ERA_B)[0]["sequence"] == ["SOFT", "HARD"]


def test_concurrent_era_rankings_do_not_cross_contaminate():
    eras = [ERA_A, ERA_B] * 50

    with ThreadPoolExecutor(max_workers=8) as executor:
        rankings = list(executor.map(_rank_for_era, eras))

    for era, ranked in zip(eras, rankings):
        expected = ["MEDIUM", "HARD"] if era == ERA_A else ["SOFT", "HARD"]
        assert ranked[0]["sequence"] == expected
