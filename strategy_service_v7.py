"""Strategy prediction service behind GET /strategy/predict (replaces strategy_production_v6).

Built on race_strategy_timing_v1 (real stints, stop timing, pre-start stage):

  stage "pre_qualifying"  track + era evidence only
  stage "post_qualifying" grid given: track + grid band evidence first
  stage "pre_start"       starting compound given: history filtered to that start tyre

Weather: for races 0-16 days out, a near-term rain forecast (fetch_race_forecast
wet_strategy_trigger) adds a wet-race precedent block. A weather outage is a data
note, not an error: the dry prediction still stands.

Only races strictly before race_date are used. Real stints for every race are
expensive to rebuild, so they are loaded once per process and refreshed after
CACHE_SECONDS.
"""

from __future__ import annotations

import time
from datetime import date
from threading import Lock
from typing import Any

from sqlalchemy import text

from race_strategy_timing_v1 import CarStrategy, StrategyPrediction, load_car_strategies, predict_strategy
from race_strategy_simulator_v1 import Strategy

MODEL_VERSION = "strategy_v7"
CACHE_SECONDS = 6 * 3600
FORECAST_WINDOW_DAYS = 16

VALIDATION = {
    "backtest": "race_strategy_prediction_backtest_v1 (walk-forward, truth = real stints)",
    "method": "C hierarchy: track + grid band -> track -> era + band -> era",
    "status": "pending: run the backtest on production data and record the numbers here",
}

_cache: dict[str, Any] = {"loaded_at": 0.0, "rows": []}
_lock = Lock()


class StrategyNotFound(LookupError):
    pass


def _history(db: Any) -> list[CarStrategy]:
    with _lock:
        if not _cache["rows"] or time.time() - _cache["loaded_at"] > CACHE_SECONDS:
            _cache["rows"] = load_car_strategies(db)
            _cache["loaded_at"] = time.time()
        return _cache["rows"]


def clear_cache() -> None:
    with _lock:
        _cache["rows"], _cache["loaded_at"] = [], 0.0


def _race_context(db: Any, track_id: int, race_date: date) -> dict:
    track = db.execute(text("SELECT id, name, total_race_laps FROM tracks WHERE id = :t"),
                       {"t": track_id}).mappings().first()
    if track is None:
        raise StrategyNotFound(f"track {track_id} not found")
    race = db.execute(text("""
        SELECT id, regulation_era FROM races
        WHERE track_id = :t AND race_date BETWEEN CAST(:d AS DATE) - 3 AND CAST(:d AS DATE) + 3
        ORDER BY abs(race_date - CAST(:d AS DATE)) LIMIT 1
    """), {"t": track_id, "d": race_date}).mappings().first()
    era = race["regulation_era"] if race and race["regulation_era"] else db.execute(text("""
        SELECT regulation_era FROM races WHERE race_date <= :d AND regulation_era IS NOT NULL
        ORDER BY race_date DESC LIMIT 1
    """), {"d": race_date}).scalar()
    if era is None:
        raise StrategyNotFound("no regulation era known on or before this date")
    total_laps = track["total_race_laps"] or db.execute(text("""
        SELECT MAX(l.lap_number) FROM laps l
        JOIN sessions s ON s.id = l.session_id AND s.session_type = 'R'
        JOIN races r ON r.id = s.race_id
        WHERE r.track_id = :t AND r.race_date < :d
    """), {"t": track_id, "d": race_date}).scalar()
    if not total_laps:
        raise StrategyNotFound("race distance unknown for this track")
    return {"track_id": track_id, "track_name": track["name"], "race_id": race["id"] if race else None,
            "regulation_era": era, "total_laps": int(total_laps)}


def _strategy_json(strategy: Strategy, share: float | None = None) -> dict:
    out = {
        "sequence": list(strategy.sequence),
        "stop_laps": list(strategy.stop_laps),
        "stints": [{"compound": s.compound, "start_lap": s.start_lap, "end_lap": s.end_lap} for s in strategy.stints],
    }
    if share is not None:
        out["share"] = round(share, 3)
    return out


def _prediction_json(p: StrategyPrediction) -> dict:
    return {
        "source": p.source,
        "primary_strategy": _strategy_json(*p.alternatives[0]),
        "alternatives": [_strategy_json(s, w) for s, w in p.alternatives[1:]],
    }


def _weather(track_id: int, race_date: date) -> dict:
    from fetch_race_forecast import predict_race_conditions
    return predict_race_conditions(track_id, race_date)


def predict(db: Any, track_id: int, race_date: date, *, grid: int | None = None,
            start_compound: str | None = None, today: date | None = None) -> dict:
    ctx = _race_context(db, track_id, race_date)
    history = [c for c in _history(db) if c.race_date < race_date]
    notes: list[str] = []
    era, laps = ctx["regulation_era"], ctx["total_laps"]

    dry = predict_strategy(history, track_id=track_id, regulation_era=era, grid=grid, total_laps=laps,
                           start_compound=start_compound)
    if dry.strategy is None and start_compound:
        notes.append(f"no {era} dry race started on {start_compound.upper()}; ignoring the start tyre")
        dry = predict_strategy(history, track_id=track_id, regulation_era=era, grid=grid, total_laps=laps)
    if dry.strategy is None:
        earlier = sorted({c.regulation_era for c in history if c.regulation_era != era},
                         key=lambda e: max(c.race_date for c in history if c.regulation_era == e))
        if earlier:
            dry = predict_strategy(history, track_id=track_id, regulation_era=earlier[-1], grid=grid,
                                   total_laps=laps, start_compound=start_compound)
            notes.append(f"no dry {era} races yet; using {earlier[-1]} strategies")
    if dry.strategy is None:
        raise StrategyNotFound("no historical dry strategies before this date")
    if not dry.source.startswith("track"):
        notes.append("fewer than 15 earlier cars at this track in this era; evidence widened to the whole era")

    stage = "pre_start" if start_compound else "post_qualifying" if grid is not None else "pre_qualifying"
    result = {
        "status": "ok", "model_version": MODEL_VERSION, **ctx, "race_date": race_date, "stage": stage,
        "grid": grid, "start_compound": start_compound.upper() if start_compound else None,
        **_prediction_json(dry), "weather": None, "wet_strategy": None,
        "validation": VALIDATION, "data_notes": notes,
    }

    days_out = (race_date - (today or date.today())).days
    if 0 <= days_out <= FORECAST_WINDOW_DAYS:
        try:
            weather = _weather(track_id, race_date)
        except Exception as exc:                      # network, API or missing coordinates
            weather = {"status": "error", "message": str(exc)}
        if weather.get("status") != "ok":
            notes.append(f"weather unavailable ({weather.get('message', 'unknown error')}); dry prediction only")
        else:
            result["weather"] = {k: weather.get(k) for k in (
                "tier", "days_out", "rain_expected", "rain_onset_lap", "wet_strategy_trigger", "target_track_temp")}
            if weather.get("wet_strategy_trigger"):
                wet = predict_strategy(history, track_id=track_id, regulation_era=era, grid=grid,
                                       total_laps=laps, wet=True)
                if wet.strategy is None:
                    notes.append("rain forecast, but no wet races in this era to learn from")
                else:
                    result["wet_strategy"] = _prediction_json(wet)
                    notes.append("rain forecast during the race: wet-race precedent included")
    return result
