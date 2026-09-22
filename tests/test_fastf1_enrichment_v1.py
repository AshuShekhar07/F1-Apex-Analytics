import math
import pandas as pd

from backfill_fastf1_enrichment_v1 import (
    SESSION_LOAD_NAMES,
    TRACK_STATUS_NAMES,
    message_fingerprint,
    safe_bool,
    safe_float,
    safe_int,
    status_intervals,
    telemetry_summary,
    to_seconds,
)


def test_to_seconds_handles_timedelta_and_none():
    assert math.isclose(to_seconds(pd.Timedelta(seconds=12.5)), 12.5)
    assert to_seconds(None) is None
    assert to_seconds(pd.NaT) is None


def test_safe_converters():
    assert safe_float("12.5") == 12.5
    assert safe_int("17") == 17
    assert safe_bool("true") is True
    assert safe_bool("0") is False
    assert safe_bool(None) is None


def test_status_intervals_compresses_consecutive_states():
    df = pd.DataFrame(
        [
            {"Time": pd.Timedelta(seconds=0), "Status": "1"},
            {"Time": pd.Timedelta(seconds=10), "Status": "1"},
            {"Time": pd.Timedelta(seconds=20), "Status": "4"},
            {"Time": pd.Timedelta(seconds=30), "Status": "4"},
            {"Time": pd.Timedelta(seconds=40), "Status": "1"},
        ]
    )
    result = status_intervals(df)
    assert result == [
        {"start": 0.0, "end": 20.0, "status_code": "1", "status_name": "ALL_CLEAR"},
        {"start": 20.0, "end": 40.0, "status_code": "4", "status_name": "SAFETY_CAR"},
        {"start": 40.0, "end": None, "status_code": "1", "status_name": "ALL_CLEAR"},
    ]


def test_telemetry_summary_extracts_core_metrics():
    df = pd.DataFrame(
        {
            "Speed": [100.0, 200.0, 150.0],
            "Throttle": [50.0, 100.0, 96.0],
            "Brake": [False, True, False],
            "DRS": [0, 12, 0],
            "RPM": [8000.0, 9000.0, 8500.0],
            "nGear": [5, 7, 6],
            "Distance": [0.0, 1200.0, 2400.0],
            "DistanceToDriverAhead": [300.0, 100.0, 200.0],
        }
    )
    result = telemetry_summary(df)
    assert result["mean_speed_kmh"] == 150.0
    assert result["max_speed_kmh"] == 200.0
    assert result["full_throttle_pct"] == 66.66666666666666
    assert result["brake_active_pct"] == 33.33333333333333
    assert result["drs_active_pct"] == 33.33333333333333
    assert result["distance_m"] == 2400.0
    assert result["close_traffic_150m_pct"] == 33.33333333333333
    assert result["driver_ahead_samples"] == 3
    assert result["telemetry_quality"] == "full"


def test_message_fingerprint_is_stable():
    row = {
        "Time": pd.Timedelta(seconds=12),
        "Lap": 3,
        "Category": "Flag",
        "Message": "Yellow flag",
        "Flag": "YELLOW",
    }
    assert message_fingerprint(10, row) == message_fingerprint(10, dict(row))
    assert message_fingerprint(10, row) != message_fingerprint(11, row)


def test_session_types_cover_current_project_codes():
    assert {"FP1", "FP2", "FP3", "Q", "SQ", "S", "R"} <= set(SESSION_LOAD_NAMES)
    assert TRACK_STATUS_NAMES["4"] == "SAFETY_CAR"
