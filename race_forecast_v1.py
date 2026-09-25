"""Pre-race forecast: the production strategy stack, computed and stored per race.

What is shown, and why (each chose by the walk-forward backtest, see VALIDATION):
  predicted position   grid/pace blend: half grid rank, half race-pace rank where
                       race pace = qualifying-implied gap + recent team race form
  P(win), P(podium)    historical rate for the driver's grid slot (same era)
  strategy             the era's most common dry strategy with typical stop laps
  what-if scenarios    simulator v2 replayed from the stored inputs -- scenarios,
                       NOT forecasts (it did not beat the blend at forecasting)

Everything uses races strictly before the race date. Computing needs the full lap
history, so it runs as a batch job once qualifying is known; the API only reads
the stored result and replays what-ifs from the stored snapshot.

    python race_forecast_v1.py --race-id 175
    python race_forecast_v1.py --season 2026 --missing      # every race with qualifying but no forecast
"""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from race_form_signal_v1 import DriverForm, build_rows, choose_weight, predictions, team_form, team_residual_history
from race_pace_from_quali_v1 import best_quali_times, fit_model, load_observations, quali_gaps
from race_status import DNF_STATUSES
from race_strategy_backtest_v2 import grid_slot_rates
from race_strategy_precedent_v1 import build_strategy, historical_strategy_options, load_precedents
from race_strategy_simulator_v1 import Distribution, Strategy, StrategyStint
from race_strategy_simulator_v2 import GREEN, SC, VSC, CarSpec, EventModel, TrackModel, simulate_race

MODEL_VERSION = "forecast_v1"
WHAT_IF_THRESHOLD = 0.45   # s/lap to pass; chosen by walk-forward calibration on 2023 and 2024
WHAT_IF_NOISE_SCALE = 1.0  # full measured race-day spread: scenarios should show real uncertainty

VALIDATION = {
    "source": "race_strategy_backtest_v2 walk-forward, dry races 2024-2025, 717 drivers in 36 races",
    "predicted_position": {"method": "grid/pace blend", "mean_abs_error_places": 3.009,
                           "grid_only_mean_abs_error_places": 3.071},
    "p_win": {"method": "historical rate for grid slot", "brier_score": 0.028},
    "p_podium": {"method": "historical rate for grid slot", "brier_score": 0.061},
    "strategy": {"method": "era's most common dry strategy", "stop_count_accuracy": 0.703,
                 "sequence_accuracy": 0.468, "first_stop_mean_abs_error_laps": 9.9},
    "what_if": {"method": "simulator v2", "note": "scenario tool; did not beat the blend at forecasting"},
}


class ForecastError(ValueError):
    """The race cannot be forecast yet (e.g. no qualifying) or lacks history."""


def strategy_to_json(strategy: Strategy, weight: float | None = None) -> dict:
    out = {"sequence": list(strategy.sequence), "stop_laps": list(strategy.stop_laps)}
    if weight is not None:
        out["weight"] = weight
    return out


def strategy_from_json(data: dict, total_laps: int) -> Strategy:
    sequence, stops = [str(c).upper() for c in data["sequence"]], [int(s) for s in data.get("stop_laps", [])]
    if len(stops) != len(sequence) - 1:
        raise ValueError("stop_laps must have one entry fewer than sequence")
    if any(not 1 <= s < total_laps for s in stops) or stops != sorted(set(stops)):
        raise ValueError(f"stop_laps must be increasing laps between 1 and {total_laps - 1}")
    bounds = [0, *stops, total_laps]
    stints = tuple(StrategyStint(sequence[i], bounds[i] + 1, bounds[i + 1]) for i in range(len(sequence)))
    return Strategy(" → ".join(sequence), stints, source="user")


def _dist(d: dict) -> Distribution:
    return Distribution(d["mean"], d["std"], d.get("lower"), d.get("upper"))


def _dist_json(d: Distribution) -> dict:
    return {"mean": round(float(d.mean), 4), "std": round(float(d.std), 4),
            "lower": d.lower if d.lower is None else round(float(d.lower), 4),
            "upper": d.upper if d.upper is None else round(float(d.upper), 4)}


# --- compute ------------------------------------------------------------------

def compute_forecast(db: Any, race_id: int) -> dict:
    from race_strategy_calibration_v1 import calibrate_event_hazards
    from race_strategy_data_adapter_v1 import load_event_observations
    from race_strategy_pit_calibration_v1 import calibrate_total_pit_lane_from_db
    from race_strategy_pit_loss_v2 import estimate_pit_loss, load_observations as load_pit_losses

    race = db.execute(text("""
        SELECT r.id, r.season_year, r.round_number, r.race_date, r.track_id, r.regulation_era,
               COALESCE(t.total_race_laps, (
                   SELECT MAX(l.lap_number) FROM laps l JOIN sessions s ON s.id = l.session_id
                   JOIN races r2 ON r2.id = s.race_id
                   WHERE r2.track_id = r.track_id AND s.session_type = 'R' AND r2.race_date < r.race_date
               )) AS total_laps
        FROM races r JOIN tracks t ON t.id = r.track_id WHERE r.id = :r
    """), {"r": race_id}).mappings().first()
    if race is None:
        raise ForecastError("race not found")
    if not race["regulation_era"] or not race["total_laps"] or not race["race_date"]:
        raise ForecastError("race has no era, date or known distance")
    era, season, as_of, total_laps = race["regulation_era"], race["season_year"], race["race_date"], int(race["total_laps"])

    # grid: official starting grid once the race has results, else qualifying order
    field = db.execute(text("""
        SELECT re.driver_id, re.team_id, q.final_position, rr.starting_grid_position
        FROM qualifying_results q
        JOIN sessions sq ON sq.id = q.session_id AND sq.session_type = 'Q'
        JOIN race_entries re ON re.id = q.race_entry_id
        LEFT JOIN sessions sr ON sr.race_id = sq.race_id AND sr.session_type = 'R'
        LEFT JOIN race_results rr ON rr.session_id = sr.id AND rr.race_entry_id = re.id
        WHERE sq.race_id = :r AND re.role = 'race_driver'
    """), {"r": race_id}).mappings().all()
    if len(field) < 10:
        raise ForecastError("qualifying results not available yet")
    has_start = all(f["starting_grid_position"] is not None for f in field)
    back = len(field) + 1
    grid = {
        int(f["driver_id"]): (int(f["starting_grid_position"]) if has_start and f["starting_grid_position"] > 0
                              else back if has_start else int(f["final_position"] or back))
        for f in field
    }

    # pace = qualifying-implied gap + recent team form, all from races before as_of
    observations = [o for o in load_observations(db, start_year=2018, end_year=season)
                    if o.race_date is not None and o.race_date < as_of]
    try:
        model = fit_model([o for o in observations if o.regulation_era == era])
    except ValueError as exc:
        raise ForecastError(f"not enough race history in this era: {exc}") from exc
    times = best_quali_times(db, race_id)
    gaps = quali_gaps(times)
    pole = min(t for t in times.values() if t)
    back_gap = max(gaps.values()) if gaps else 0.03
    history = team_residual_history(observations, era=era, before=as_of)
    rows = []
    for f in sorted(field, key=lambda f: (grid[int(f["driver_id"])], f["driver_id"])):
        d, team = int(f["driver_id"]), int(f["team_id"])
        rows.append(DriverForm(race_id, season, d, grid[d], 0, True, model.race_gap(gaps.get(d, back_gap)),
                               team_form(history.get(team, []), team, as_of), None))

    previous = build_rows(db, observations, start_year=season - 1, end_year=season - 1)
    prev_by_race = defaultdict(list)
    for r in previous:
        if r.season_year == season - 1:
            prev_by_race[r.race_id].append(r)
    weight = choose_weight(prev_by_race) if prev_by_race else 0.5
    predicted = predictions(rows, weight)["blend"]

    finishes = db.execute(text("""
        SELECT rr.starting_grid_position, rr.finishing_position, rr.status
        FROM race_results rr
        JOIN sessions s ON s.id = rr.session_id AND s.session_type = 'R'
        JOIN races r ON r.id = s.race_id
        WHERE r.regulation_era = :era AND r.race_date < :d
          AND rr.starting_grid_position > 0 AND rr.finishing_position IS NOT NULL
    """), {"era": era, "d": as_of}).all()
    slot_rates = grid_slot_rates([(int(g), int(f)) for g, f, _ in finishes], field_size=len(field))

    precedents = [p for p in load_precedents(db, before_year=season) if p.regulation_era == era]
    options = historical_strategy_options((p.strategy for p in precedents), total_laps, top_k=4)
    if not options:
        raise ForecastError("no historical dry strategies in this era")
    total_weight = sum(w for _, w in options)
    strategy_payload = {
        "most_common": strategy_to_json(options[0][0]),
        "alternatives": [strategy_to_json(s, round(w / total_weight, 3)) for s, w in options],
    }

    # simulator snapshot for what-if scenarios
    spread_rows = [r for r in previous if r.residual is not None]
    spread = float(np.std([r.residual - r.form for r in spread_rows])) if len(spread_rows) >= 30 else model.residual_std
    reference_lap = pole * model.race_to_pole_ratio
    pit_observations = load_pit_losses(db, start_year=2018, end_year=season)
    losses = {}
    for condition in ("green", "sc", "vsc"):
        est = estimate_pit_loss(pit_observations, track_id=race["track_id"], regulation_era=era,
                                condition=condition, before_date=as_of)
        if est is not None:
            losses[condition] = Distribution(est.median_seconds, est.robust_std_seconds, 5.0, 60.0)
    if "green" not in losses:
        losses["green"] = calibrate_total_pit_lane_from_db(db, start_year=2018, end_year=season - 1,
                                                           era=era).total_pit_lane_seconds
    for condition in ("sc", "vsc"):
        losses.setdefault(condition, losses["green"])
    event_obs, _ = load_event_observations(db, era=era, start_year=2018, end_year=season - 1)
    hazards, _ = calibrate_event_hazards(event_obs)
    dnf = (sum(s in DNF_STATUSES for *_, s in finishes) + 1) / (len(finishes) + 2)

    drivers = []
    for r, position in zip(rows, predicted):
        rate = slot_rates.get(r.grid, (0.0, 0.0))
        drivers.append({
            "driver_id": r.driver_id, "grid": r.grid, "predicted_position": position,
            "p_win": round(rate[0], 4), "p_podium": round(rate[1], 4),
            "pace": _dist_json(Distribution(reference_lap * (1 + r.implied_gap + r.form), reference_lap * spread,
                                            reference_lap * 0.95, reference_lap * 1.10)),
            "team_form": round(r.form, 5),
        })
    return {
        "race_id": race_id, "as_of_date": as_of, "grid_source": "starting_grid" if has_start else "qualifying",
        "drivers": drivers, "strategy": strategy_payload,
        "inputs": {
            "total_laps": total_laps, "blend_weight": weight, "drivers": drivers,
            "strategy_options": [strategy_to_json(s, w) for s, w in options],
            "pit_loss": {k: _dist_json(v) for k, v in losses.items()},
            "hazards": {k: float(v) for k, v in hazards.items()}, "dnf_probability": dnf,
            "overtake_threshold": WHAT_IF_THRESHOLD, "noise_scale": WHAT_IF_NOISE_SCALE,
        },
    }


def store_forecast(db: Any, forecast: dict) -> int:
    forecast_id = db.execute(text("""
        INSERT INTO race_forecasts (race_id, model_version, as_of_date, grid_source, inputs, validation)
        VALUES (:r, :v, :d, :g, CAST(:i AS JSONB), CAST(:val AS JSONB))
        ON CONFLICT (race_id, model_version) DO UPDATE SET
            as_of_date = EXCLUDED.as_of_date, grid_source = EXCLUDED.grid_source,
            inputs = EXCLUDED.inputs, validation = EXCLUDED.validation, computed_at = now()
        RETURNING id
    """), {"r": forecast["race_id"], "v": MODEL_VERSION, "d": forecast["as_of_date"], "g": forecast["grid_source"],
           "i": json.dumps(forecast["inputs"], default=str), "val": json.dumps(VALIDATION)}).scalar()
    db.execute(text("DELETE FROM race_forecast_drivers WHERE forecast_id = :f"), {"f": forecast_id})
    for d in forecast["drivers"]:
        db.execute(text("""
            INSERT INTO race_forecast_drivers (forecast_id, driver_id, grid, predicted_position, p_win, p_podium, strategy)
            VALUES (:f, :d, :g, :p, :w, :pod, CAST(:s AS JSONB))
        """), {"f": forecast_id, "d": d["driver_id"], "g": d["grid"], "p": d["predicted_position"],
               "w": d["p_win"], "pod": d["p_podium"], "s": json.dumps(forecast["strategy"]["most_common"])})
    return forecast_id


# --- what-if --------------------------------------------------------------------

@dataclass(frozen=True)
class Scenario:
    safety_car_laps: tuple[int, ...] = ()
    vsc_laps: tuple[int, ...] = ()
    strategies: tuple[tuple[int, dict], ...] = ()   # (driver_id, {"sequence": [...], "stop_laps": [...]})


def run_what_if(inputs: dict, scenario: Scenario, *, sims: int = 1000, seed: int = 7) -> list[dict]:
    """Baseline vs scenario finishing distribution for every driver (common random numbers)."""
    total_laps = int(inputs["total_laps"])
    options = tuple((strategy_from_json(o, total_laps), float(o["weight"])) for o in inputs["strategy_options"])
    overrides = {int(d): strategy_from_json(s, total_laps) for d, s in scenario.strategies}
    known = {d["driver_id"] for d in inputs["drivers"]}
    unknown = set(overrides) - known
    if unknown:
        raise ValueError(f"drivers not in this race: {sorted(unknown)}")
    for lap in (*scenario.safety_car_laps, *scenario.vsc_laps):
        if not 1 <= lap <= total_laps:
            raise ValueError(f"neutralised laps must be between 1 and {total_laps}")

    scale = float(inputs["noise_scale"])
    track = TrackModel(total_laps=total_laps, tyre_deg_per_lap={c: Distribution(0.0) for c in ("SOFT", "MEDIUM", "HARD")},
                       pit_loss={k: _dist(v) for k, v in inputs["pit_loss"].items()},
                       overtake_threshold_seconds=float(inputs["overtake_threshold"]),
                       dnf_probability=float(inputs["dnf_probability"]))
    events = EventModel(sc_per_lap=inputs["hazards"]["sc_probability_per_lap_dry"],
                        vsc_per_lap=inputs["hazards"]["vsc_probability_per_lap_dry"],
                        red_per_lap=inputs["hazards"]["red_flag_probability_per_lap_dry"])

    def cars(with_overrides: bool) -> list[CarSpec]:
        out = []
        for d in inputs["drivers"]:
            pace = _dist(d["pace"])
            pace = Distribution(pace.mean, pace.std * scale, pace.lower, pace.upper)
            chosen = ((overrides[d["driver_id"]], 1.0),) if with_overrides and d["driver_id"] in overrides else options
            out.append(CarSpec(f"driver:{d['driver_id']}", int(d["grid"]), pace, chosen))
        return out

    state = None
    if scenario.safety_car_laps or scenario.vsc_laps:
        state = np.full(total_laps + 1, GREEN, dtype=np.int8)
        state[list(scenario.vsc_laps)] = VSC
        state[list(scenario.safety_car_laps)] = SC
    base = simulate_race(cars(False), track, events, sims=sims, seed=seed)
    what_if = simulate_race(cars(True), track, events, sims=sims, seed=seed, event_state=state)

    result = []
    for j, d in enumerate(inputs["drivers"]):
        b, w = base[:, j], what_if[:, j]
        result.append({
            "driver_id": d["driver_id"], "grid": d["grid"],
            "baseline": {"expected_position": round(float(b.mean()), 2), "p_win": round(float((b == 1).mean()), 4),
                         "p_podium": round(float((b <= 3).mean()), 4)},
            "scenario": {"expected_position": round(float(w.mean()), 2), "p_win": round(float((w == 1).mean()), 4),
                         "p_podium": round(float((w <= 3).mean()), 4)},
            "expected_position_change": round(float(w.mean() - b.mean()), 2),
        })
    return result


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--race-id", type=int)
    parser.add_argument("--season", type=int)
    parser.add_argument("--missing", action="store_true", help="with --season: only races without a forecast")
    args = parser.parse_args()
    if not args.race_id and not args.season:
        parser.error("give --race-id or --season")

    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as db:
        if args.race_id:
            race_ids = [args.race_id]
        else:
            race_ids = db.execute(text("""
                SELECT r.id FROM races r
                WHERE r.season_year = :s
                  AND EXISTS (SELECT 1 FROM sessions s JOIN qualifying_results q ON q.session_id = s.id
                              WHERE s.race_id = r.id AND s.session_type = 'Q')
                  AND (NOT :missing OR NOT EXISTS (
                        SELECT 1 FROM race_forecasts f WHERE f.race_id = r.id AND f.model_version = :v))
                ORDER BY r.round_number
            """), {"s": args.season, "missing": args.missing, "v": MODEL_VERSION}).scalars().all()
    failures = 0
    for race_id in race_ids:
        with engine.begin() as db:
            try:
                forecast = compute_forecast(db, race_id)
                forecast_id = store_forecast(db, forecast)
                leader = min(forecast["drivers"], key=lambda d: d["predicted_position"])
                print(f"race {race_id}: stored forecast {forecast_id} ({forecast['grid_source']}), "
                      f"predicted winner driver {leader['driver_id']}")
            except ForecastError as exc:
                failures += 1
                print(f"race {race_id}: SKIP {exc}")
    return 1 if failures and len(race_ids) == 1 else 0


if __name__ == "__main__":
    raise SystemExit(main())
