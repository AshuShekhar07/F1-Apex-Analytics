"""Real-data checks for the Phase 2 data layer (APEX21_REAL_DB_TESTS=1).

Where enrichment coverage is missing the test skips rather than fails, so a
skip means "backfill needed", not "passed".
"""

from statistics import median

import pytest
from sqlalchemy import text

from app.database import SessionLocal
from pirelli_compounds_v1 import run_audit as compound_audit
from race_neutralisations_v1 import load_race_neutralisations
from race_strategy_pit_loss_v2 import load_observations


@pytest.fixture(scope="module")
def db():
    with SessionLocal() as session:
        yield session


def _race(db, season, *patterns):
    clauses = " OR ".join(f"t.name ILIKE :p{i} OR t.country ILIKE :p{i}" for i in range(len(patterns)))
    params = {f"p{i}": p for i, p in enumerate(patterns)} | {"season": season}
    ids = db.execute(text(
        f"SELECT r.id FROM races r JOIN tracks t ON t.id = r.track_id WHERE r.season_year = :season AND ({clauses})"
    ), params).scalars().all()
    assert len(ids) == 1, f"expected one {season} race for {patterns}, found {ids}"
    return ids[0]


def _neutralisations_or_skip(db, race_id):
    result = load_race_neutralisations(db, race_id)
    if result.coverage != "ok":
        pytest.skip(f"race {race_id}: {result.coverage} (run the events / lap-metadata backfill)")
    return result


# --- neutralisation windows --------------------------------------------------

def test_2021_abu_dhabi_late_safety_car(db):
    """Latifi's crash on lap 53 of 58 brought out the Safety Car that ran to the final lap."""
    result = _neutralisations_or_skip(db, _race(db, 2021, "%Yas Marina%", "%Abu Dhabi%"))
    late = [e for e in result.events if e.event_type == "SC" and e.start_lap and e.start_lap >= 50]
    assert len(late) == 1, result.events
    assert late[0].start_lap in (53, 54) and late[0].end_lap in (57, 58), late[0]


def test_2021_belgian_gp_red_flag(db):
    result = _neutralisations_or_skip(db, _race(db, 2021, "%Spa%", "%Belgium%"))
    assert result.count("RED_FLAG") >= 1


def test_all_windows_lie_inside_the_race(db):
    race_ids = db.execute(text("""
        SELECT DISTINCT s.race_id FROM session_track_status_intervals i
        JOIN sessions s ON s.id = i.session_id AND s.session_type = 'R'
    """)).scalars().all()
    if not race_ids:
        pytest.skip("no track-status intervals stored")
    bad = []
    for race_id in race_ids:
        result = load_race_neutralisations(db, race_id)
        for e in result.events:
            if result.coverage == "ok" and not (1 <= e.start_lap <= e.end_lap <= result.total_laps):
                bad.append((race_id, e))
    assert bad == []


# --- pit loss v2 -------------------------------------------------------------

@pytest.fixture(scope="module")
def pit_observations(db):
    observations = load_observations(db, start_year=2018, end_year=2026)
    if not any(o.condition == "green" for o in observations):
        pytest.skip("no green-flag stops with track status (run the lap-metadata backfill)")
    return observations


def _era_median(observations, era, condition):
    values = [o.loss_seconds for o in observations
              if o.regulation_era == era and o.condition == condition and o.loss_seconds is not None]
    return (median(values), len(values)) if len(values) >= 8 else (None, len(values))


def test_green_pit_loss_is_physically_plausible(pit_observations):
    for era in sorted({o.regulation_era for o in pit_observations}):
        loss, n = _era_median(pit_observations, era, "green")
        if loss is not None:
            assert 10.0 <= loss <= 40.0, (era, loss, n)


def test_pit_loss_is_less_than_pit_lane_time(db, pit_observations):
    """Loss excludes the stretch of track the car would have driven anyway."""
    lane = dict(db.execute(text("""
        SELECT r.regulation_era, percentile_cont(0.5) WITHIN GROUP (ORDER BY p.total_pit_lane_seconds)
        FROM race_strategy_pit_stops p JOIN races r ON r.id = p.race_id GROUP BY r.regulation_era
    """)).all())
    compared = 0
    for era, lane_median in lane.items():
        loss, _ = _era_median(pit_observations, era, "green")
        if loss is not None:
            compared += 1
            assert loss < float(lane_median), (era, loss, lane_median)
    if not compared:
        pytest.skip("no era with both v1 pit-lane times and enough green v2 stops")


def test_safety_car_stops_cost_less_than_green_stops(pit_observations):
    compared = 0
    for era in sorted({o.regulation_era for o in pit_observations}):
        green, _ = _era_median(pit_observations, era, "green")
        sc, _ = _era_median(pit_observations, era, "sc")
        if green is not None and sc is not None:
            compared += 1
            assert sc < green, (era, sc, green)
    if not compared:
        pytest.skip("not enough SC stops in any era")


# --- compounds ---------------------------------------------------------------

def test_compound_nominations_are_valid(db):
    _, problems = compound_audit(db, start_year=2018, end_year=2026)
    assert problems == []
