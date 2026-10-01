from race_strategy_pace_db_adapter_v1 import load_race_pace_observations


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class FakeDB:
    def __init__(self, rows):
        self.rows = rows
        self.sql = ""

    def execute(self, statement, params=None):
        self.sql = str(statement)
        return FakeResult(self.rows)


def test_loads_persistent_driver_track_and_era_identity():
    db = FakeDB([
        {
            "race_id": 10,
            "track_id": 3,
            "season_year": 2024,
            "regulation_era": "era2_18inch_groundeffect",
            "driver_id": 44,
            "lap_number": 20,
            "lap_time": 91.2,
            "tire_compound": "MEDIUM",
            "rainfall": False,
        }
    ])

    observations, warnings = load_race_pace_observations(db)

    assert len(observations) == 1
    row = observations[0]
    assert row.driver_key == "44"
    assert row.track_id == 3
    assert row.regulation_era == "era2_18inch_groundeffect"
    assert row.compound == "MEDIUM"
    assert warnings == ("Only 1 clean race-pace laps matched the requested filter",)


def test_era_filter_is_passed_to_query_parameters():
    db = FakeDB([])

    observations, warnings = load_race_pace_observations(db, era="era1_13inch", start_year=2020, end_year=2022)

    assert observations == ()
    assert warnings == ("No clean dry race-pace observations matched the requested filter",)
