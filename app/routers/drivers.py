from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.database import get_db
from race_status import DNF_STATUSES


class DriverSeasonInfo(BaseModel):
    id: int
    name: str
    nationality: Optional[str] = None
    photo_url: Optional[str] = None


class DriverSeasonResponse(BaseModel):
    driver: DriverSeasonInfo
    season: int
    races: int
    wins: int
    podiums: int
    race_points: float
    sprint_points: float
    total_points: float
    average_finish: Optional[float] = None
    average_qualifying: Optional[float] = None
    dnfs: int


router = APIRouter(prefix="/drivers", tags=["drivers"])


@router.get("/{driver_id}/history")
def get_driver_history(
    driver_id: int = Path(..., ge=1),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
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

    history_base_query = """
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
    """

    career_stats = db.execute(text("""
        SELECT
            COUNT(*) AS races,
            COUNT(*) FILTER (
                WHERE rr.finishing_position = 1
            ) AS wins,
            COUNT(*) FILTER (
                WHERE rr.finishing_position BETWEEN 1 AND 3
            ) AS podiums,
            COALESCE(SUM(rr.points), 0) AS race_points
        FROM race_entries re
        JOIN races r
          ON r.id = re.race_id
        LEFT JOIN race_results rr
          ON rr.race_entry_id = re.id
         AND rr.session_id = (
             SELECT id
             FROM sessions
             WHERE race_id = r.id
               AND session_type = 'R'
             LIMIT 1
         )
        WHERE re.driver_id = :id
          AND re.role = 'race_driver'
    """), {"id": driver_id}).mappings().first()

    history = db.execute(
        text(history_base_query + " LIMIT :limit OFFSET :offset"),
        {"id": driver_id, "limit": limit, "offset": offset},
    ).mappings().all()

    total_points = float(career_stats["race_points"] or 0)
    wins = int(career_stats["wins"] or 0)
    podiums = int(career_stats["podiums"] or 0)

    sprint_points_row = db.execute(text("""
        SELECT SUM(rr.points) AS sprint_points
        FROM race_results rr
        JOIN sessions s ON s.id = rr.session_id AND s.session_type = 'S'
        JOIN race_entries re ON re.id = rr.race_entry_id
        WHERE re.driver_id = :id AND re.role = 'race_driver'
    """), {"id": driver_id}).mappings().first()
    sprint_points = float(sprint_points_row["sprint_points"]) if sprint_points_row["sprint_points"] else 0.0

    return {
        **dict(driver),
        "current_car_number": current_number_row["current_number"] if current_number_row else None,
        "career_summary": {
            "races": int(career_stats["races"] or 0),
            "wins": wins,
            "podiums": podiums,
            "race_points": float(total_points),
            "sprint_points": sprint_points,
            "total_points": float(total_points) + sprint_points,
        },
        "race_history": list(history),
    }


@router.get("/{driver_id}/season/{season}", response_model=DriverSeasonResponse)
def get_driver_season(
    driver_id: int = Path(..., ge=1),
    season: int = Path(..., ge=2018, le=2026),
    db: Session = Depends(get_db),
):
    driver = db.execute(text("""
        SELECT id, name, nationality, photo_url
        FROM drivers
        WHERE id = :id
    """), {"id": driver_id}).mappings().first()

    if driver is None:
        raise HTTPException(status_code=404, detail="Driver not found")

    race_stats = db.execute(text("""
        SELECT
            COUNT(*) AS races,
            COUNT(*) FILTER (
                WHERE rr.finishing_position = 1
            ) AS wins,
            COUNT(*) FILTER (
                WHERE rr.finishing_position <= 3
            ) AS podiums,
            COALESCE(SUM(rr.points), 0) AS race_points,
            AVG(rr.finishing_position) FILTER (
                WHERE rr.finishing_position IS NOT NULL
            ) AS average_finish,
            AVG(qr.final_position) FILTER (
                WHERE qr.final_position IS NOT NULL
            ) AS average_qualifying
        FROM race_entries re
        JOIN races r
          ON r.id = re.race_id
        LEFT JOIN sessions sr
          ON sr.race_id = r.id
         AND sr.session_type = 'R'
        LEFT JOIN race_results rr
          ON rr.race_entry_id = re.id
         AND rr.session_id = sr.id
        LEFT JOIN sessions sq
          ON sq.race_id = r.id
         AND sq.session_type = 'Q'
        LEFT JOIN qualifying_results qr
          ON qr.race_entry_id = re.id
         AND qr.session_id = sq.id
        WHERE re.driver_id = :driver_id
          AND re.role = 'race_driver'
          AND r.season_year = :season
    """), {
        "driver_id": driver_id,
        "season": season,
    }).mappings().first()

    if race_stats is None or race_stats["races"] == 0:
        raise HTTPException(
            status_code=404,
            detail=f"No race data found for driver {driver_id} in {season}",
        )

    dnf_count = db.execute(text("""
        SELECT COUNT(*)
        FROM race_entries re
        JOIN races r
          ON r.id = re.race_id
        JOIN sessions s
          ON s.race_id = r.id
         AND s.session_type = 'R'
        JOIN race_results rr
          ON rr.race_entry_id = re.id
         AND rr.session_id = s.id
        WHERE re.driver_id = :driver_id
          AND re.role = 'race_driver'
          AND r.season_year = :season
          AND rr.status = ANY(:dnf_statuses)
    """), {
        "driver_id": driver_id,
        "season": season,
        "dnf_statuses": list(DNF_STATUSES),
    }).scalar()

    sprint_points = db.execute(text("""
        SELECT COALESCE(SUM(rr.points), 0)
        FROM race_entries re
        JOIN races r
          ON r.id = re.race_id
        JOIN race_results rr
          ON rr.race_entry_id = re.id
        JOIN sessions s
          ON s.id = rr.session_id
         AND s.session_type = 'S'
        WHERE re.driver_id = :driver_id
          AND re.role = 'race_driver'
          AND r.season_year = :season
    """), {
        "driver_id": driver_id,
        "season": season,
    }).scalar()

    race_points = float(race_stats["race_points"] or 0)
    sprint_points = float(sprint_points or 0)

    return {
        "driver": dict(driver),
        "season": season,
        "races": int(race_stats["races"]),
        "wins": int(race_stats["wins"] or 0),
        "podiums": int(race_stats["podiums"] or 0),
        "race_points": round(race_points, 1),
        "sprint_points": round(sprint_points, 1),
        "total_points": round(race_points + sprint_points, 1),
        "average_finish": (
            round(float(race_stats["average_finish"]), 2)
            if race_stats["average_finish"] is not None
            else None
        ),
        "average_qualifying": (
            round(float(race_stats["average_qualifying"]), 2)
            if race_stats["average_qualifying"] is not None
            else None
        ),
        "dnfs": int(dnf_count or 0),
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
        "track_records_note": (
            "fastest valid race lap stored per circuit since 2018 (any layout), "
            "not official FIA lap records"
        ),
        "last_gp_win": dict(last_win) if last_win else None,
    }


@router.get("/roster/{season}")
def get_season_roster(season: int = Path(..., ge=2018, le=2100), db: Session = Depends(get_db)):
    """Every race driver of a season with their latest team, photo and stats: this season's
    and their career since 2018 (the seasons in the database). Points include sprints; poles
    are qualifying P1; DNFs count retirements (race_status.DNF_STATUSES)."""
    roster = db.execute(text("""
        SELECT DISTINCT ON (re.driver_id)
               re.driver_id, re.car_number, d.name, d.nationality, d.photo_url, d.country_code,
               d.total_world_championships, tm.id AS team_id, tm.name AS team_name, tm.color_hex, tm.logo_url
        FROM race_entries re
        JOIN races r ON r.id = re.race_id
        JOIN drivers d ON d.id = re.driver_id
        JOIN teams tm ON tm.id = re.team_id
        WHERE r.season_year = :s AND re.role = 'race_driver'
        ORDER BY re.driver_id, r.round_number DESC
    """), {"s": season}).mappings().all()
    if not roster:
        raise HTTPException(status_code=404, detail=f"No drivers found for {season}")
    ids = [int(r["driver_id"]) for r in roster]
    results = db.execute(text("""
        SELECT re.driver_id, r.season_year, s.session_type, rr.finishing_position, rr.points, rr.status
        FROM race_results rr
        JOIN sessions s ON s.id = rr.session_id AND s.session_type IN ('R', 'S')
        JOIN race_entries re ON re.id = rr.race_entry_id
        JOIN races r ON r.id = s.race_id
        WHERE re.driver_id = ANY(:ids) AND re.role = 'race_driver'
    """), {"ids": ids}).mappings().all()
    qualifying = db.execute(text("""
        SELECT re.driver_id, r.season_year, q.final_position
        FROM qualifying_results q
        JOIN sessions s ON s.id = q.session_id AND s.session_type = 'Q'
        JOIN race_entries re ON re.id = q.race_entry_id
        JOIN races r ON r.id = s.race_id
        WHERE re.driver_id = ANY(:ids) AND re.role = 'race_driver' AND q.final_position IS NOT NULL
    """), {"ids": ids}).mappings().all()

    def stats(driver_id: int, only_season: int | None) -> dict:
        def keep(row) -> bool:
            return row["driver_id"] == driver_id and (only_season is None or row["season_year"] == only_season)
        races = [r for r in results if keep(r) and r["session_type"] == "R"]
        finishes = [int(r["finishing_position"]) for r in races if r["finishing_position"] is not None]
        quali = [int(q["final_position"]) for q in qualifying if keep(q)]
        return {
            "races": len(races),
            "wins": sum(f == 1 for f in finishes),
            "podiums": sum(f <= 3 for f in finishes),
            "points": round(sum(float(r["points"] or 0) for r in results if keep(r)), 1),
            "poles": sum(p == 1 for p in quali),
            "best_finish": min(finishes) if finishes else None,
            "average_finish": round(sum(finishes) / len(finishes), 2) if finishes else None,
            "average_qualifying": round(sum(quali) / len(quali), 2) if quali else None,
            "dnfs": sum(r["status"] in DNF_STATUSES for r in races),
        }

    out = []
    for r in roster:
        driver_id = int(r["driver_id"])
        out.append({
            "id": driver_id, "name": r["name"], "number": r["car_number"], "nationality": r["nationality"],
            "country_code": r["country_code"], "photo_url": r["photo_url"],
            "world_championships": int(r["total_world_championships"] or 0),
            "team": {"id": r["team_id"], "name": r["team_name"], "color_hex": r["color_hex"],
                     "logo_url": r["logo_url"]},
            "season": stats(driver_id, season),
            "career_since_2018": stats(driver_id, None),
        })
    out.sort(key=lambda d: (-d["season"]["points"], d["name"]))
    return {"season": season, "drivers": out,
            "note": "career figures cover the seasons in the database (2018 onwards)"}
