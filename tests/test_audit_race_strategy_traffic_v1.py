import math

import pandas as pd

from audit_race_strategy_event_timing_v1 import EventWindow
from audit_race_strategy_traffic_v1 import (
    LapRecord,
    TrafficExposure,
    _classify,
    _compute_exposure,
    _interval_overlap_seconds,
    _match_exposures,
    _prepare_laps,
    _race_balanced_summary,
    _tyre_age,
    AuditCounters,
    RaceMeta,
    parse_thresholds,
)


def make_lap(
    lap_number: int,
    *,
    driver: str = "44",
    race_id: int = 1,
    stint: int = 1,
    compound: str = "MEDIUM",
    tyre_age: int = 10,
    residual: float = 0.0,
    position: float = 5.0,
    previous_position: float = 5.0,
) -> LapRecord:
    return LapRecord(
        race_id=race_id,
        season_year=2024,
        round_number=1,
        regulation_era="2022-2025",
        driver_key=driver,
        driver_label=driver,
        lap_number=lap_number,
        stint_number=stint,
        compound=compound,
        tyre_age=tyre_age,
        lap_time_seconds=90.0,
        field_median_seconds=89.5,
        field_relative_residual=residual,
        lap_start_seconds=float((lap_number - 1) * 90),
        lap_end_seconds=float(lap_number * 90),
        current_position=position,
        previous_position=previous_position,
    )


def make_exposure(
    lap_number: int,
    *,
    close_fraction: float,
    residual: float = 0.0,
    tyre_age: int = 10,
    stint: int = 1,
) -> TrafficExposure:
    lap = make_lap(lap_number, residual=residual, tyre_age=tyre_age, stint=stint)
    valid = 100.0
    close = close_fraction * valid
    return TrafficExposure(
        lap=lap,
        valid_seconds=valid,
        known_ahead_seconds=valid,
        close_seconds_by_threshold=((100.0, close), (150.0, close), (200.0, close)),
        sustained_close_seconds_by_threshold=((100.0, close), (150.0, close), (200.0, close)),
        dominant_driver_ahead="63",
    )


def test_interval_overlap_is_exact_and_non_negative():
    event = EventWindow("VSC", 10.0, 20.0, 1, 1)
    assert _interval_overlap_seconds(0.0, 5.0, event) == 0.0
    assert _interval_overlap_seconds(5.0, 15.0, event) == 5.0
    assert _interval_overlap_seconds(12.0, 18.0, event) == 6.0


def test_compute_exposure_uses_elapsed_time_not_sample_count():
    telemetry = pd.DataFrame(
        {
            "SessionTime": pd.to_timedelta([0, 1, 2, 4], unit="s"),
            "Time": pd.to_timedelta([0, 1, 2, 4], unit="s"),
            "DistanceToDriverAhead": [100.0, 100.0, 200.0, 200.0],
            "DriverAhead": [63, 63, 63, 63],
        }
    )
    valid, known, close, sustained, ahead_seconds, gap_error, empty_error = _compute_exposure(
        telemetry, [], (150.0,), 2.0
    )
    assert math.isclose(valid, 4.0)
    assert math.isclose(known, 4.0)
    assert math.isclose(close[150.0], 2.0)
    assert math.isclose(sustained[150.0], 2.0)
    assert math.isclose(ahead_seconds["63"], 4.0)
    assert not gap_error
    assert not empty_error


def test_compute_exposure_rejects_large_telemetry_gap():
    telemetry = pd.DataFrame(
        {
            "SessionTime": pd.to_timedelta([0, 1, 4], unit="s"),
            "Time": pd.to_timedelta([0, 1, 4], unit="s"),
            "DistanceToDriverAhead": [100.0, 100.0, 100.0],
            "DriverAhead": [63, 63, 63],
        }
    )
    valid, known, close, sustained, ahead_seconds, gap_error, empty_error = _compute_exposure(
        telemetry, [], (150.0,), 2.0
    )
    assert valid == 0.0
    assert known == 0.0
    assert gap_error
    assert not empty_error


def test_compute_exposure_does_not_count_event_overlap():
    telemetry = pd.DataFrame(
        {
            "SessionTime": pd.to_timedelta([0, 1, 2, 3], unit="s"),
            "Time": pd.to_timedelta([0, 1, 2, 3], unit="s"),
            "DistanceToDriverAhead": [100.0, 100.0, 100.0, 200.0],
            "DriverAhead": [63, 63, 63, 63],
        }
    )
    event = EventWindow("SC", 1.5, 2.5, 2, 3)
    valid, known, close, sustained, _ahead_seconds, gap_error, empty_error = _compute_exposure(
        telemetry, [event], (150.0,), 2.0
    )
    assert math.isclose(valid, 1.0)
    assert math.isclose(known, 1.0)
    assert math.isclose(close[150.0], 1.0)
    assert math.isclose(sustained[150.0], 1.0)
    assert not gap_error
    assert not empty_error


def test_compute_exposure_requires_driver_ahead_and_distance_for_known_time():
    telemetry = pd.DataFrame(
        {
            "SessionTime": pd.to_timedelta([0, 1, 2], unit="s"),
            "Time": pd.to_timedelta([0, 1, 2], unit="s"),
            "DistanceToDriverAhead": [100.0, None, 100.0],
            "DriverAhead": [63, None, 63],
        }
    )
    valid, known, close, _sustained, _ahead_seconds, gap_error, empty_error = _compute_exposure(
        telemetry, [], (150.0,), 2.0
    )
    assert math.isclose(valid, 2.0)
    assert math.isclose(known, 1.0)
    assert math.isclose(close[150.0], 1.0)
    assert not gap_error
    assert not empty_error


def test_classify_marks_low_ahead_coverage_unknown_not_clear():
    exposure = make_exposure(5, close_fraction=0.0)
    partial = TrafficExposure(
        lap=exposure.lap,
        valid_seconds=100.0,
        known_ahead_seconds=40.0,
        close_seconds_by_threshold=exposure.close_seconds_by_threshold,
        sustained_close_seconds_by_threshold=exposure.sustained_close_seconds_by_threshold,
        dominant_driver_ahead=None,
    )
    assert _classify(partial, 150.0, 0.25, 0.50) == "unknown"


def test_tyre_age_falls_back_when_tyre_life_missing():
    assert _tyre_age({"TyreLife": None}, 7) == 7
    assert _tyre_age({"TyreLife": 12}, 7) == 12


def test_parse_thresholds_is_deterministic():
    assert parse_thresholds("200, 100, 150, 150") == (100.0, 150.0, 200.0)


def test_match_requires_same_driver_stint_compound_and_close_tyre_age():
    close = make_exposure(20, close_fraction=0.6, residual=1.5, tyre_age=10, stint=2)
    clear_same = make_exposure(15, close_fraction=0.0, residual=0.5, tyre_age=10, stint=2)
    clear_other_stint = make_exposure(16, close_fraction=0.0, residual=-3.0, tyre_age=10, stint=1)
    clear_other_driver = TrafficExposure(
        lap=make_lap(17, driver="63", residual=-4.0, tyre_age=10, stint=2),
        valid_seconds=100.0,
        known_ahead_seconds=100.0,
        close_seconds_by_threshold=((100.0, 0.0), (150.0, 0.0), (200.0, 0.0)),
        sustained_close_seconds_by_threshold=((100.0, 0.0), (150.0, 0.0), (200.0, 0.0)),
        dominant_driver_ahead="44",
    )
    matches, unmatched, close_count, clear_count = _match_exposures(
        [close, clear_same, clear_other_stint, clear_other_driver],
        threshold=150.0,
        min_close_fraction=0.25,
        min_ahead_coverage=0.50,
        max_lap_distance=10,
        max_tyre_age_diff=1,
        max_clean_air_reuse=1,
    )
    assert close_count == 1
    assert clear_count == 3
    assert unmatched == 0
    assert len(matches) == 1
    assert matches[0].clean_lap == 15
    assert math.isclose(matches[0].traffic_delta_seconds, 1.0)


def test_match_rejects_large_tyre_age_difference():
    close = make_exposure(20, close_fraction=0.6, residual=1.0, tyre_age=10)
    clear = make_exposure(15, close_fraction=0.0, residual=0.0, tyre_age=13)
    matches, unmatched, _close, _clear = _match_exposures(
        [close, clear],
        threshold=150.0,
        min_close_fraction=0.25,
        min_ahead_coverage=0.50,
        max_lap_distance=10,
        max_tyre_age_diff=1,
        max_clean_air_reuse=1,
    )
    assert matches == []
    assert unmatched == 1


def test_clean_air_lap_is_not_reused_when_reuse_limit_is_one():
    close_a = make_exposure(20, close_fraction=0.6, residual=1.0, tyre_age=10)
    close_b = make_exposure(21, close_fraction=0.7, residual=2.0, tyre_age=10)
    clear = make_exposure(15, close_fraction=0.0, residual=0.0, tyre_age=10)
    matches, unmatched, _close, _clear = _match_exposures(
        [close_a, close_b, clear],
        threshold=150.0,
        min_close_fraction=0.25,
        min_ahead_coverage=0.50,
        max_lap_distance=10,
        max_tyre_age_diff=1,
        max_clean_air_reuse=1,
    )
    assert len(matches) == 1
    assert unmatched == 1


def test_race_balanced_summary_weights_races_equally():
    class MatchFactory:
        @staticmethod
        def make(race_id: int, delta: float):
            from audit_race_strategy_traffic_v1 import TrafficMatch

            return TrafficMatch(
                race_id=race_id,
                season_year=2024,
                round_number=1,
                regulation_era="2022-2025",
                driver_key="44",
                driver_label="Lewis Hamilton",
                stint_number=1,
                compound="MEDIUM",
                close_lap=20,
                clean_lap=15,
                close_tyre_age=10,
                clean_tyre_age=10,
                close_fraction=0.5,
                close_sustained_seconds=10.0,
                traffic_delta_seconds=delta,
                position_delta=0.0,
            )

    matches = [MatchFactory.make(1, 1.0), MatchFactory.make(1, 3.0), MatchFactory.make(2, 10.0)]
    rows = _race_balanced_summary(
        matches,
        threshold=150.0,
        unmatched_close=0,
        min_races_for_gate=1,
        min_pairs_for_gate=1,
        min_era_races_for_gate=1,
        min_era_pairs_for_gate=1,
    )
    overall = rows[0]
    assert math.isclose(overall["mean_traffic_delta_seconds_race_balanced"], 6.0)
    assert overall["races"] == 2
    assert overall["matched_pairs"] == 3


def test_prepare_laps_accounts_for_pit_and_event_before_generic_invalid_flags():
    class FakeLaps:
        def __init__(self, frame):
            self._frame = frame

        def iterrows(self):
            return self._frame.iterrows()

    class FakeSession:
        def __init__(self, frame):
            self.laps = FakeLaps(frame)

    frame = pd.DataFrame(
        [
            {
                "DriverNumber": "44",
                "LapNumber": 4,
                "LapTime": pd.Timedelta(seconds=90),
                "LapStartTime": pd.Timedelta(seconds=270),
                "Time": pd.Timedelta(seconds=360),
                "PitInTime": pd.NaT,
                "PitOutTime": pd.NaT,
                "IsAccurate": True,
                "Deleted": False,
                "Stint": 1,
                "Compound": "MEDIUM",
                "TyreLife": 4,
                "Position": 5,
            },
            {
                "DriverNumber": "44",
                "LapNumber": 5,
                "LapTime": pd.Timedelta(seconds=90),
                "LapStartTime": pd.Timedelta(seconds=360),
                "Time": pd.Timedelta(seconds=450),
                "PitInTime": pd.Timestamp("2024-01-01"),
                "PitOutTime": pd.NaT,
                "IsAccurate": True,
                "Deleted": False,
                "Stint": 1,
                "Compound": "MEDIUM",
                "TyreLife": 5,
                "Position": 5,
            },
            {
                "DriverNumber": "44",
                "LapNumber": 6,
                "LapTime": pd.Timedelta(seconds=90),
                "LapStartTime": pd.Timedelta(seconds=450),
                "Time": pd.Timedelta(seconds=540),
                "PitInTime": pd.NaT,
                "PitOutTime": pd.NaT,
                "IsAccurate": True,
                "Deleted": True,
                "Stint": 1,
                "Compound": "MEDIUM",
                "TyreLife": 6,
                "Position": 5,
            },
            {
                "DriverNumber": "44",
                "LapNumber": 7,
                "LapTime": pd.Timedelta(seconds=90),
                "LapStartTime": pd.Timedelta(seconds=540),
                "Time": pd.Timedelta(seconds=630),
                "PitInTime": pd.NaT,
                "PitOutTime": pd.NaT,
                "IsAccurate": True,
                "Deleted": False,
                "Stint": 1,
                "Compound": "MEDIUM",
                "TyreLife": 7,
                "Position": 5,
            },
            {
                "DriverNumber": "44",
                "LapNumber": 8,
                "LapTime": pd.Timedelta(seconds=90),
                "LapStartTime": pd.Timedelta(seconds=630),
                "Time": pd.Timedelta(seconds=720),
                "PitInTime": pd.NaT,
                "PitOutTime": pd.NaT,
                "IsAccurate": True,
                "Deleted": True,
                "Stint": 1,
                "Compound": "MEDIUM",
                "TyreLife": 8,
                "Position": 5,
            },
        ]
    )

    counters = AuditCounters()
    records = _prepare_laps(
        FakeSession(frame),
        RaceMeta(1, 2024, 1, "2022-2025", False),
        [EventWindow("SC", 455.0, 475.0, 6, 6)],
        counters,
        min_field_laps=1,
        first_laps_to_exclude=0,
    )

    assert counters.pit_excluded == 1
    assert counters.event_excluded == 1
    assert counters.invalid_lap_excluded == 1
    assert [record.lap_number for record in records] == [4, 7]
