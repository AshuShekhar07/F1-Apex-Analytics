from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.database import get_db

router = APIRouter(prefix="/drivers", tags=["wet-weather"])


def compute_margins(db, season, era, position_col, results_table, session_type):
    """position_col: 'finishing_position' or 'final_position'.
    results_table: 'race_results' or 'qualifying_results'.
    session_type: 'R' or 'Q' -- uses THAT session's own weather, since quali and race
    conditions can genuinely differ (dry quali into a wet race is common)."""
    filters = ["re1.role = 'race_driver'", "re2.role = 'race_driver'",
               f"rr1.{position_col} IS NOT NULL", f"rr2.{position_col} IS NOT NULL"]
    params = {}
    if season is not None:
        filters.append("r.season_year = :season")
        params["season"] = season
    if era is not None:
        filters.append("r.regulation_era = :era")
        params["era"] = era

    query = f"""
        SELECT re1.driver_id, sw.rainfall,
               (rr2.{position_col} - rr1.{position_col}) AS margin_over_teammate
        FROM race_entries re1
        JOIN race_entries re2 ON re2.race_id = re1.race_id
            AND re2.team_id = re1.team_id
            AND re2.driver_id != re1.driver_id
        JOIN races r ON r.id = re1.race_id
        JOIN {results_table} rr1 ON rr1.race_entry_id = re1.id
        JOIN {results_table} rr2 ON rr2.race_entry_id = re2.id
        JOIN sessions s ON s.race_id = r.id AND s.session_type = '{session_type}'
            AND rr1.session_id = s.id
        JOIN session_weather sw ON sw.session_id = s.id
        WHERE {' AND '.join(filters)}
    """
    return db.execute(text(query), params).mappings().all()


def build_ranking(rows, min_wet, min_dry):
    by_driver = {}
    for row in rows:
        d = by_driver.setdefault(row["driver_id"], {"wet": [], "dry": []})
        key = "wet" if row["rainfall"] else "dry"
        d[key].append(row["margin_over_teammate"])

    results = []
    for driver_id, data in by_driver.items():
        wet_n, dry_n = len(data["wet"]), len(data["dry"])
        if wet_n < min_wet or dry_n < min_dry:
            continue
        wet_avg = sum(data["wet"]) / wet_n
        dry_avg = sum(data["dry"]) / dry_n
        results.append({
            "driver_id": driver_id,
            "wet_avg_margin_over_teammate": round(wet_avg, 2),
            "dry_avg_margin_over_teammate": round(dry_avg, 2),
            "specialist_score": round(wet_avg - dry_avg, 2),
            "wet_sessions_counted": wet_n,
            "dry_sessions_counted": dry_n,
        })
    results.sort(key=lambda r: r["specialist_score"], reverse=True)
    return results


@router.get("/wet-weather-ranking")
def get_wet_weather_ranking(
    db: Session = Depends(get_db),
    season: int | None = Query(None, description="Restrict to a single season, e.g. 2023"),
    era: str | None = Query(None, description="e.g. era1_13inch, era2_18inch_groundeffect, era3_2026regs"),
    min_wet_races: int = Query(3, ge=1),
    min_dry_races: int = Query(5, ge=1),
    include_qualifying: bool = Query(True, description="Also compute a qualifying-based specialist score"),
):
    race_rows = compute_margins(db, season, era, "finishing_position", "race_results", "R")
    race_rankings = build_ranking(race_rows, min_wet_races, min_dry_races)

    quali_rankings = []
    if include_qualifying:
        quali_rows = compute_margins(db, season, era, "final_position", "qualifying_results", "Q")
        quali_rankings = build_ranking(quali_rows, min_wet_races, min_dry_races)

    all_ids = {r["driver_id"] for r in race_rankings} | {r["driver_id"] for r in quali_rankings}
    name_map = {}
    if all_ids:
        names = db.execute(text("SELECT id, name FROM drivers WHERE id = ANY(:ids)"),
                            {"ids": list(all_ids)}).mappings().all()
        name_map = {n["id"]: n["name"] for n in names}

    for r in race_rankings:
        r["driver_name"] = name_map.get(r["driver_id"])
    for r in quali_rankings:
        r["driver_name"] = name_map.get(r["driver_id"])

    return {
        "filters": {"season": season, "regulation_era": era,
                    "min_wet_races_required": min_wet_races, "min_dry_races_required": min_dry_races},
        "note": "specialist_score = (avg margin over teammate in wet sessions) minus "
                "(avg margin over teammate in dry sessions), using each session's own weather. "
                "Positive = performs relatively better against their own teammate when it's wet.",
        "race_rankings": race_rankings,
        "qualifying_rankings": quali_rankings,
    }
