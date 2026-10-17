from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session

from app.database import get_db

router = APIRouter(prefix="/cars", tags=["cars"])


def _with_fair(car: dict, fair: dict) -> dict:
    return {**car, "fair": fair.get(car["key"])}


@router.get("")
def get_cars(season: int | None = Query(None, ge=2018, le=2100), db: Session = Depends(get_db)):
    """Every team's car of 2018 onwards: name, power unit and livery (cars_catalogue_v1) with
    ratings measured from the timing data (car_ratings_v1): qualifying pace, race pace, top speed,
    reliability, an overall rating and the car's character; and era-fair measures for comparing
    cars of different seasons (car_compare_v1: dominance over its own season's field, the race
    model's car strength with driver skill separated, a fingerprint, normalised achievements and
    the drivers' head-to-heads). Read-only; no prediction model changes."""
    from car_compare_v1 import ERAS, fair_table
    from car_ratings_v1 import car_table

    data = car_table(db)
    fair = fair_table(db)["fair"]
    cars = data["cars"] if season is None else [c for c in data["cars"] if c["season"] == season]
    if season is not None and not cars:
        raise HTTPException(status_code=404, detail=f"no cars with results in {season}")
    return {**data, "cars": [_with_fair(c, fair) for c in cars], "count": len(cars),
            "eras": {code: {"label": label, "rules": rules} for code, (label, rules) in ERAS.items()}}


@router.get("/{key}")
def get_car(key: str = Path(..., min_length=6, max_length=60, pattern=r"^\d{4}-[a-z0-9-]+$"), db: Session = Depends(get_db)):
    """One car with its era-fair measures and its race-by-race record against its own field (for
    the development curve and the same-circuit comparison)."""
    from car_compare_v1 import fair_table
    from car_ratings_v1 import car_table

    car = next((c for c in car_table(db)["cars"] if c["key"] == key), None)
    if car is None:
        raise HTTPException(status_code=404, detail=f"no car {key}")
    table = fair_table(db)
    return {**_with_fair(car, table["fair"]), "races": table["races"].get(key, [])}
