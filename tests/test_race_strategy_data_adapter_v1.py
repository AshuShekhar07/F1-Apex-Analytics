from race_strategy_data_adapter_v1 import (
    load_event_observations,
    load_tyre_observations,
    load_unavailable_inputs,
)


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class FakeDB:
    def __init__(self, responses):
        self.responses = list(responses)
        self.sql = []

    def execute(self, statement, params=None):
        self.sql.append((str(statement), params or {}))
        return FakeResult(self.responses.pop(0))


def test_event_adapter_maps_known_rain_onset_to_wet_exposure():
    db = FakeDB([
        [{
            "race_id": 1,
            "season_year": 2024,
            "round_number": 5,
            "total_race_laps": 50,
            "safety_car_periods": 2,
            "vsc_periods": 1,
            "red_flags": 0,
            "rainfall": True,
            "rain_onset_lap": 31,
        }]
    ])

    observations, warnings = load_event_observations(db, era="era2_18inch_groundeffect")

    assert len(observations) == 1
    assert observations[0].total_laps == 50
    assert observations[0].wet_laps == 20
    assert observations[0].safety_car_count == 2
    assert warnings == ()


def test_event_adapter_does_not_invent_wet_exposure_when_onset_is_missing():
    db = FakeDB([
        [{
            "race_id": 2,
            "season_year": 2024,
            "round_number": 7,
            "total_race_laps": 60,
            "safety_car_periods": 1,
            "vsc_periods": 0,
            "red_flags": 0,
            "rainfall": True,
            "rain_onset_lap": None,
        }]
    ])

    observations, warnings = load_event_observations(db)

    assert observations[0].wet_laps == 0
    assert any("no rain_onset_lap" in warning for warning in warnings)


def test_tyre_adapter_derives_age_and_delta_from_a_stint():
    db = FakeDB([
        [
            {"race_id": 1, "race_entry_id": 10, "stint_number": 1, "compound": "MEDIUM", "start_lap": 1, "end_lap": 5, "lap_number": 1, "lap_time": 90.0},
            {"race_id": 1, "race_entry_id": 10, "stint_number": 1, "compound": "MEDIUM", "start_lap": 1, "end_lap": 5, "lap_number": 2, "lap_time": 90.1},
            {"race_id": 1, "race_entry_id": 10, "stint_number": 1, "compound": "MEDIUM", "start_lap": 1, "end_lap": 5, "lap_number": 3, "lap_time": 90.3},
            {"race_id": 1, "race_entry_id": 10, "stint_number": 1, "compound": "MEDIUM", "start_lap": 1, "end_lap": 5, "lap_number": 4, "lap_time": 90.4},
            {"race_id": 1, "race_entry_id": 10, "stint_number": 1, "compound": "MEDIUM", "start_lap": 1, "end_lap": 5, "lap_number": 5, "lap_time": 90.6},
        ]
    ])

    observations, warnings = load_tyre_observations(db, era="era2_18inch_groundeffect", min_stint_laps=5)

    assert [o.tyre_age_laps for o in observations] == [1, 2, 3, 4]
    assert observations[0].lap_time_delta_seconds == 0.05
    assert observations[-1].lap_time_delta_seconds == 0.55
    assert any("Only 4 tyre observations" in warning for warning in warnings)


def test_unavailable_inputs_are_explicit():
    pit, pace, warnings = load_unavailable_inputs()
    assert pit == ()
    assert pace == ()
    assert len(warnings) == 2
    assert all("not" in warning.lower() or "required" in warning.lower() for warning in warnings)
