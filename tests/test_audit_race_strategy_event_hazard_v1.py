import pytest

from audit_race_strategy_event_hazard_v1 import EventObservation, phase_for_lap, summarize


def test_phase_boundaries_are_deterministic():
    assert phase_for_lap(1, 100) == "early"
    assert phase_for_lap(25, 100) == "early"
    assert phase_for_lap(26, 100) == "mid_early"
    assert phase_for_lap(50, 100) == "mid_early"
    assert phase_for_lap(51, 100) == "mid_late"
    assert phase_for_lap(75, 100) == "mid_late"
    assert phase_for_lap(76, 100) == "late"
    assert phase_for_lap(100, 100) == "late"


def test_invalid_phase_inputs_return_none():
    assert phase_for_lap(None, 100) is None
    assert phase_for_lap(1, 0) is None


def test_summary_counts_starts_and_durations():
    observations = [
        EventObservation(2025, 1, "SC", 10, 100, "early", 120.0),
        EventObservation(2025, 1, "SC", 60, 100, "mid_late", 60.0),
        EventObservation(2025, 2, "VSC", 30, 100, "mid_early", 45.0),
        EventObservation(2025, 3, "RED_FLAG", 50, 100, "mid_early", 300.0),
    ]
    result = summarize(observations, race_total=3)
    phase_lookup = {(r["event_type"], r["phase"]): r for r in result["phase_rows"]}
    assert phase_lookup[("SC", "early")]["event_starts"] == 1
    assert phase_lookup[("SC", "mid_late")]["event_starts"] == 1
    assert phase_lookup[("VSC", "mid_early")]["event_starts"] == 1
    assert phase_lookup[("RED_FLAG", "mid_early")]["event_starts"] == 1
    durations = {r["event_type"]: r for r in result["duration_rows"]}
    assert durations["SC"]["n"] == 2
    assert durations["SC"]["mean_seconds"] == pytest.approx(90.0)
    assert durations["RED_FLAG"]["median_seconds"] == pytest.approx(300.0)
