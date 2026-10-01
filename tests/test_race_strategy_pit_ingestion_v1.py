from race_strategy_pit_ingestion_v1 import (
    PitIngestionConfig,
    ReconstructedPitStop,
    filter_pit_stop_outliers,
    reconstruct_pit_stops,
)


def test_reconstructs_pit_stop_from_consecutive_lap_timestamps():
    rows = [
        {"Driver": "VER", "DriverNumber": 1, "LapNumber": 20, "PitInTime": 1000.0, "PitOutTime": None},
        {"Driver": "VER", "DriverNumber": 1, "LapNumber": 21, "PitInTime": None, "PitOutTime": 1023.4},
    ]

    stops = reconstruct_pit_stops(rows)

    assert len(stops) == 1
    assert stops[0].driver == "VER"
    assert stops[0].driver_number == 1
    assert stops[0].pit_lap == 20
    assert stops[0].total_pit_lane_seconds == 23.4


def test_unmatched_pit_entry_is_not_invented_into_a_stop():
    rows = [
        {"Driver": "HAM", "DriverNumber": 44, "LapNumber": 30, "PitInTime": 1500.0, "PitOutTime": None},
    ]

    assert reconstruct_pit_stops(rows) == []


def test_physical_and_iqr_filters_remove_bad_stops():
    stops = [
        ReconstructedPitStop("A", 1, 100.0, 121.0, 21.0),
        ReconstructedPitStop("B", 2, 200.0, 222.0, 22.0),
        ReconstructedPitStop("C", 3, 300.0, 323.0, 23.0),
        ReconstructedPitStop("D", 4, 400.0, 424.0, 24.0),
        ReconstructedPitStop("E", 5, 500.0, 800.0, 300.0),
        ReconstructedPitStop("F", 6, 600.0, 610.0, 10.0),
    ]

    kept, removed = filter_pit_stop_outliers(stops, config=PitIngestionConfig())

    assert [row.total_pit_lane_seconds for row in kept] == [21.0, 22.0, 23.0, 24.0]
    assert removed == 2
