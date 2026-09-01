from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.database import get_db

router = APIRouter(prefix="/drivers", tags=["drivers"])


@router.get("/{driver_id}/history")
def get_driver_history(driver_id: int, db: Session = Depends(get_db)):
    driver = db.execute(text("""
        SELECT id, name, nationality, permanent_number
        FROM drivers WHERE id = :id
    """), {"id": driver_id}).mappings().first()

    if driver is None:
        raise HTTPException(status_code=404, detail="Driver not found")

    current_number_row = db.execute(text("""
        SELECT DISTINCT ON (re.driver_id)
            re.car_number AS current_number, r.season_year
        FROM race_entries re
        JOIN races r ON r.id = re.race_id
        WHERE re.driver_id = :id AND re.role = 'race_driver'
        ORDER BY re.driver_id, r.season_year DESC, r.round_number DESC
    """), {"id": driver_id}).mappings().first()

    history = db.execute(text("""
        SELECT r.season_year, r.round_number, t.name AS track_name,
               tm.name AS team_name, tm.color_hex,
               rr.finishing_position, rr.points, rr.status
        FROM race_entries re
        JOIN races r ON r.id = re.race_id
        JOIN tracks t ON t.id = r.track_id
        JOIN teams tm ON tm.id = re.team_id
        LEFT JOIN race_results rr ON rr.race_entry_id = re.id
            AND rr.session_id = (
                SELECT id FROM sessions
                WHERE race_id = r.id AND session_type = 'R'
                LIMIT 1
            )
        WHERE re.driver_id = :id AND re.role = 'race_driver'
        ORDER BY r.season_year, r.round_number
    """), {"id": driver_id}).mappings().all()

    total_points = sum(row["points"] or 0 for row in history)
    wins = sum(1 for row in history if row["finishing_position"] == 1)
    podiums = sum(1 for row in history if row["finishing_position"] and row["finishing_position"] <= 3)

    return {
        **dict(driver),
        "current_car_number": current_number_row["current_number"] if current_number_row else None,
        "career_summary": {
            "races": len(history),
            "wins": wins,
            "podiums": podiums,
            "total_points": total_points,
        },
        "race_history": list(history),
    }


@router.get("/{driver_id}/profile")
def get_driver_profile(driver_id: int, db: Session = Depends(get_db)):
    driver = db.execute(text("""
        SELECT id, name, nationality, photo_url, country_code, total_world_championships
        FROM drivers WHERE id = :id
    """), {"id": driver_id}).mappings().first()
    if driver is None:
        raise HTTPException(status_code=404, detail="Driver not found")

    current_team = db.execute(text("""
        SELECT tm.id, tm.name, tm.color_hex, tm.logo_url
        FROM race_entries re
        JOIN races r ON r.id = re.race_id
        JOIN teams tm ON tm.id = re.team_id
        WHERE re.driver_id = :id AND re.role = 'race_driver'
        ORDER BY r.season_year DESC, r.round_number DESC
        LIMIT 1
    """), {"id": driver_id}).mappings().first()

    team_history_2018plus = db.execute(text("""
        WITH driver_team_seasons AS (
            SELECT DISTINCT tm.id AS team_id, tm.name AS team_name, tm.logo_url,
                   ra.season_year
            FROM race_entries re
            JOIN teams tm ON re.team_id = tm.id
            JOIN races ra ON re.race_id = ra.id
            WHERE re.driver_id = :id AND re.role = 'race_driver'
        ),
        ranked AS (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY team_id ORDER BY season_year) AS rn
            FROM driver_team_seasons
        ),
        grouped AS (
            SELECT *, season_year - rn AS grp
            FROM ranked
        )
        SELECT team_id, team_name, logo_url,
               MIN(season_year) AS start_year, MAX(season_year) AS end_year
        FROM grouped
        GROUP BY team_id, team_name, logo_url, grp
        ORDER BY start_year
    """), {"id": driver_id}).mappings().all()

    team_history_pre2018 = db.execute(text("""
        SELECT team_name, team_logo_url AS logo_url, start_year, end_year
        FROM driver_team_history_manual
        WHERE driver_id = :id
        ORDER BY start_year
    """), {"id": driver_id}).mappings().all()

    combined_team_history = sorted(
        [dict(row) for row in team_history_pre2018] + [dict(row) for row in team_history_2018plus],
        key=lambda r: r["start_year"]
    )

    track_records = db.execute(text("""
        WITH fastest_per_track AS (
            SELECT DISTINCT ON (t.id)
                t.id AS track_id, t.name AS track_name,
                d.id AS driver_id, l.lap_time, ra.season_year
            FROM laps l
            JOIN sessions s ON l.session_id = s.id
            JOIN races ra ON s.race_id = ra.id
            JOIN tracks t ON ra.track_id = t.id
            JOIN race_entries re ON l.race_entry_id = re.id
            JOIN drivers d ON re.driver_id = d.id
            WHERE l.is_valid = true
              AND l.lap_time IS NOT NULL
              AND s.session_type = 'R'
              AND NOT (ra.season_year = 2020 AND ra.round_number = 16)
            ORDER BY t.id, l.lap_time ASC
        )
        SELECT track_name, lap_time, season_year
        FROM fastest_per_track
        WHERE driver_id = :id
        ORDER BY track_name
    """), {"id": driver_id}).mappings().all()

    last_win = db.execute(text("""
        SELECT t.name AS track_name, ra.season_year, ra.round_number, ra.race_date
        FROM race_results rr
        JOIN sessions s ON rr.session_id = s.id
        JOIN races ra ON s.race_id = ra.id
        JOIN tracks t ON ra.track_id = t.id
        JOIN race_entries re ON rr.race_entry_id = re.id
        WHERE re.driver_id = :id
          AND rr.finishing_position = 1
          AND s.session_type = 'R'
        ORDER BY ra.race_date DESC
        LIMIT 1
    """), {"id": driver_id}).mappings().first()

    return {
        "id": driver["id"],
        "name": driver["name"],
        "nationality": driver["nationality"],
        "photo_url": driver["photo_url"],
        "total_world_championships": driver["total_world_championships"],
        "current_team": dict(current_team) if current_team else None,
        "team_history": combined_team_history,
        "track_records_held": [dict(r) for r in track_records],
        "last_gp_win": dict(last_win) if last_win else None,
    }
