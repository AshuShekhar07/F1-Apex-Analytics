import math

from sqlalchemy import create_engine, text

from race_strategy_backtest_v2 import (
    BacktestRow,
    HistoricalStrategy,
    brier,
    candidates_from_options,
    historical_strategy_options,
    load_history,
    paired_bootstrap_ci,
    strategy_metrics,
    summarise,
)
from race_strategy_simulator_v1 import Strategy, StrategyStint

MH = ("MEDIUM", "HARD")


def _s(sequence, stops, laps=50):
    bounds = [0, *stops, laps]
    return Strategy("x", tuple(StrategyStint(sequence[i], bounds[i] + 1, bounds[i + 1]) for i in range(len(sequence))))


def test_options_rank_by_frequency_with_median_stops():
    history = (
        [HistoricalStrategy(MH, (0.40,)), HistoricalStrategy(MH, (0.50,)), HistoricalStrategy(MH, (0.44,))]
        + [HistoricalStrategy(("SOFT", "HARD"), (0.30,))]
        + [HistoricalStrategy(("HARD",), ())]                        # no-stop: illegal in the dry
        + [HistoricalStrategy(("INTERMEDIATE", "MEDIUM"), (0.2,))]  # wet: excluded
        + [HistoricalStrategy(("MEDIUM", "MEDIUM"), (0.5,))]        # one compound: illegal
    )
    options = historical_strategy_options(history, 50)
    assert [(s.sequence, s.stop_laps, w) for s, w in options] == [
        (MH, (22,), 3.0),          # median 0.44 * 50
        (("SOFT", "HARD"), (15,), 1.0),
    ]


def test_candidates_shift_historical_stops_and_dedupe():
    options = [(_s(MH, [22]), 3.0), (_s(MH, [22]), 1.0)]
    candidates = candidates_from_options(options, 50, offsets=(-3, 0, 3))
    assert [c.stop_laps for c in candidates] == [(19,), (22,), (25,)]


def test_candidates_drop_impossible_two_stop_shifts():
    options = [(_s(("SOFT", "MEDIUM", "HARD"), [3, 4]), 1.0)]
    candidates = candidates_from_options(options, 50, offsets=(-6, 0))
    assert [c.stop_laps for c in candidates] == [(3, 4)]   # -6 collapses both stops to lap 2


def test_strategy_metrics():
    actual = _s(MH, [20])
    assert strategy_metrics(_s(MH, [24]), actual) == (1, 1, 4.0)
    assert strategy_metrics(_s(("SOFT", "MEDIUM", "HARD"), [15, 32]), actual) == (0, 0, 5.0)
    assert strategy_metrics(_s(MH, [24]), None) == (None, None, None)


def test_brier_and_bootstrap():
    assert abs(brier(0.8, 1) - 0.04) < 1e-12
    diff, low, high = paired_bootstrap_ci([-1.0, -2.0, -1.5, -0.5])
    assert diff == -1.25 and low <= diff <= high < 0
    assert all(math.isnan(x) for x in paired_bootstrap_ci([]))


def _row(race_id, grid, finish, v2_expected, v2_p1, base_p1=0.5):
    return BacktestRow(race_id, 2024, grid, finish, int(finish == 1), "MEDIUM → HARD", "s", v2_expected, v2_p1,
                       1, 1, 2.0, base_p1, "b", 0, 1, 5.0, "green:track_era", 19)


def test_summary_compares_with_external_baselines():
    rows = [_row(1, 1, 1, 1.2, 0.9), _row(2, 1, 3, 2.5, 0.3), _row(3, 1, 1, 1.5, 0.7)]
    s = summarise(rows)
    assert s["races"] == 3
    assert s["finish_mae"]["grid_baseline"] == 2 / 3          # grid 1 -> finishes 1, 3, 1
    assert abs(s["finish_mae"]["v2"] - (0.2 + 0.5 + 0.5) / 3) < 1e-9
    assert s["win_brier"]["v2"] < s["win_brier"]["pole_rate_baseline"]
    assert s["sequence_match"]["v2"] == (1.0, 3) and s["sequence_match"]["era_mode_baseline"] == (0.0, 3)


def test_load_history_is_era_scoped_dry_and_before_target_year(fixture_db_url):
    engine = create_engine(fixture_db_url)
    with engine.connect() as db:
        trans = db.begin()
        # race 3 (2022, era2, dry) and race 4 (2023, era2, dry); race 2 (2021) is wet
        for race_id, driver, stints in [
            (3, 1, [("MEDIUM", 1, 20), ("HARD", 21, 50)]),
            (3, 2, [("SOFT", 1, 10)]),                         # retired: < 90% distance
            (4, 1, [("SOFT", 1, 15), ("HARD", 16, 50)]),       # 2023: after a 2023 target's cutoff
        ]:
            entry = db.execute(text("SELECT id FROM race_entries WHERE race_id = :r AND driver_id = :d"),
                               {"r": race_id, "d": driver}).scalar()
            for n, (compound, start, end) in enumerate(stints, start=1):
                db.execute(text("""
                    INSERT INTO race_stints (race_id, race_entry_id, stint_number, compound, start_lap, end_lap, stint_length)
                    VALUES (:r, :e, :n, :c, :a, :b, :b - :a + 1)
                """), {"r": race_id, "e": entry, "n": n, "c": compound, "a": start, "b": end})
        history = load_history(db, era="era2_18inch_groundeffect", before_year=2023)
        trans.rollback()
    engine.dispose()
    assert history == [HistoricalStrategy(MH, (0.4,))]
