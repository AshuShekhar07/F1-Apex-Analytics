from audit_race_strategy_event_timing_v1 import (
    LapBoundary,
    StatusPoint,
    extract_event_windows,
    map_windows_to_laps,
    normalize_status_points,
)


def test_normalize_status_points_collapses_duplicate_states():
    points = [
        StatusPoint(10.0, "1"),
        StatusPoint(10.5, "1"),
        StatusPoint(20.0, "4"),
        StatusPoint(25.0, "4"),
        StatusPoint(40.0, "1"),
    ]
    normalized = normalize_status_points(points)
    assert normalized == [
        StatusPoint(10.0, "1"),
        StatusPoint(20.0, "4"),
        StatusPoint(40.0, "1"),
    ]


def test_extract_event_windows_uses_real_status_transition_times():
    points = [
        StatusPoint(0.0, "1"),
        StatusPoint(120.0, "6"),
        StatusPoint(180.0, "7"),
        StatusPoint(300.0, "1"),
        StatusPoint(500.0, "5"),
        StatusPoint(700.0, "1"),
    ]
    assert extract_event_windows(points) == [
        ("VSC", 120.0, 180.0),
        ("RED_FLAG", 500.0, 700.0),
    ]


def test_map_windows_to_laps_uses_lap_end_convention():
    windows = [("SC", 105.0, 195.0)]
    laps = [
        LapBoundary(1, 100.0),
        LapBoundary(2, 150.0),
        LapBoundary(3, 200.0),
    ]
    mapped = map_windows_to_laps(windows, laps)
    assert mapped[0].start_lap == 2
    assert mapped[0].end_lap == 3
    assert mapped[0].event_type == "SC"
