from types import SimpleNamespace

from audit_race_strategy_tyre_model_v1 import audit_rows


def _stint(compound, stint_key, slope):
    # Three age points produce the requested OLS slope exactly when centred on age 1.
    return [
        SimpleNamespace(compound=compound, stint_key=stint_key, tyre_age_laps=1, lap_time_delta_seconds=0.0),
        SimpleNamespace(compound=compound, stint_key=stint_key, tyre_age_laps=2, lap_time_delta_seconds=slope),
        SimpleNamespace(compound=compound, stint_key=stint_key, tyre_age_laps=3, lap_time_delta_seconds=2 * slope),
    ]


def test_audit_exposes_negative_raw_slopes():
    rows = _stint("SOFT", "s1", 0.10) + _stint("SOFT", "s2", -0.05)
    report = audit_rows(rows)

    assert report["SOFT"]["stints"] == 2
    assert report["SOFT"]["negative_slope_stints"] == 1
    assert report["SOFT"]["negative_slope_rate"] == 0.5
    assert report["SOFT"]["median_raw_slope"] == 0.025


def test_audit_separates_compounds():
    rows = _stint("SOFT", "s1", 0.10) + _stint("MEDIUM", "m1", 0.05) + _stint("HARD", "h1", 0.02)
    report = audit_rows(rows)

    assert report["SOFT"]["stints"] == 1
    assert report["MEDIUM"]["stints"] == 1
    assert report["HARD"]["stints"] == 1
    assert report["SOFT"]["median_raw_slope"] > report["MEDIUM"]["median_raw_slope"] > report["HARD"]["median_raw_slope"]
