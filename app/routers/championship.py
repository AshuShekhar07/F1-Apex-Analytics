"""Drivers' and constructors' title chances (championship_model_v1), stored after every round
by championship_projection_v1.py."""

from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from championship_model_v1 import MODEL_VERSION, VALIDATION

router = APIRouter(prefix="/championship", tags=["championship"])

_ENTITY_SQL = {
    "drivers": """
        SELECT cp.entity_id AS driver_id, d.name AS driver_name, tm.name AS team_name, tm.color_hex,
               cp.p_title, cp.points_now, cp.expected_points
        FROM championship_projections cp
        JOIN drivers d ON d.id = cp.entity_id
        LEFT JOIN LATERAL (
            SELECT re.team_id FROM race_entries re JOIN races r ON r.id = re.race_id
            WHERE re.driver_id = cp.entity_id AND r.season_year = cp.season_year
              AND r.round_number <= cp.after_round AND re.role = 'race_driver'
            ORDER BY r.round_number DESC LIMIT 1) last_team ON TRUE
        LEFT JOIN teams tm ON tm.id = last_team.team_id
        WHERE cp.season_year = :s AND cp.after_round = :r AND cp.model_version = :v AND cp.kind = 'drivers'
        ORDER BY cp.p_title DESC, cp.points_now DESC""",
    "constructors": """
        SELECT cp.entity_id AS team_id, tm.name AS team_name, tm.color_hex,
               cp.p_title, cp.points_now, cp.expected_points
        FROM championship_projections cp
        JOIN teams tm ON tm.id = cp.entity_id
        WHERE cp.season_year = :s AND cp.after_round = :r AND cp.model_version = :v AND cp.kind = 'constructors'
        ORDER BY cp.p_title DESC, cp.points_now DESC""",
}


def _table_exists(db: Session) -> bool:
    return bool(db.execute(text("SELECT to_regclass('championship_projections') IS NOT NULL")).scalar())


def _numbers(row: dict) -> dict:
    return {**row, "p_title": float(row["p_title"]), "points_now": float(row["points_now"]),
            "expected_points": float(row["expected_points"])}


@router.get("/{season}")
def get_championship(season: int = Path(..., ge=2018, le=2100), db: Session = Depends(get_db)):
    """Latest title chances: per driver / team the chance of the title, points now and the
    expected final points."""
    meta = db.execute(text("""
        SELECT after_round, MAX(weekends_left) AS weekends_left, MAX(computed_at) AS computed_at
        FROM championship_projections WHERE season_year = :s AND model_version = :v
        GROUP BY after_round ORDER BY after_round DESC LIMIT 1"""),
        {"s": season, "v": MODEL_VERSION}).mappings().first() if _table_exists(db) else None
    if meta is None:
        raise HTTPException(status_code=404, detail="No championship projection for this season yet "
                                                    "(run championship_projection_v1.py after a race)")
    params = {"s": season, "r": meta["after_round"], "v": MODEL_VERSION}
    out = {"season": season, "model_version": MODEL_VERSION, "after_round": meta["after_round"],
           "weekends_left": meta["weekends_left"], "computed_at": meta["computed_at"]}
    for kind, sql in _ENTITY_SQL.items():
        out[kind] = [_numbers(dict(r)) for r in db.execute(text(sql), params).mappings()]
    out["validation"] = VALIDATION
    return out


@router.get("/{season}/history")
def get_championship_history(season: int = Path(..., ge=2018, le=2100), db: Session = Depends(get_db)):
    """Title chance of every driver and team after each round (for a trend chart)."""
    if not _table_exists(db):
        raise HTTPException(status_code=404, detail="No championship projection for this season yet")
    rows = db.execute(text("""
        SELECT cp.kind, cp.entity_id, cp.after_round, cp.p_title,
               COALESCE(d.name, tm.name) AS name
        FROM championship_projections cp
        LEFT JOIN drivers d ON cp.kind = 'drivers' AND d.id = cp.entity_id
        LEFT JOIN teams tm ON cp.kind = 'constructors' AND tm.id = cp.entity_id
        WHERE cp.season_year = :s AND cp.model_version = :v
        ORDER BY cp.after_round"""), {"s": season, "v": MODEL_VERSION}).mappings().all()
    if not rows:
        raise HTTPException(status_code=404, detail="No championship projection for this season yet")
    series: dict = {"drivers": defaultdict(lambda: {"rounds": []}), "constructors": defaultdict(lambda: {"rounds": []})}
    for r in rows:
        entry = series[r["kind"]][r["entity_id"]]
        entry["name"] = r["name"]
        entry["rounds"].append({"after_round": r["after_round"], "p_title": float(r["p_title"])})
    return {"season": season, "model_version": MODEL_VERSION,
            **{kind: [{"id": k, **v} for k, v in by.items()] for kind, by in series.items()}}
