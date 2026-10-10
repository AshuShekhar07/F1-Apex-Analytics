from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.database import get_db

router = APIRouter(prefix="/races", tags=["practice"])

VALID_SESSIONS = {"FP1", "FP2", "FP3"}


@router.get("/{race_id}/practice/{session_type}")
def get_practice_results(race_id: int, session_type: str, db: Session = Depends(get_db)):
    session_type = session_type.upper()
    if session_type not in VALID_SESSIONS:
        raise HTTPException(status_code=422, detail="session_type must be one of FP1, FP2, FP3")

    race = db.execute(text("""
        SELECT r.id, r.season_year, r.round_number, t.name AS track_name
        FROM races r JOIN tracks t ON r.track_id = t.id
        WHERE r.id = :race_id
    """), {"race_id": race_id}).mappings().first()
    if race is None:
        raise HTTPException(status_code=404, detail="Race not found")

    rows = db.execute(text("""
        WITH session_pick AS (
            SELECT s.id AS session_id
            FROM sessions s
            WHERE s.race_id = :race_id AND s.session_type = :session_type
        ),
        -- rank per race entry, not per driver: FP1 reserve/rookie drivers have
        -- their own entry (role != 'race_driver') and belong in the classification.
        -- Laps deleted for track limits (FastF1 enrichment) do not count, matching
        -- official timing; unenriched laps have is_deleted NULL and still count.
        eligible_laps AS (
            SELECT l.*
            FROM laps l
            WHERE l.session_id = (SELECT session_id FROM session_pick)
              AND l.lap_time IS NOT NULL
              AND COALESCE(l.is_deleted, false) = false
        ),
        lap_counts AS (
            SELECT l.race_entry_id, COUNT(l.id) AS total_laps
            FROM laps l
            WHERE l.session_id = (SELECT session_id FROM session_pick)
            GROUP BY l.race_entry_id
        ),
        best_laps AS (
            SELECT el.race_entry_id, MIN(el.lap_time) AS best_lap_time
            FROM eligible_laps el
            GROUP BY el.race_entry_id
        ),
        best_lap_detail AS (
            SELECT DISTINCT ON (el.race_entry_id)
                el.race_entry_id, el.sector_1_time, el.sector_2_time, el.sector_3_time, el.tire_compound
            FROM eligible_laps el
            ORDER BY el.race_entry_id, el.lap_time ASC, el.lap_number ASC
        ),
        track_record AS (
            SELECT MIN(l.lap_time) AS record_time
            FROM laps l
            JOIN sessions s ON l.session_id = s.id
            JOIN races ra ON s.race_id = ra.id
            WHERE ra.track_id = (SELECT track_id FROM races WHERE id = :race_id)
              AND l.is_valid = true
              AND s.session_type = 'R'
              AND NOT (ra.season_year = 2020 AND ra.round_number = 16)
        )
        SELECT
            RANK() OVER (ORDER BY bl.best_lap_time ASC) AS position,
            d.id AS driver_id,
            d.name AS driver,
            re.role,
            tm.name AS team,
            tm.logo_url,
            bl.best_lap_time,
            bl.best_lap_time - MIN(bl.best_lap_time) OVER () AS gap_to_fastest,
            lc.total_laps,
            bld.tire_compound,
            bld.sector_1_time,
            bld.sector_2_time,
            bld.sector_3_time,
            bl.best_lap_time - (SELECT record_time FROM track_record) AS delta_to_track_record
        FROM best_laps bl
        JOIN race_entries re ON re.id = bl.race_entry_id AND re.race_id = :race_id
        JOIN drivers d ON d.id = re.driver_id
        JOIN best_lap_detail bld ON bld.race_entry_id = bl.race_entry_id
        JOIN lap_counts lc ON lc.race_entry_id = bl.race_entry_id
        JOIN teams tm ON re.team_id = tm.id
        ORDER BY position, d.name
    """), {"race_id": race_id, "session_type": session_type}).mappings().all()

    return {
        "race": dict(race),
        "session_type": session_type,
        "track_record_note": (
            "delta_to_track_record compares with the fastest valid race lap stored for this "
            "circuit since 2018 (any layout), not the official FIA lap record"
        ),
        "results": [dict(r) for r in rows],
    }


@router.get("/{race_id}/long-runs")
def get_long_runs(race_id: int, db: Session = Depends(get_db)):
    """Friday long-run pace: each driver's race-trim pace from practice (FP2, else FP1 on a
    sprint weekend), as the gap to the best driver. Long runs are 5+ consecutive laps within
    10% of the driver's best lap, trimmed of traffic laps (race_practice_pace_v1). Fuel loads
    are team choices, so read it as a guide. Empty when practice was wet or has no long runs."""
    from collections import defaultdict
    from statistics import median

    from race_practice_pace_v1 import long_run_laps, practice_gaps, session_is_wet

    race = db.execute(text("SELECT id FROM races WHERE id = :r"), {"r": race_id}).scalar()
    if race is None:
        raise HTTPException(status_code=404, detail="Race not found")
    for session_type in ("FP2", "FP1"):
        session = db.execute(text("""
            SELECT s.id, COALESCE(sw.rainfall, FALSE) AS wet
            FROM sessions s LEFT JOIN session_weather sw ON sw.session_id = s.id
            WHERE s.race_id = :r AND s.session_type = :t
        """), {"r": race_id, "t": session_type}).mappings().first()
        if session is None:
            continue
        if session_is_wet(db, session["id"], session["wet"]):
            return {"race_id": race_id, "session": session_type, "wet": True, "drivers": []}
        laps = defaultdict(list)
        for driver, n, t, c in db.execute(text("""
            SELECT re.driver_id, l.lap_number, l.lap_time, l.tire_compound
            FROM laps l JOIN race_entries re ON re.id = l.race_entry_id
            WHERE l.session_id = :s AND COALESCE(l.is_deleted, FALSE) = FALSE
        """), {"s": session["id"]}):
            laps[int(driver)].append((int(n), float(t) if t is not None else None, c))
        runs = {d: r for d, rows in laps.items() if (r := long_run_laps(rows))}
        gaps = practice_gaps(runs)
        if not gaps:
            return {"race_id": race_id, "session": session_type, "wet": False, "drivers": []}
        # a gap in seconds: the fraction applied to the quickest median on the most-run compound
        by_compound = defaultdict(list)
        for d, comp in runs.items():
            for c, times in comp.items():
                by_compound[c].append(median(times))
        main = max(by_compound, key=lambda c: len(by_compound[c]))
        reference = min(by_compound[main])
        info = {int(r["id"]): r for r in db.execute(text("""
            SELECT d.id, d.name, tm.name AS team_name, tm.color_hex
            FROM race_entries re JOIN drivers d ON d.id = re.driver_id JOIN teams tm ON tm.id = re.team_id
            WHERE re.race_id = :r AND re.driver_id = ANY(:ids)
        """), {"r": race_id, "ids": list(gaps)}).mappings().all()}
        drivers = []
        for d, gap in sorted(gaps.items(), key=lambda x: x[1]):
            row = info.get(d, {})
            drivers.append({
                "driver_id": d, "driver_name": row.get("name"), "team_name": row.get("team_name"),
                "color_hex": row.get("color_hex"), "gap_pct": round(gap * 100, 3),
                "gap_s": round(gap * reference, 3),
                "compounds": {c: {"laps": len(t), "median": round(median(t), 3)} for c, t in runs[d].items()},
            })
        return {"race_id": race_id, "session": session_type, "wet": False, "reference_compound": main,
                "reference_lap": round(reference, 3), "drivers": drivers,
                "note": "Race-trim pace from practice long runs; fuel loads are team choices, so treat as a guide."}
    return {"race_id": race_id, "session": None, "wet": False, "drivers": []}
