from audit_race_strategy_event_timing_batch_v3 import DEFAULT_CASES


def test_default_cases_are_unique_and_well_formed():
    assert len(DEFAULT_CASES) == len(set(DEFAULT_CASES))
    assert all(year >= 2018 and round_number >= 1 for year, round_number in DEFAULT_CASES)
