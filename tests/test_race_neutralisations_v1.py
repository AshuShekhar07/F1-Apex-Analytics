import pytest
from sqlalchemy import create_engine, text

from race_neutralisations_v1 import (
    NeutralisationEvent,
    StatusInterval,
    build_events,
    leader_lap_ends,
    load_race_neutralisations,
    map_to_leader_laps,
    run_audit,
)


def test_vsc_and_vsc_ending_form_one_event():
    events = build_events([
        StatusInterval(0, 100, "1"),
        StatusInterval(100, 160, "6"),
        StatusInterval(160, 175, "7"),
        StatusInterval(175, None, "1"),
    ])
    assert events == [NeutralisationEvent("VSC", 100, 175)]


def test_separate_periods_stay_separate_and_other_codes_ignored():
    events = build_events([
        StatusInterval(0, 50, "1"),
        StatusInterval(50, 80, "4"),
        StatusInterval(80, 90, "2"),   # yellow between two SC periods
        StatusInterval(90, 120, "4"),
        StatusInterval(120, 130, "5"),
        StatusInterval(130, None, "1"),
    ])
    assert [(e.event_type, e.start_seconds, e.end_seconds) for e in events] == [
        ("SC", 50, 80), ("SC", 90, 120), ("RED_FLAG", 120, 130),
    ]


def test_touching_duplicate_intervals_merge():
    events = build_events([StatusInterval(10, 20, "4"), StatusInterval(20, 30, "4")])
    assert events == [NeutralisationEvent("SC", 10, 30)]


def test_leader_clock_ignores_backmarker_boundaries():
    rows = [
        # leader
        (1, 100.0, 95.0), (2, 195.0, 94.0), (3, 289.0, 94.0),
        # backmarker, a lap down by lap 3
        (1, 101.0, 99.0), (2, 200.0, 99.0),
    ]
    assert leader_lap_ends(rows) == {1: 195.0, 2: 289.0, 3: 383.0}


def test_mapping_to_leader_laps():
    ends = {1: 195.0, 2: 289.0, 3: 383.0}
    [sc, open_ended] = map_to_leader_laps(
        [NeutralisationEvent("SC", 200.0, 300.0), NeutralisationEvent("VSC", 350.0, None)], ends,
    )
    assert (sc.start_lap, sc.end_lap, sc.laps_affected) == (2, 3, 2)
    assert (open_ended.start_lap, open_ended.end_lap) == (3, 3)


# --- database adapter -------------------------------------------------------

@pytest.fixture
def db(fixture_db_url):
    engine = create_engine(fixture_db_url)
    with engine.connect() as conn:
        trans = conn.begin()
        yield conn
        trans.rollback()
    engine.dispose()


def _seed_race_1(db):
    session = db.execute(text("SELECT id FROM sessions WHERE race_id = 1 AND session_type = 'R'")).scalar()
    # every car starts lap 1 at t=100 and each later lap as its previous lap ended
    db.execute(text("""
        UPDATE laps l SET lap_start_time_seconds = 100 + COALESCE((
            SELECT SUM(p.lap_time) FROM laps p
            WHERE p.session_id = l.session_id AND p.race_entry_id = l.race_entry_id
              AND p.lap_number < l.lap_number), 0)
        WHERE l.session_id = :s
    """), {"s": session})
    for start, end, code in [(0, 150, "1"), (150, 210, "6"), (210, 220, "7"), (220, 250, "1"),
                             (250, 330, "4"), (330, None, "1")]:
        db.execute(text("""
            INSERT INTO session_track_status_intervals (session_id, start_time_seconds, end_time_seconds, status_code)
            VALUES (:s, :a, :b, :c)
        """), {"s": session, "a": start, "b": end, "c": code})
    db.execute(text("UPDATE races SET safety_car_periods = 3, vsc_periods = 1, red_flags = 0 WHERE id = 1"))


def test_load_race_neutralisations(db):
    _seed_race_1(db)
    result = load_race_neutralisations(db, 1)
    # leader (A): lap 1 ends 195.0, lap 2 ends 289.5, lap 3 ends 384.3
    assert result.coverage == "ok"
    assert [(e.event_type, e.start_lap, e.end_lap) for e in result.events] == [("VSC", 1, 2), ("SC", 2, 3)]
    assert result.neutralised_laps() == frozenset({1, 2, 3})
    assert result.total_laps == 3


def test_coverage_states(db):
    assert load_race_neutralisations(db, 2).coverage == "no_status_intervals"
    assert load_race_neutralisations(db, 5).coverage == "no_race_session"


def test_audit_flags_sample_count_mismatch(db):
    _seed_race_1(db)
    by_season, rows = run_audit(db, start_year=2021, end_year=2021)
    assert by_season[2021]["ok"] == 1
    assert by_season[2021]["stored_count_mismatch"] == 1  # stored 3 SC "periods", derived 1
    assert {r["derived_sc_vsc_red"] for r in rows} == {"1/1/0"}
