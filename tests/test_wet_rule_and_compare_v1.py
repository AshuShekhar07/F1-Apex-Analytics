from sqlalchemy import text

import race_pace_from_quali_v1 as pace
from prediction_scoreboard_v1 import DriverScore, dump_scores, load_dump
from scoreboard_compare_v1 import compare
from test_race_strategy_backtest_v2 import synthetic_db  # noqa: F401  (fixture)


def test_production_rule_is_the_rain_flag():
    assert pace.WET_RULE == "rain_flag" and pace.SESSION_WET_SQL == pace.RAIN_FLAG_WET_SQL


def test_tyre_rule_ignores_a_rain_flag_without_wet_tyres(synthetic_db, monkeypatch):
    from race_practice_pace_v1 import session_is_wet

    with synthetic_db.connect() as db:
        tx = db.begin()
        try:
            s = db.execute(text("SELECT id FROM sessions WHERE race_id = 1 AND session_type = 'R'")).scalar()
            db.execute(text("UPDATE session_weather SET rainfall = TRUE WHERE session_id = :s"), {"s": s})

            def wet(sql):
                return db.execute(text(f"""SELECT {sql} FROM sessions s LEFT JOIN session_weather sw
                                           ON sw.session_id = s.id WHERE s.id = :s"""), {"s": s}).scalar()
            assert wet(pace.RAIN_FLAG_WET_SQL) is True and wet(pace.TYRE_WET_SQL) is False
            assert session_is_wet(db, s, True)
            monkeypatch.setattr(pace, "WET_RULE", "tyres")
            assert not session_is_wet(db, s, True)
        finally:
            tx.rollback()


def _scores(model, offset):
    out = []
    for race in range(1, 31):
        for d in range(1, 6):
            predicted = d if (race + offset) % 3 else (d % 5) + 1      # some races shuffled
            out.append(DriverScore(race, model, d, d, predicted, d, True, 0.6 if d == 1 else 0.1, 0.5))
    return out


def test_dump_round_trip_and_paired_compare(tmp_path):
    base, new = _scores("blend", 0), _scores("blend", 1)
    path = tmp_path / "scores.csv"
    dump_scores(base, str(path))
    assert load_dump(str(path)) == base
    rows = {r[0]: r for r in compare(base, base)}
    assert rows["mae"][3] == (0.0, 0.0, 0.0) and rows["mae"][4] == "no clear difference"
    rows = {r[0]: r for r in compare(base, new)}
    assert set(rows) >= {"winner_hit", "mae", "brier_win"}
    assert abs(rows["mae"][2] - rows["mae"][1] - rows["mae"][3][0]) < 1e-9
