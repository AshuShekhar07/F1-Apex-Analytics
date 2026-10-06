from datetime import date, timedelta

from race_strategy_timing_v1 import CarStrategy, Stint
from race_winning_strategy_v1 import clean_front_cars, format_strategy, predict_winning_strategy
from winning_strategy_backtest_v1 import run, score_car, summarise, M
from test_race_strategy_backtest_v2 import synthetic_db  # noqa: F401  (fixture)

START = date(2023, 3, 1)


def _car(race, track, grid, plan, stops, *, era="e", laps=60, week=None, finish=None, wet=False):
    bounds = [0, *stops, laps]
    when = START + timedelta(weeks=week if week is not None else race)
    return CarStrategy(race, when, when.year, track, era, grid, wet, laps,
                       tuple(Stint(c, bounds[i] + 1, bounds[i + 1]) for i, c in enumerate(plan)), None, finish)


ONE = ("MEDIUM", "HARD")
TWO = ("MEDIUM", "HARD", "HARD")


def _history():
    cars = []
    # track 1: front grid two-stops, stops around laps 20 and 42; the rest of the field one-stops
    for race in (1, 2, 3):
        for grid, (a, b) in zip(range(1, 6), ((19, 41), (20, 42), (21, 43), (22, 44), (20, 42))):
            cars.append(_car(race, 1, grid, TWO, [a, b]))
        for grid in range(6, 20):
            cars.append(_car(race, 1, grid, ONE, [30]))
    # elsewhere the era's front grid one-stops around lap 30
    for race in range(4, 12):
        for grid in range(1, 6):
            cars.append(_car(race, 100 + race, grid, ONE, [28 + grid % 3]))
    return cars


def test_front_grid_plan_and_windows_come_from_this_track():
    history = clean_front_cars(_history(), {})
    assert all(c.grid <= 5 for c in history)
    p = predict_winning_strategy(history, track_id=1, regulation_era="e", total_laps=60)
    assert p.sequence == TWO and p.stops == 2 and p.window_source == "track"
    (w1, w2) = p.windows
    assert (w1.first_lap, w1.last_lap) == (20, 21) and (w2.first_lap, w2.last_lap) == (42, 43)
    assert p.track_races == 3 and p.track_cars == 15
    text = format_strategy(p)
    assert "First pit window: laps 20-21" in text and "Second pit window: laps 42-43" in text


def test_new_track_gets_the_eras_front_grid_plan_with_one_window():
    p = predict_winning_strategy(clean_front_cars(_history(), {}), track_id=999, regulation_era="e", total_laps=60)
    assert p.sequence == ONE and len(p.windows) == 1 and p.window_source == "era"
    assert "no earlier race here" in p.source


def test_thin_track_history_leans_on_the_era():
    cars = [c for c in _history() if not (c.track_id == 1 and c.race_id > 1)]   # one race at track 1
    p = predict_winning_strategy(clean_front_cars(cars, {}), track_id=1, regulation_era="e", total_laps=60)
    # 5 track cars two-stop vs the era's answer worth 5 cars, mostly one-stop: still two stops by a hair
    assert p.stop_chances[2] > p.stop_chances[1]
    p = predict_winning_strategy(clean_front_cars(cars, {}), track_id=1, regulation_era="e", total_laps=60, k=20)
    assert p.sequence == ONE


def test_safety_car_stops_are_left_out_and_unknown_windows_kept():
    sc_car = _car(1, 1, 1, TWO, [10, 42])
    green_car = _car(1, 1, 2, TWO, [20, 42])
    kept = clean_front_cars([sc_car, green_car, _car(1, 1, 9, ONE, [30]), _car(1, 1, 3, ONE, [30], wet=True)],
                            {1: frozenset(range(9, 12))})
    assert kept == [green_car]
    assert clean_front_cars([sc_car], {1: None}) == [sc_car]


def test_window_scoring_ignores_safety_car_stops():
    car = _car(50, 1, 1, TWO, [20, 45], finish=1)
    row = score_car(50, "W", car, sequence=TWO, windows=[(19, 22), (40, 44)], first_stop=21,
                    neutralised=frozenset(range(45, 47)))
    v = row.values
    assert v["first_window_hit"] == 1 and v["second_window_hit"] is None     # second stop was under SC
    assert v["stop_count_hit"] == 1 and v["stop_count_hit_green"] is None
    assert v["first_stop_within_3"] == 1 and v["first_window_width"] == 4 and v["winner_sequence_hit"] == 1
    unknown = score_car(50, "W", car, sequence=ONE, windows=[(19, 22)], first_stop=21, neutralised=None).values
    assert unknown["first_window_hit"] is None and unknown["stop_count_hit"] == 0


def test_backtest_end_to_end(synthetic_db):
    with synthetic_db.connect() as db:
        rows = run(db, start_year=2024, end_year=2024)
    assert rows and {r.method for r in rows} == {"W winning strategy (track)", "E era front grid", M}
    table = summarise(rows, M)
    assert {t[0] for t in table} >= {"stop_count_hit", "first_window_hit", "winner_sequence_hit"}
