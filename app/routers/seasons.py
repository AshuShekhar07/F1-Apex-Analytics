"""Season-wide views for the frontend: every driver's result in every round."""

from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from race_status import is_classified, is_dnf

router = APIRouter(prefix="/seasons", tags=["seasons"])


def _countback(positions: list[int]) -> tuple[int, ...]:
    """F1 tie-break: most wins, then most second places, and so on (as a sort key)."""
    return tuple(-positions.count(p) for p in range(1, 21))


@router.get("/{season}/results")
def season_results(season: int = Path(..., ge=2018, le=2100), db: Session = Depends(get_db)):
    """The season's results matrix: each round, and per race driver their race and sprint
    result (position, grid, points, status) with the team they drove for that weekend.
    Drivers and teams are ordered as the championship table: points (sprints included), then
    countback on race finishes."""
    rounds = db.execute(text("""
        SELECT r.id AS race_id, r.round_number, r.race_date, r.weekend_format, t.name AS track_name, t.country,
               EXISTS (SELECT 1 FROM sessions s JOIN race_results rr ON rr.session_id = s.id
                       WHERE s.race_id = r.id AND s.session_type = 'R') AS raced
        FROM races r JOIN tracks t ON t.id = r.track_id
        WHERE r.season_year = :s
        ORDER BY r.round_number
    """), {"s": season}).mappings().all()
    if not rounds:
        raise HTTPException(status_code=404, detail=f"No races found for {season}")
    rows = db.execute(text("""
        SELECT r.round_number, s.session_type, re.driver_id, d.name AS driver_name, re.car_number, re.team_id,
               rr.finishing_position, rr.starting_grid_position, rr.points, rr.status
        FROM race_results rr
        JOIN sessions s ON s.id = rr.session_id AND s.session_type IN ('R', 'S')
        JOIN race_entries re ON re.id = rr.race_entry_id
        JOIN races r ON r.id = s.race_id
        JOIN drivers d ON d.id = re.driver_id
        WHERE r.season_year = :s AND re.role = 'race_driver'
        ORDER BY r.round_number
    """), {"s": season}).mappings().all()
    team_ids = sorted({int(r["team_id"]) for r in rows if r["team_id"] is not None})
    teams = {int(t["id"]): dict(t) for t in db.execute(text("""
        SELECT id, name, color_hex, logo_url FROM teams WHERE id = ANY(:ids)
    """), {"ids": team_ids}).mappings().all()} if team_ids else {}

    def result(row) -> dict:
        status = row["status"]
        return {"position": row["finishing_position"], "grid": row["starting_grid_position"],
                "points": float(row["points"] or 0), "status": status,
                "classified": is_classified(status), "dnf": is_dnf(status)}

    drivers: dict[int, dict] = {}
    by_round: dict[int, dict[int, dict]] = defaultdict(dict)
    for row in rows:
        did = int(row["driver_id"])
        d = drivers.setdefault(did, {"id": did, "name": row["driver_name"], "number": None, "team_id": None})
        d["number"] = row["car_number"] or d["number"]
        d["team_id"] = row["team_id"]          # rows come in round order: the last one is the latest team
        cell = by_round[did].setdefault(int(row["round_number"]), {"round": int(row["round_number"]),
                                                                     "team_id": row["team_id"], "race": None, "sprint": None})
        cell["race" if row["session_type"] == "R" else "sprint"] = result(row)

    team_points: dict[int, dict[int, float]] = defaultdict(lambda: defaultdict(float))
    team_finishes: dict[int, list[int]] = defaultdict(list)
    out = []
    for did, d in drivers.items():
        cells = [by_round[did][n] for n in sorted(by_round[did])]
        finishes = [c["race"]["position"] for c in cells if c["race"] and c["race"]["position"] is not None]
        points = 0.0
        for c in cells:
            got = sum(x["points"] for x in (c["race"], c["sprint"]) if x)
            points += got
            if c["team_id"] is not None:
                team_points[int(c["team_id"])][c["round"]] += got
                if c["race"] and c["race"]["position"] is not None:
                    team_finishes[int(c["team_id"])].append(c["race"]["position"])
        team = teams.get(int(d["team_id"])) if d["team_id"] is not None else None
        out.append({"id": did, "name": d["name"], "number": d["number"], "team": team,
                    "points": round(points, 1), "wins": finishes.count(1),
                    "podiums": sum(p <= 3 for p in finishes), "results": cells, "_cb": _countback(finishes)})
    out.sort(key=lambda d: (-d["points"], d["_cb"], d["name"]))
    for d in out:
        d.pop("_cb")

    team_rows = []
    for tid, per_round in team_points.items():
        t = teams.get(tid, {"id": tid, "name": None, "color_hex": None, "logo_url": None})
        total = round(sum(per_round.values()), 1)
        team_rows.append({**t, "points": total, "wins": team_finishes[tid].count(1),
                          "by_round": [{"round": n, "points": round(v, 1)} for n, v in sorted(per_round.items())],
                          "_cb": _countback(team_finishes[tid])})
    team_rows.sort(key=lambda t: (-t["points"], t["_cb"], t["name"] or ""))
    for t in team_rows:
        t.pop("_cb")

    raced = [r["round_number"] for r in rounds if r["raced"]]
    return {
        "season": season,
        "latest_round": max(raced) if raced else None,
        "rounds": [{"race_id": r["race_id"], "round": r["round_number"], "race_date": str(r["race_date"]),
                    "track_name": r["track_name"], "country": r["country"], "weekend_format": r["weekend_format"],
                    "sprint": (r["weekend_format"] or "conventional") != "conventional", "raced": r["raced"]}
                   for r in rounds],
        "drivers": out,
        "teams": team_rows,
        "note": "points include sprints; ties broken by countback on race finishes",
    }
