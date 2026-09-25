"""Resume gates that let the FastF1 enrichment backfill skip finished sessions."""

from sqlalchemy import create_engine, text

from backfill_fastf1_enrichment_v1 import has_status_intervals, lap_metadata_coverage


def test_resume_gates(fixture_db_url):
    engine = create_engine(fixture_db_url)
    with engine.connect() as db:
        trans = db.begin()
        session = db.execute(text("SELECT id FROM sessions WHERE race_id = 1 AND session_type = 'R'")).scalar()
        assert lap_metadata_coverage(db, session) == 0.0
        assert not has_status_intervals(db, session)

        db.execute(text("UPDATE laps SET lap_start_time_seconds = 1 WHERE session_id = :s AND lap_number > 1"),
                    {"s": session})
        assert 0.0 < lap_metadata_coverage(db, session) < 0.95   # partial -> not skipped
        db.execute(text("UPDATE laps SET lap_start_time_seconds = 1 WHERE session_id = :s"), {"s": session})
        assert lap_metadata_coverage(db, session) == 1.0

        db.execute(text("""
            INSERT INTO session_track_status_intervals (session_id, start_time_seconds, status_code)
            VALUES (:s, 0, '1')
        """), {"s": session})
        assert has_status_intervals(db, session)

        empty = db.execute(text("SELECT id FROM sessions WHERE race_id = 3 AND session_type = 'Q'")).scalar()
        assert lap_metadata_coverage(db, empty) == 0.0   # no laps at all
        trans.rollback()
    engine.dispose()
