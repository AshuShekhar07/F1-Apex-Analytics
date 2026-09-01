from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.database import get_db

router = APIRouter(prefix="/races", tags=["races"])

FINISHED_STATUSES = ('Finished', 'Lapped', '+1 Lap', '+2 Laps', '+3 Laps', '+5 Laps', '+6 Laps')


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
    if status in FINISHED_STATUSES:
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


@router.get("")
def list_races(season: int | None = None, db: Session = Depends(get_db)):
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


@router.get("/{race_id}")
def get_race(race_id: int, db: Session = Depends(get_db)):
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
