"""Race analysis for fans: read-only views built from the stored timing. Nothing here reads
or writes a prediction model."""

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.orm import Session

from app.database import get_db

router = APIRouter(prefix="/races", tags=["race analysis"])


@router.get("/{race_id}/race-trace")
def get_race_trace(race_id: int = Path(..., ge=1), db: Session = Depends(get_db)):
    """Every car's gap to the leader on every lap, and the race trace (time against the winner's
    average lap), with each car's pit stops and the Safety Car / VSC / red-flag laps. Lists are
    indexed by lap - 1; null where the timing has no lap end."""
    from race_trace_v1 import load_race_trace

    trace = load_race_trace(db, race_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="Race not found")
    return trace
