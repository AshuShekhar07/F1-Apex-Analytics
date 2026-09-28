from datetime import date

import pytest

from show_forecast import Style, parse_laps, parse_strategy, pct, render, result_summary, stint_bar

DRIVERS = [
    {"driver_id": 1, "driver_name": "Lando Norris"}, {"driver_id": 2, "driver_name": "Max Verstappen"},
    {"driver_id": 3, "driver_name": "Driver 1"}, {"driver_id": 4, "driver_name": "Driver 10"},
]


def test_parse_laps_and_strategy():
    assert parse_laps("20-22, 40", 50) == (20, 21, 22, 40)
    assert parse_laps(None, 50) == ()
    with pytest.raises(ValueError):
        parse_laps("45-60", 50)
    assert parse_strategy("norris:medium-hard@18", DRIVERS) == (1, {"sequence": ["MEDIUM", "HARD"], "stop_laps": [18]})
    assert parse_strategy("2:SOFT-MEDIUM-HARD@12,35", DRIVERS)[1]["stop_laps"] == [12, 35]
    assert parse_strategy("Driver 1:MEDIUM-HARD@8", DRIVERS)[0] == 3          # exact name beats "Driver 10"
    with pytest.raises(ValueError):
        parse_strategy("Norris-MEDIUM", DRIVERS)
    with pytest.raises(ValueError):
        parse_strategy("Driver:MEDIUM-HARD@8", DRIVERS)                        # ambiguous


def test_formatting_helpers_without_colour():
    plain = Style(color=False)
    assert stint_bar(["MEDIUM", "HARD"], [10], 40, plain, width=20) == "M" * 5 + "H" * 15
    assert pct(0.001) == "<1%" and pct(0.456) == "46%"


def _data(finish=True):
    drivers = [
        {"driver_id": d, "driver_name": f"Driver {d}", "team_name": "T", "color_hex": "#FF8000", "grid": d,
         "predicted_position": d, "p_win": 0.5 if d == 1 else 0.05, "p_podium": 0.8 if d <= 3 else 0.1,
         "finish": ({1: 2, 2: 1}.get(d, d) if finish else None), "status": "Finished"}
        for d in range(1, 11)
    ]
    forecast = {"as_of_date": date(2026, 8, 23), "grid_source": "starting_grid",
                "inputs": {"total_laps": 50, "data_notes": ["a note"],
                           "strategy_options": [{"sequence": ["MEDIUM", "HARD"], "stop_laps": [20], "weight": 30},
                                                {"sequence": ["SOFT", "HARD"], "stop_laps": [15], "weight": 10}]},
                "validation": {"predicted_position": {"mean_abs_error_places": 3.34,
                                                       "grid_only_mean_abs_error_places": 3.49,
                                                       "finishers_off_by_10_or_more": 0.018}}}
    race = {"season_year": 2026, "round_number": 12, "track": "Zandvoort", "country": "Netherlands",
            "race_date": date(2026, 8, 23)}
    return {"race": race, "forecast": forecast, "drivers": drivers}


def test_result_summary_and_render():
    s = result_summary(_data()["drivers"])
    assert not s["winner_hit"] and s["winner_predicted"] == 2 and s["podium_overlap"] == 3
    assert s["mae"] == pytest.approx(0.2)
    assert result_summary(_data(finish=False)["drivers"]) is None
    out = render(_data(), Style(color=False))
    assert "2026 Round 12 · Zandvoort" in out and "Actual" in out
    assert "75%" in out and "25%" in out                    # strategy shares normalised from counts
    assert "winner Driver 2 was predicted P2" in out and "a note" in out
    assert "\x1b[" not in out                               # no colour codes when colour is off
    upcoming = render(_data(finish=False), Style(color=False))
    assert "Actual" not in upcoming and "Result vs forecast" not in upcoming
