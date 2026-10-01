from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.database import get_db
from race_status import DNF_STATUSES

router = APIRouter(prefix="/records", tags=["records"])


@router.get("/all-time")
def get_all_time_records(db: Session = Depends(get_db)):
    records = []

    def top_driver(sql, label, unit="", params=None):
        row = db.execute(
            text(sql),
            params or {},
        ).mappings().first()
        if row:
            records.append({
                "category": label,
                "driver_name": row.get("driver_name"),
                "value": row.get("value"),
                "unit": unit,
                "context": {k: v for k, v in row.items() if k not in ("driver_name", "value")},
            })

    top_driver("""
        SELECT d.name AS driver_name, COUNT(*) AS value
        FROM race_results rr
        JOIN sessions s ON s.id = rr.session_id AND s.session_type = 'R'
        JOIN race_entries re ON re.id = rr.race_entry_id
        JOIN drivers d ON d.id = re.driver_id
        WHERE rr.finishing_position = 1
        GROUP BY d.id, d.name ORDER BY value DESC LIMIT 1
    """, "Most race wins", "wins")

    top_driver("""
        SELECT d.name AS driver_name, COUNT(*) AS value
        FROM qualifying_results qr
        JOIN sessions s ON s.id = qr.session_id AND s.session_type = 'Q'
        JOIN race_entries re ON re.id = qr.race_entry_id
        JOIN drivers d ON d.id = re.driver_id
        WHERE qr.final_position = 1
        GROUP BY d.id, d.name ORDER BY value DESC LIMIT 1
    """, "Most pole positions", "poles")

    top_driver("""
        SELECT d.name AS driver_name, COUNT(*) AS value
        FROM race_results rr
        JOIN sessions s ON s.id = rr.session_id AND s.session_type = 'R'
        JOIN race_entries re ON re.id = rr.race_entry_id
        JOIN drivers d ON d.id = re.driver_id
        WHERE rr.finishing_position <= 3
        GROUP BY d.id, d.name ORDER BY value DESC LIMIT 1
    """, "Most podiums", "podiums")

    top_driver("""
        SELECT d.name AS driver_name, SUM(rr.points) AS value
        FROM race_results rr
        JOIN sessions s ON s.id = rr.session_id AND s.session_type = 'R'
        JOIN race_entries re ON re.id = rr.race_entry_id
        JOIN drivers d ON d.id = re.driver_id
        GROUP BY d.id, d.name ORDER BY value DESC LIMIT 1
    """, "Most career points (Race sessions only)", "points")

    top_driver("""
        SELECT d.name AS driver_name, SUM(rr.points) AS value
        FROM race_results rr
        JOIN sessions s ON s.id = rr.session_id AND s.session_type IN ('R', 'S')
        JOIN race_entries re ON re.id = rr.race_entry_id
        JOIN drivers d ON d.id = re.driver_id
        GROUP BY d.id, d.name ORDER BY value DESC LIMIT 1
    """, "Most career points (Race + Sprint combined)", "points")

    top_driver(f"""
        SELECT d.name AS driver_name, COUNT(*) AS value
        FROM race_results rr
        JOIN sessions s ON s.id = rr.session_id AND s.session_type = 'R'
        JOIN race_entries re ON re.id = rr.race_entry_id
        JOIN drivers d ON d.id = re.driver_id
        WHERE rr.status = ANY(:dnf_statuses)
        GROUP BY d.id, d.name ORDER BY value DESC LIMIT 1
    """, "Most DNFs", "DNFs", {
        "dnf_statuses": list(DNF_STATUSES),
    })

    # youngest/oldest winner -- age in years at time of race, using real DOB + race_date
    top_driver("""
        SELECT d.name AS driver_name,
               ROUND((r.race_date - d.date_of_birth)::numeric / 365.25, 1) AS value,
               t.name AS track_name, r.season_year
        FROM race_results rr
        JOIN sessions s ON s.id = rr.session_id AND s.session_type = 'R'
        JOIN race_entries re ON re.id = rr.race_entry_id
        JOIN drivers d ON d.id = re.driver_id
        JOIN races r ON r.id = re.race_id
        JOIN tracks t ON t.id = r.track_id
        WHERE rr.finishing_position = 1 AND d.date_of_birth IS NOT NULL
        ORDER BY value ASC LIMIT 1
    """, "Youngest race winner", "years old")

    top_driver("""
        SELECT d.name AS driver_name,
               ROUND((r.race_date - d.date_of_birth)::numeric / 365.25, 1) AS value,
               t.name AS track_name, r.season_year
        FROM race_results rr
        JOIN sessions s ON s.id = rr.session_id AND s.session_type = 'R'
        JOIN race_entries re ON re.id = rr.race_entry_id
        JOIN drivers d ON d.id = re.driver_id
        JOIN races r ON r.id = re.race_id
        JOIN tracks t ON t.id = r.track_id
        WHERE rr.finishing_position = 1 AND d.date_of_birth IS NOT NULL
        ORDER BY value DESC LIMIT 1
    """, "Oldest race winner", "years old")

    # biggest winning margin = P2's gap_to_winner_seconds in that race
    top_driver("""
        SELECT winner.name AS driver_name, rr2.gap_to_winner_seconds AS value,
               t.name AS track_name, r.season_year
        FROM race_results rr2
        JOIN sessions s ON s.id = rr2.session_id AND s.session_type = 'R'
        JOIN races r ON r.id = s.race_id
        JOIN tracks t ON t.id = r.track_id
        JOIN race_results rr1 ON rr1.session_id = rr2.session_id AND rr1.finishing_position = 1
        JOIN race_entries re1 ON re1.id = rr1.race_entry_id
        JOIN drivers winner ON winner.id = re1.driver_id
        WHERE rr2.finishing_position = 2 AND rr2.gap_to_winner_seconds IS NOT NULL
        ORDER BY value DESC LIMIT 1
    """, "Biggest winning margin", "seconds")

    top_driver("""
        SELECT winner.name AS driver_name, rr2.gap_to_winner_seconds AS value,
               t.name AS track_name, r.season_year
        FROM race_results rr2
        JOIN sessions s ON s.id = rr2.session_id AND s.session_type = 'R'
        JOIN races r ON r.id = s.race_id
        JOIN tracks t ON t.id = r.track_id
        JOIN race_results rr1 ON rr1.session_id = rr2.session_id AND rr1.finishing_position = 1
        JOIN race_entries re1 ON re1.id = rr1.race_entry_id
        JOIN drivers winner ON winner.id = re1.driver_id
        WHERE rr2.finishing_position = 2 AND rr2.gap_to_winner_seconds > 0
        ORDER BY value ASC LIMIT 1
    """, "Closest finish (winning margin)", "seconds")

    top_driver("""
        SELECT d.name AS driver_name, t.name AS track_name, COUNT(*) AS value
        FROM race_results rr
        JOIN sessions s ON s.id = rr.session_id AND s.session_type = 'R'
        JOIN race_entries re ON re.id = rr.race_entry_id
        JOIN drivers d ON d.id = re.driver_id
        JOIN races r ON r.id = re.race_id
        JOIN tracks t ON t.id = r.track_id
        WHERE rr.finishing_position = 1
        GROUP BY d.id, d.name, t.id, t.name
        ORDER BY value DESC LIMIT 1
    """, "Most wins at a single circuit", "wins")

    top_driver("""
        SELECT
            d.name AS driver_name,
            COUNT(*) AS value
        FROM race_results rr
        JOIN sessions s
          ON s.id = rr.session_id
         AND s.session_type = 'R'
        JOIN race_entries re
          ON re.id = rr.race_entry_id
        JOIN drivers d
          ON d.id = re.driver_id
        WHERE rr.finishing_position = 1
          AND rr.starting_grid_position = 1
        GROUP BY d.id, d.name
        ORDER BY value DESC, d.name
        LIMIT 1
    """, "Most wins from pole", "wins")

    top_driver("""
        SELECT
            d.name AS driver_name,
            COUNT(*) AS value
        FROM race_results rr
        JOIN sessions s
          ON s.id = rr.session_id
         AND s.session_type = 'R'
        JOIN race_entries re
          ON re.id = rr.race_entry_id
        JOIN drivers d
          ON d.id = re.driver_id
        WHERE rr.finishing_position = 1
          AND rr.starting_grid_position > 3
        GROUP BY d.id, d.name
        ORDER BY value DESC, d.name
        LIMIT 1
    """, "Most wins starting outside the top 3", "wins")

    top_driver("""
        SELECT
            d.name AS driver_name,
            COUNT(*) AS value,
            r.season_year
        FROM race_results rr
        JOIN sessions s
          ON s.id = rr.session_id
         AND s.session_type = 'R'
        JOIN race_entries re
          ON re.id = rr.race_entry_id
        JOIN drivers d
          ON d.id = re.driver_id
        JOIN races r
          ON r.id = s.race_id
        WHERE rr.finishing_position <= 3
        GROUP BY d.id, d.name, r.season_year
        ORDER BY value DESC, r.season_year ASC, d.name
        LIMIT 1
    """, "Most podiums in a single season", "podiums")

    top_driver("""
        SELECT
            tm.name AS driver_name,
            COUNT(*) AS value,
            r.season_year
        FROM race_results rr
        JOIN sessions s
          ON s.id = rr.session_id
         AND s.session_type = 'R'
        JOIN race_entries re
          ON re.id = rr.race_entry_id
        JOIN teams tm
          ON tm.id = re.team_id
        JOIN races r
          ON r.id = s.race_id
        WHERE rr.finishing_position = 1
        GROUP BY tm.id, tm.name, r.season_year
        ORDER BY value DESC, r.season_year ASC, tm.name
        LIMIT 1
    """, "Most race wins by a team in a single season", "wins")

    top_driver("""
        SELECT
            tm.name AS driver_name,
            COUNT(*) AS value,
            r.season_year
        FROM race_results rr
        JOIN sessions s
          ON s.id = rr.session_id
         AND s.session_type = 'R'
        JOIN race_entries re
          ON re.id = rr.race_entry_id
        JOIN teams tm
          ON tm.id = re.team_id
        JOIN races r
          ON r.id = s.race_id
        WHERE rr.finishing_position <= 3
        GROUP BY tm.id, tm.name, r.season_year
        ORDER BY value DESC, r.season_year ASC, tm.name
        LIMIT 1
    """, "Most podiums by a team in a single season", "podiums")

    top_driver("""
        SELECT
            tm.name AS driver_name,
            SUM(rr.points) AS value,
            r.season_year
        FROM race_results rr
        JOIN sessions s
          ON s.id = rr.session_id
         AND s.session_type = 'R'
        JOIN race_entries re
          ON re.id = rr.race_entry_id
        JOIN teams tm
          ON tm.id = re.team_id
        JOIN races r
          ON r.id = s.race_id
        GROUP BY tm.id, tm.name, r.season_year
        ORDER BY value DESC, r.season_year ASC, tm.name
        LIMIT 1
    """, "Most race points by a team in a single season", "points")

    most_winners_row = db.execute(text("""
        SELECT season_year, COUNT(DISTINCT driver_id) AS distinct_winners
        FROM (
            SELECT r.season_year, re.driver_id
            FROM race_results rr
            JOIN sessions s ON s.id = rr.session_id AND s.session_type = 'R'
            JOIN race_entries re ON re.id = rr.race_entry_id
            JOIN races r ON r.id = re.race_id
            WHERE rr.finishing_position = 1
        ) w
        GROUP BY season_year ORDER BY distinct_winners DESC, season_year ASC LIMIT 1
    """)).mappings().first()
    if most_winners_row:
        records.append({
            "category": "Season with most different race winners",
            "driver_name": None,
            "value": most_winners_row["distinct_winners"],
            "unit": "different winners",
            "context": {"season_year": most_winners_row["season_year"]},
        })

    # longest win streak -- needs sequential logic, computed in Python
    win_rows = db.execute(text("""
        SELECT re.driver_id, d.name AS driver_name, r.season_year, r.round_number
        FROM race_results rr
        JOIN sessions s ON s.id = rr.session_id AND s.session_type = 'R'
        JOIN race_entries re ON re.id = rr.race_entry_id
        JOIN drivers d ON d.id = re.driver_id
        JOIN races r ON r.id = re.race_id
        WHERE rr.finishing_position = 1
        ORDER BY re.driver_id, r.season_year, r.round_number
    """)).mappings().all()

    all_races = db.execute(text("""
        SELECT id, season_year, round_number FROM races ORDER BY season_year, round_number
    """)).mappings().all()
    race_order = {(r["season_year"], r["round_number"]): i for i, r in enumerate(all_races)}

    best_streak = 0
    best_streak_driver = None
    current_driver = None
    current_streak = 0
    prev_idx = None
    for row in win_rows:
        idx = race_order[(row["season_year"], row["round_number"])]
        if row["driver_id"] == current_driver and prev_idx is not None and idx == prev_idx + 1:
            current_streak += 1
        else:
            current_driver = row["driver_id"]
            current_streak = 1
        if current_streak > best_streak:
            best_streak = current_streak
            best_streak_driver = row["driver_name"]
        prev_idx = idx

    records.append({
        "category": "Longest consecutive race-win streak",
        "driver_name": best_streak_driver,
        "value": best_streak,
        "unit": "consecutive wins",
        "context": {},
    })

    return {"records": records}
