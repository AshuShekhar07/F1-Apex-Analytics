from fastapi import APIRouter, Depends, HTTPException, Query, Path
from pydantic import BaseModel
from typing import Optional
from datetime import date, datetime
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.database import get_db
from race_status import is_classified, classify_status

router = APIRouter(prefix="/races", tags=["races"])

class PositionPoint(BaseModel):
    lap: int
    position: Optional[int] = None


class PositionBattleDriver(BaseModel):
    driver_id: int
    driver_name: str
    car_number: Optional[int] = None
    team_id: int
    team_name: str
    color_hex: Optional[str] = None
    starting_grid_position: Optional[int] = None
    finishing_position: Optional[int] = None
    status: Optional[str] = None
    position_data_available: bool
    positions: list[PositionPoint]
    positions_gained: Optional[int] = None
    missing_position_laps: int
    best_position: Optional[int] = None
    worst_position: Optional[int] = None


class PositionBattleResponse(BaseModel):
    race_id: int
    season_year: int
    round_number: int
    race_date: date
    track_id: int
    track_name: str
    results: list[PositionBattleDriver]


class RaceListItem(BaseModel):
    id: int
    season_year: int
    round_number: int
    race_date: date
    weekend_format: Optional[str] = None
    track_name: str
    country: Optional[str] = None


class RaceSession(BaseModel):
    id: int
    session_type: str
    start_time: datetime


class RaceResult(BaseModel):
    driver_id: int
    driver_name: str
    car_number: Optional[int] = None
    team_name: str
    color_hex: Optional[str] = None
    finishing_position: Optional[int] = None
    starting_grid_position: Optional[int] = None
    points: Optional[float] = None
    status: Optional[str] = None
    gap_to_winner_seconds: Optional[float] = None
    gap_to_winner_display: Optional[str] = None
    laps_completed: Optional[int] = None
    display: Optional[str] = None


class RaceDetail(BaseModel):
    id: int
    season_year: int
    round_number: int
    race_date: date
    weekend_format: Optional[str] = None
    winner_time_seconds: Optional[float] = None
    track_id: int
    track_name: str
    country: Optional[str] = None
    length_km: Optional[float] = None
    lap_record: Optional[str] = None
    sessions: list[RaceSession]
    results: list[RaceResult]


def format_absolute_time(total_seconds):
    if total_seconds is None:
        return None
    total_seconds = float(total_seconds)
    hours = int(total_seconds // 3600)
    minutes = int((total_seconds % 3600) // 60)
    seconds = total_seconds % 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:06.3f}"
    return f"{minutes}:{seconds:06.3f}"


def build_display_value(row, winner_time_seconds, leader_laps_completed):
    status = row["status"]
    if is_classified(status):
        if row["finishing_position"] == 1:
            return format_absolute_time(winner_time_seconds)

        lap_diff = None
        if leader_laps_completed is not None and row["laps_completed"] is not None:
            lap_diff = leader_laps_completed - row["laps_completed"]

        if lap_diff and lap_diff > 0:
            return f"+{lap_diff} Lap" if lap_diff == 1 else f"+{lap_diff} Laps"

        if row["gap_to_winner_display"]:
            return row["gap_to_winner_display"]
        return format_absolute_time(winner_time_seconds) if winner_time_seconds else status
    if status == 'Did not start':
        return 'DNS'
    if status == 'Withdrew':
        return 'WD'
    if status == 'Disqualified':
        return 'DSQ'
    return status


@router.get("", response_model=list[RaceListItem])
def list_races(
    season: int | None = Query(None, ge=2018, le=2026),
    db: Session = Depends(get_db)
):
    query = """
        SELECT r.id, r.season_year, r.round_number, r.race_date,
               r.weekend_format, t.name AS track_name, t.country
        FROM races r
        JOIN tracks t ON t.id = r.track_id
    """
    params = {}
    if season is not None:
        query += " WHERE r.season_year = :season"
        params["season"] = season
    query += " ORDER BY r.season_year, r.round_number"

    rows = db.execute(text(query), params).mappings().all()
    return list(rows)


@router.get("/{race_id}", response_model=RaceDetail)
def get_race(
    race_id: int = Path(..., ge=1),
    db: Session = Depends(get_db)
):
    race = db.execute(text("""
        SELECT r.id, r.season_year, r.round_number, r.race_date,
               r.weekend_format, r.winner_time_seconds,
               t.id AS track_id, t.name AS track_name,
               t.country, t.length_km, t.lap_record
        FROM races r
        JOIN tracks t ON t.id = r.track_id
        WHERE r.id = :id
    """), {"id": race_id}).mappings().first()

    if race is None:
        raise HTTPException(status_code=404, detail="Race not found")

    sessions = db.execute(text("""
        SELECT id, session_type, start_time
        FROM sessions
        WHERE race_id = :id
        ORDER BY start_time NULLS LAST
    """), {"id": race_id}).mappings().all()

    race_result_session = db.execute(text("""
        SELECT s.id FROM sessions s
        WHERE s.race_id = :id AND s.session_type = 'R'
    """), {"id": race_id}).scalar()

    results = []
    if race_result_session:
        raw_results = db.execute(text("""
            SELECT d.id AS driver_id, d.name AS driver_name, re.car_number,
                   tm.name AS team_name, tm.color_hex,
                   rr.finishing_position, rr.starting_grid_position,
                   rr.points, rr.status, rr.gap_to_winner_seconds, rr.gap_to_winner_display,
                   (SELECT MAX(l.lap_number) FROM laps l WHERE l.race_entry_id = re.id AND l.session_id = rr.session_id) AS laps_completed
            FROM race_results rr
            JOIN race_entries re ON re.id = rr.race_entry_id
            JOIN drivers d ON d.id = re.driver_id
            JOIN teams tm ON tm.id = re.team_id
            WHERE rr.session_id = :sid
            ORDER BY rr.finishing_position NULLS LAST
        """), {"sid": race_result_session}).mappings().all()

        leader_row = next((r for r in raw_results if r["finishing_position"] == 1), None)
        leader_laps_completed = leader_row["laps_completed"] if leader_row else None

        for row in raw_results:
            row_dict = dict(row)
            row_dict["display"] = build_display_value(row, race["winner_time_seconds"], leader_laps_completed)
            results.append(row_dict)

    return {
        **dict(race),
        "sessions": list(sessions),
        "results": results,
    }


@router.get("/{race_id}/strategy")
def get_race_strategy(race_id: int, db: Session = Depends(get_db)):
    race = db.execute(text("""
        SELECT
            r.id,
            r.season_year,
            r.round_number,
            r.race_date,
            r.weekend_format,
            t.id AS track_id,
            t.name AS track_name
        FROM races r
        JOIN tracks t ON t.id = r.track_id
        WHERE r.id = :id
    """), {"id": race_id}).mappings().first()

    if race is None:
        raise HTTPException(status_code=404, detail="Race not found")

    rows = db.execute(text("""
        SELECT
            d.id AS driver_id,
            d.name AS driver_name,
            re.car_number,
            tm.id AS team_id,
            tm.name AS team_name,
            tm.color_hex,
            rr.finishing_position,
            rr.status,
            rs.stint_number,
            rs.compound,
            rs.start_lap,
            rs.end_lap,
            rs.stint_length
        FROM race_stints rs
        JOIN race_entries re
          ON re.id = rs.race_entry_id
        JOIN drivers d
          ON d.id = re.driver_id
        JOIN teams tm
          ON tm.id = re.team_id
        JOIN sessions s
          ON s.race_id = rs.race_id
         AND s.session_type = 'R'
        JOIN race_results rr
          ON rr.race_entry_id = rs.race_entry_id
         AND rr.session_id = s.id
        WHERE rs.race_id = :race_id
        ORDER BY
            rr.finishing_position NULLS LAST,
            re.id,
            rs.stint_number
    """), {"race_id": race_id}).mappings().all()

    drivers = {}

    for row in rows:
        driver_id = row["driver_id"]

        if driver_id not in drivers:
            drivers[driver_id] = {
                "driver_id": driver_id,
                "driver_name": row["driver_name"],
                "car_number": row["car_number"],
                "team_id": row["team_id"],
                "team_name": row["team_name"],
                "color_hex": row["color_hex"],
                "finishing_position": row["finishing_position"],
                "status": row["status"],
                "stints": [],
            }

        drivers[driver_id]["stints"].append({
            "stint_number": row["stint_number"],
            "compound": (
                str(row["compound"]).upper()
                if row["compound"] is not None
                else None
            ),
            "start_lap": row["start_lap"],
            "end_lap": row["end_lap"],
            "stint_length": row["stint_length"],
        })

    results = []

    for driver in drivers.values():
        stints = driver["stints"]

        driver["pit_stops"] = max(len(stints) - 1, 0)
        driver["first_compound"] = stints[0]["compound"] if stints else None
        driver["final_compound"] = stints[-1]["compound"] if stints else None

        results.append(driver)

    return {
        "race_id": race_id,
        "season_year": race["season_year"],
        "round_number": race["round_number"],
        "race_date": race["race_date"],
        "weekend_format": race["weekend_format"],
        "track_id": race["track_id"],
        "track_name": race["track_name"],
        "results": results,
    }


@router.get("/{race_id}/position-battle", response_model=PositionBattleResponse)
def get_position_battle(
    race_id: int = Path(..., ge=1),
    db: Session = Depends(get_db)
):
    race = db.execute(text("""
        SELECT
            r.id,
            r.season_year,
            r.round_number,
            r.race_date,
            t.id AS track_id,
            t.name AS track_name
        FROM races r
        JOIN tracks t ON t.id = r.track_id
        WHERE r.id = :id
    """), {"id": race_id}).mappings().first()

    if race is None:
        raise HTTPException(status_code=404, detail="Race not found")

    rows = db.execute(text("""
        SELECT
            d.id AS driver_id,
            d.name AS driver_name,
            re.car_number,
            tm.id AS team_id,
            tm.name AS team_name,
            tm.color_hex,
            rr.starting_grid_position,
            rr.finishing_position,
            rr.status,
            l.lap_number,
            l.position
        FROM laps l
        JOIN sessions s
          ON s.id = l.session_id
         AND s.session_type = 'R'
        JOIN race_entries re
          ON re.id = l.race_entry_id
        JOIN drivers d
          ON d.id = re.driver_id
        JOIN teams tm
          ON tm.id = re.team_id
        LEFT JOIN race_results rr
          ON rr.race_entry_id = re.id
         AND rr.session_id = s.id
        WHERE s.race_id = :race_id
        ORDER BY
            rr.finishing_position NULLS LAST,
            d.id,
            l.lap_number
    """), {"race_id": race_id}).mappings().all()

    drivers = {}

    for row in rows:
        driver_id = row["driver_id"]

        if driver_id not in drivers:
            drivers[driver_id] = {
                "driver_id": driver_id,
                "driver_name": row["driver_name"],
                "car_number": row["car_number"],
                "team_id": row["team_id"],
                "team_name": row["team_name"],
                "color_hex": row["color_hex"],
                "starting_grid_position": row["starting_grid_position"],
                "finishing_position": row["finishing_position"],
                "status": row["status"],
                "position_data_available": False,
                "positions": [],
            }

        if row["position"] is not None:
            drivers[driver_id]["position_data_available"] = True

        drivers[driver_id]["positions"].append({
            "lap": row["lap_number"],
            "position": row["position"],
        })

    results = []

    for driver in drivers.values():
        grid = driver["starting_grid_position"]
        finish = driver["finishing_position"]

        driver["positions_gained"] = (
            grid - finish
            if (
                is_classified(driver["status"])
                and grid is not None
                and finish is not None
            )
            else None
        )

        driver["missing_position_laps"] = sum(
            1 for x in driver["positions"]
            if x["position"] is None
        )

        valid_positions = [
            x["position"]
            for x in driver["positions"]
            if x["position"] is not None
        ]

        driver["best_position"] = (
            min(valid_positions)
            if valid_positions else None
        )

        driver["worst_position"] = (
            max(valid_positions)
            if valid_positions else None
        )

        results.append(driver)

    return {
        "race_id": race_id,
        "season_year": race["season_year"],
        "round_number": race["round_number"],
        "race_date": race["race_date"],
        "track_id": race["track_id"],
        "track_name": race["track_name"],
        "results": results,
    }


def format_quali_time(seconds):
    if seconds is None:
        return None
    return format_absolute_time(float(seconds))


@router.get("/{race_id}/qualifying")
def get_qualifying(race_id: int, db: Session = Depends(get_db)):
    race = db.execute(text("SELECT id FROM races WHERE id = :id"), {"id": race_id}).mappings().first()
    if race is None:
        raise HTTPException(status_code=404, detail="Race not found")

    quali_session = db.execute(text("""
        SELECT s.id, sw.rainfall
        FROM sessions s
        LEFT JOIN session_weather sw ON sw.session_id = s.id
        WHERE s.race_id = :id AND s.session_type = 'Q'
    """), {"id": race_id}).mappings().first()

    if quali_session is None:
        return {"race_id": race_id, "wet_qualifying": None, "results": []}

    rows = db.execute(text("""
        SELECT d.id AS driver_id, d.name AS driver_name, re.car_number,
               tm.name AS team_name, tm.color_hex,
               qr.q1_time, qr.q2_time, qr.q3_time, qr.final_position,
               rr.starting_grid_position
        FROM qualifying_results qr
        JOIN race_entries re ON re.id = qr.race_entry_id
        JOIN drivers d ON d.id = re.driver_id
        JOIN teams tm ON tm.id = re.team_id
        LEFT JOIN race_results rr ON rr.race_entry_id = re.id
            AND rr.session_id = (SELECT id FROM sessions WHERE race_id = :id AND session_type = 'R')
        WHERE qr.session_id = :sid
        ORDER BY qr.final_position NULLS LAST
    """), {"id": race_id, "sid": quali_session["id"]}).mappings().all()

    if not rows:
        return {"race_id": race_id, "wet_qualifying": quali_session["rainfall"], "results": []}

    def final_time(row):
        return row["q3_time"] or row["q2_time"] or row["q1_time"]

    pole_row = next((r for r in rows if r["final_position"] == 1), None)
    pole_time = final_time(pole_row) if pole_row else None

    results = []
    for row in rows:
        ft = final_time(row)
        gap_to_pole = round(float(ft) - float(pole_time), 3) if (ft is not None and pole_time is not None and ft != pole_time) else (0.0 if ft == pole_time and ft is not None else None)

        if row["q3_time"] is not None:
            eliminated_in = None
        elif row["q2_time"] is not None and row["final_position"] is not None and row["final_position"] <= 10:
            eliminated_in = None  # reached Q3 (always top 10) but set no Q3 time
        elif row["q2_time"] is not None:
            eliminated_in = "Q2"
        elif row["q1_time"] is not None:
            eliminated_in = "Q1"
        else:
            eliminated_in = None  # no time set at all -- likely DNS

        improvement_q1_to_q3 = (
            round(float(row["q1_time"]) - float(row["q3_time"]), 3)
            if row["q1_time"] is not None and row["q3_time"] is not None else None
        )

        grid_penalty = None
        if row["starting_grid_position"] is not None and row["final_position"] is not None:
            diff = row["starting_grid_position"] - row["final_position"]
            if diff != 0:
                grid_penalty = diff  # positive = dropped grid spots (penalty), negative = promoted (someone else's penalty)

        results.append({
            "driver_id": row["driver_id"],
            "driver_name": row["driver_name"],
            "car_number": row["car_number"],
            "team_name": row["team_name"],
            "color_hex": row["color_hex"],
            "final_position": row["final_position"],
            "starting_grid_position": row["starting_grid_position"],
            "grid_penalty": grid_penalty,
            "q1_time": format_quali_time(row["q1_time"]),
            "q2_time": format_quali_time(row["q2_time"]),
            "q3_time": format_quali_time(row["q3_time"]),
            "eliminated_in": eliminated_in,
            "gap_to_pole": gap_to_pole,
            "improvement_q1_to_q3": improvement_q1_to_q3,
        })

    return {
        "race_id": race_id,
        "wet_qualifying": quali_session["rainfall"],
        "results": results,
    }
