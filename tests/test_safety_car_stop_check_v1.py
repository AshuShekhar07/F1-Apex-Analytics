from datetime import date

from race_strategy_timing_v1 import CarStrategy, Stint
from safety_car_stop_check_v1 import classify, neutralised_stops, report, run, stop_in_laps
from test_race_strategy_backtest_v2 import synthetic_db  # noqa: F401  (fixture)


def _car(stops, laps=50):
    bounds = [0, *stops, laps]
    compounds = ("MEDIUM", "HARD", "HARD", "SOFT")
    return CarStrategy(1, date(2024, 5, 1), 2024, 1, "e", 5, False, laps,
                       tuple(Stint(compounds[i], bounds[i] + 1, bounds[i + 1]) for i in range(len(stops) + 1)))


def test_stops_under_a_window_are_counted_by_in_lap():
    car = _car([18, 34])
    assert stop_in_laps(car) == [18, 34]
    assert neutralised_stops(car, frozenset(range(33, 36))) == 1
    assert neutralised_stops(car, frozenset({19, 20})) == 0          # out-lap only: not a stop under SC


def test_misses_are_split_by_what_the_neutralisation_explains():
    sc = frozenset(range(33, 36))
    assert classify(2, _car([18, 34]), sc) == "right"
    assert classify(1, _car([18, 34]), sc) == "explained"            # extra stop was the SC stop
    assert classify(1, _car([10, 20, 34]), sc) == "helped"            # still one green stop too many
    assert classify(1, _car([18, 30]), sc) == "untouched"
    assert classify(None, _car([18]), sc) == "no prediction"


def test_report_runs_end_to_end(synthetic_db, capsys):
    with synthetic_db.connect() as db:
        rows = run(db, start_year=2024, end_year=2024)
    assert rows and all(r.group in ("right", "explained", "helped", "untouched", "no prediction") for r in rows)
    report(rows, "test")
    out = capsys.readouterr().out
    assert "SAFETY-CAR CHECK" in out and "by race type" in out
