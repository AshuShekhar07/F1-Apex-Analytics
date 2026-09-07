from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.database import get_db

router = APIRouter(prefix="/compare", tags=["compare"])


def get_driver_basic(db, driver_id):
    row = db.execute(text("""
        SELECT id, name, nationality, photo_url FROM drivers WHERE id = :id
    """), {"id": driver_id}).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Driver {driver_id} not found")
    return dict(row)


@router.get("/{driver1_id}/{driver2_id}")
def compare_drivers(
    driver1_id: int,
    driver2_id: int,
    season: int | None = Query(None, description="Restrict comparison to a single season, e.g. 2023"),
    teammates_only: bool = Query(False, description="If true, only count races where both drivers were on the same team"),
    db: Session = Depends(get_db),
):
    if driver1_id == driver2_id:
        raise HTTPException(status_code=422, detail="Cannot compare a driver with themselves")

    driver1 = get_driver_basic(db, driver1_id)
    driver2 = get_driver_basic(db, driver2_id)

    # find every season/team pair where these two were genuine teammates (same team, same race),
    # regardless of the teammates_only flag -- useful context either way, and drives the
    # race-set restriction when teammates_only=true
    teammate_race_rows = db.execute(text("""
        SELECT re1.race_id, r.season_year, re1.team_id, tm.name AS team_name
        FROM race_entries re1
        JOIN race_entries re2 ON re2.race_id = re1.race_id AND re2.team_id = re1.team_id
        JOIN races r ON r.id = re1.race_id
        JOIN teams tm ON tm.id = re1.team_id
        WHERE re1.driver_id = :d1 AND re2.driver_id = :d2
          AND re1.role = 'race_driver' AND re2.role = 'race_driver'
        ORDER BY r.season_year, r.round_number
    """), {"d1": driver1_id, "d2": driver2_id}).mappings().all()

    teammate_race_ids = {row["race_id"] for row in teammate_race_rows}

    # collapse into contiguous season ranges per team, same gaps-and-islands technique
    # used in the driver profile's team_history query
    teammate_periods = []
    if teammate_race_rows:
        seasons_by_team = {}
        for row in teammate_race_rows:
            seasons_by_team.setdefault(row["team_id"], {"team_name": row["team_name"], "seasons": set()})
            seasons_by_team[row["team_id"]]["seasons"].add(row["season_year"])
        for team_id, info in seasons_by_team.items():
            years = sorted(info["seasons"])
            start = years[0]
            prev = years[0]
            for y in years[1:] + [None]:
                if y is None or y != prev + 1:
                    teammate_periods.append({"team_name": info["team_name"], "start_year": start, "end_year": prev})
                    if y is not None:
                        start = y
                prev = y if y is not None else prev

    # determine the race set for the actual comparison stats
    if teammates_only:
        race_filter_ids = teammate_race_ids
        if not race_filter_ids:
            return {
                "driver1": driver1,
                "driver2": driver2,
                "were_teammates": False,
                "teammate_periods": [],
                "message": "These two drivers were never teammates -- no races to compare in teammates_only mode.",
            }
    else:
        any_races = db.execute(text("""
            SELECT r.id AS race_id
            FROM races r
            WHERE EXISTS (SELECT 1 FROM race_entries re WHERE re.race_id = r.id AND re.driver_id = :d1 AND re.role = 'race_driver')
              AND EXISTS (SELECT 1 FROM race_entries re WHERE re.race_id = r.id AND re.driver_id = :d2 AND re.role = 'race_driver')
        """), {"d1": driver1_id, "d2": driver2_id}).mappings().all()
        race_filter_ids = {row["race_id"] for row in any_races}

    if season is not None:
        season_race_ids = db.execute(text("""
            SELECT id FROM races WHERE season_year = :season
        """), {"season": season}).scalars().all()
        race_filter_ids = race_filter_ids & set(season_race_ids)

    if not race_filter_ids:
        return {
            "driver1": driver1,
            "driver2": driver2,
            "were_teammates": len(teammate_race_ids) > 0,
            "teammate_periods": teammate_periods,
            "message": "No overlapping races found for the given filters.",
        }

    # pull per-race data for both drivers across the filtered race set
    rows = db.execute(text("""
        SELECT r.id AS race_id, r.season_year, r.round_number, t.name AS track_name,
               d.id AS driver_id,
               qr.final_position AS quali_position,
               rr.finishing_position, rr.points, rr.status
        FROM races r
        JOIN tracks t ON t.id = r.track_id
        JOIN race_entries re ON re.race_id = r.id AND re.role = 'race_driver'
        JOIN drivers d ON d.id = re.driver_id
        LEFT JOIN qualifying_results qr ON qr.race_entry_id = re.id
            AND qr.session_id = (SELECT id FROM sessions WHERE race_id = r.id AND session_type = 'Q' LIMIT 1)
        LEFT JOIN race_results rr ON rr.race_entry_id = re.id
            AND rr.session_id = (SELECT id FROM sessions WHERE race_id = r.id AND session_type = 'R' LIMIT 1)
        WHERE r.id = ANY(:race_ids) AND re.driver_id = ANY(:driver_ids)
        ORDER BY r.season_year, r.round_number
    """), {"race_ids": list(race_filter_ids), "driver_ids": [driver1_id, driver2_id]}).mappings().all()

    by_race = {}
    for row in rows:
        by_race.setdefault(row["race_id"], {"season_year": row["season_year"], "round_number": row["round_number"],
                                             "track_name": row["track_name"]})
        by_race[row["race_id"]][row["driver_id"]] = {
            "quali_position": row["quali_position"],
            "finishing_position": row["finishing_position"],
            "points": float(row["points"]) if row["points"] is not None else 0.0,
            "status": row["status"],
        }

    quali_h2h = {driver1_id: 0, driver2_id: 0}
    race_h2h = {driver1_id: 0, driver2_id: 0}
    points_total = {driver1_id: 0.0, driver2_id: 0.0}
    race_by_race = []

    for race_id, data in sorted(by_race.items(), key=lambda x: (x[1]["season_year"], x[1]["round_number"])):
        d1 = data.get(driver1_id, {})
        d2 = data.get(driver2_id, {})

        if d1.get("quali_position") is not None and d2.get("quali_position") is not None:
            if d1["quali_position"] < d2["quali_position"]:
                quali_h2h[driver1_id] += 1
            elif d2["quali_position"] < d1["quali_position"]:
                quali_h2h[driver2_id] += 1

        if d1.get("finishing_position") is not None and d2.get("finishing_position") is not None:
            if d1["finishing_position"] < d2["finishing_position"]:
                race_h2h[driver1_id] += 1
            elif d2["finishing_position"] < d1["finishing_position"]:
                race_h2h[driver2_id] += 1

        points_total[driver1_id] += d1.get("points", 0.0)
        points_total[driver2_id] += d2.get("points", 0.0)

        race_by_race.append({
            "season_year": data["season_year"],
            "round_number": data["round_number"],
            "track_name": data["track_name"],
            "driver1": {"quali_position": d1.get("quali_position"), "finishing_position": d1.get("finishing_position"),
                        "points": d1.get("points"), "status": d1.get("status")},
            "driver2": {"quali_position": d2.get("quali_position"), "finishing_position": d2.get("finishing_position"),
                        "points": d2.get("points"), "status": d2.get("status")},
        })

    sprint_rows = db.execute(text("""
        SELECT re.driver_id, SUM(rr.points) AS sprint_points
        FROM race_entries re
        JOIN race_results rr ON rr.race_entry_id = re.id
        JOIN sessions s ON s.id = rr.session_id AND s.session_type = 'S'
        WHERE re.race_id = ANY(:race_ids) AND re.driver_id = ANY(:driver_ids)
        GROUP BY re.driver_id
    """), {"race_ids": list(race_filter_ids), "driver_ids": [driver1_id, driver2_id]}).mappings().all()
    sprint_points = {driver1_id: 0.0, driver2_id: 0.0}
    for row in sprint_rows:
        sprint_points[row["driver_id"]] = float(row["sprint_points"] or 0.0)

    return {
        "driver1": driver1,
        "driver2": driver2,
        "season_filter": season,
        "teammates_only": teammates_only,
        "were_teammates": len(teammate_race_ids) > 0,
        "teammate_periods": teammate_periods,
        "races_compared": len(by_race),
        "qualifying_h2h": {str(driver1_id): quali_h2h[driver1_id], str(driver2_id): quali_h2h[driver2_id]},
        "race_h2h": {str(driver1_id): race_h2h[driver1_id], str(driver2_id): race_h2h[driver2_id]},
        "race_points_total": {str(driver1_id): round(points_total[driver1_id], 1), str(driver2_id): round(points_total[driver2_id], 1)},
        "sprint_points_total": {str(driver1_id): round(sprint_points[driver1_id], 1), str(driver2_id): round(sprint_points[driver2_id], 1)},
        "combined_points_total": {str(driver1_id): round(points_total[driver1_id] + sprint_points[driver1_id], 1),
                                   str(driver2_id): round(points_total[driver2_id] + sprint_points[driver2_id], 1)},
        "race_by_race": race_by_race,
    }
