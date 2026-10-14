from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db

router = APIRouter(prefix="/cars", tags=["cars"])


@router.get("")
def get_cars(season: int | None = Query(None, ge=2018, le=2100), db: Session = Depends(get_db)):
    """Every team's car of 2018 onwards: name, power unit and livery (cars_catalogue_v1) with
    ratings measured from the timing data (car_ratings_v1): qualifying pace, race pace, top speed,
    reliability, an overall rating and the car's character. Read-only; no prediction model."""
    from car_ratings_v1 import car_table

    data = car_table(db)
    cars = data["cars"] if season is None else [c for c in data["cars"] if c["season"] == season]
    if season is not None and not cars:
        raise HTTPException(status_code=404, detail=f"no cars with results in {season}")
    return {**data, "cars": cars, "count": len(cars)}
