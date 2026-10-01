from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel
from typing import Optional
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from race_status import DNF_STATUSES


class TeamSeasonDriver(BaseModel):
    driver_id: int
    driver_name: str
    car_number: Optional[int] = None
    races_entered: int


class TeamSeasonResponse(BaseModel):
    team_id: int
    team_name: str
    color_hex: Optional[str] = None
    logo_url: Optional[str] = None
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
    drivers: list[TeamSeasonDriver]


router = APIRouter(prefix="/teams", tags=["Teams"])


@router.get("/{team_id}/season/{season}", response_model=TeamSeasonResponse)
def team_season_summary(
    team_id: int = Path(..., ge=1),
    season: int = Path(..., ge=2018, le=2026),
    db: Session = Depends(get_db),
):
    team = db.execute(
        text("""
            SELECT
                id,
                name,
                color_hex,
                logo_url
            FROM teams
            WHERE id = :team_id
        """),
        {"team_id": team_id},
    ).mappings().first()

    if team is None:
        raise HTTPException(status_code=404, detail="Team not found")

    race_stats = db.execute(
        text("""
            SELECT
                COUNT(DISTINCT r.id) AS races,
                COUNT(*) FILTER (
                    WHERE rr.finishing_position = 1
                ) AS wins,
                COUNT(*) FILTER (
                    WHERE rr.finishing_position BETWEEN 1 AND 3
                ) AS podiums,
                COALESCE(SUM(rr.points), 0) AS race_points,
                AVG(rr.finishing_position) AS average_finish
            FROM race_results rr
            JOIN race_entries re
                ON re.id = rr.race_entry_id
            JOIN races r
                ON r.id = re.race_id
            JOIN sessions s
                ON s.id = rr.session_id
            WHERE re.team_id = :team_id
              AND re.role = 'race_driver'
              AND s.session_type = 'R'
              AND r.season_year = :season
        """),
        {
            "team_id": team_id,
            "season": season,
        },
    ).mappings().first()

    sprint_stats = db.execute(
        text("""
            SELECT
                COALESCE(SUM(rr.points), 0) AS sprint_points
            FROM race_results rr
            JOIN race_entries re
                ON re.id = rr.race_entry_id
            JOIN races r
                ON r.id = re.race_id
            JOIN sessions s
                ON s.id = rr.session_id
            WHERE re.team_id = :team_id
              AND re.role = 'race_driver'
              AND s.session_type = 'S'
              AND r.season_year = :season
        """),
        {
            "team_id": team_id,
            "season": season,
        },
    ).mappings().first()

    qualifying_stats = db.execute(
        text("""
            SELECT
                AVG(qr.final_position) AS average_qualifying
            FROM qualifying_results qr
            JOIN race_entries re
                ON re.id = qr.race_entry_id
            JOIN races r
                ON r.id = re.race_id
            JOIN sessions s
                ON s.id = qr.session_id
            WHERE re.team_id = :team_id
              AND re.role = 'race_driver'
              AND s.session_type = 'Q'
              AND r.season_year = :season
        """),
        {
            "team_id": team_id,
            "season": season,
        },
    ).mappings().first()

    dnf_stats = db.execute(
        text("""
            SELECT
                COUNT(*) AS dnfs
            FROM race_results rr
            JOIN race_entries re
                ON re.id = rr.race_entry_id
            JOIN races r
                ON r.id = re.race_id
            JOIN sessions s
                ON s.id = rr.session_id
            WHERE re.team_id = :team_id
              AND re.role = 'race_driver'
              AND s.session_type = 'R'
              AND r.season_year = :season
              AND rr.status = ANY(:dnf_statuses)
        """),
        {
            "team_id": team_id,
            "season": season,
            "dnf_statuses": list(DNF_STATUSES),
        },
    ).scalar() or 0

    drivers = db.execute(
        text("""
            SELECT
                d.id AS driver_id,
                d.name AS driver_name,
                MIN(re.car_number) AS car_number,
                COUNT(DISTINCT r.id) AS races_entered
            FROM race_results rr
            JOIN sessions s
                ON s.id = rr.session_id
            JOIN race_entries re
                ON re.id = rr.race_entry_id
            JOIN drivers d
                ON d.id = re.driver_id
            JOIN races r
                ON r.id = re.race_id
            WHERE re.team_id = :team_id
              AND re.role = 'race_driver'
              AND s.session_type = 'R'
              AND r.season_year = :season
            GROUP BY d.id, d.name
            ORDER BY d.name
        """),
        {
            "team_id": team_id,
            "season": season,
        },
    ).mappings().all()

    race_points = float(race_stats["race_points"] or 0)
    sprint_points = float(sprint_stats["sprint_points"] or 0)

    return {
        "team_id": team["id"],
        "team_name": team["name"],
        "color_hex": team["color_hex"],
        "logo_url": team["logo_url"],
        "season": season,
        "races": int(race_stats["races"] or 0),
        "wins": int(race_stats["wins"] or 0),
        "podiums": int(race_stats["podiums"] or 0),
        "race_points": race_points,
        "sprint_points": sprint_points,
        "total_points": race_points + sprint_points,
        "average_finish": (
            round(float(race_stats["average_finish"]), 2)
            if race_stats["average_finish"] is not None
            else None
        ),
        "average_qualifying": (
            round(float(qualifying_stats["average_qualifying"]), 2)
            if qualifying_stats["average_qualifying"] is not None
            else None
        ),
        "dnfs": int(dnf_stats),
        "drivers": [
            {
                "driver_id": driver["driver_id"],
                "driver_name": driver["driver_name"],
                "car_number": driver["car_number"],
                "races_entered": int(driver["races_entered"]),
            }
            for driver in drivers
        ],
    }
