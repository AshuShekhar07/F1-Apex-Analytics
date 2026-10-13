"""Race analysis for fans: read-only views built from the stored timing. Nothing here reads
or writes a prediction model."""

from fastapi import APIRouter, Depends, HTTPException, Path, Query
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


@router.get("/{race_id}/ideal-lap")
def get_ideal_lap(race_id: int = Path(..., ge=1),
                  session: str | None = Query(None, pattern="^(FP1|FP2|FP3|SQ|Q)$"),
                  db: Session = Depends(get_db)):
    """Each driver's ideal lap in a session (best sector 1 + 2 + 3 from any lap) against their
    real best lap, the time left on the table, the session's fastest sectors and the ultimate
    lap. session: FP1, FP2, FP3, SQ or Q (default: qualifying, else the latest with timing)."""
    from ideal_lap_v1 import load_ideal_lap

    table = load_ideal_lap(db, race_id, session)
    if table is None:
        raise HTTPException(status_code=404, detail="Race not found")
    return table
