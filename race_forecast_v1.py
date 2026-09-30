"""Pre-race forecast: the production strategy stack, computed and stored per race.

What is shown, and why (each chose by the walk-forward backtest, see VALIDATION):
  predicted position   grid/pace blend: half grid rank, half race-pace rank where
                       race pace = qualifying-implied gap + recent team race form
  P(win), P(podium)    historical rate for the driver's grid slot (same era)
  strategy             the era's most common dry strategy with typical stop laps
  confidence           race_confidence_v1: how far off the prediction usually is in earlier
                       races with the same conditions (dry / wet qualifying / rain forecast),
                       and an 80% range per driver
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

from race_form_signal_v1 import (
    DriverForm,
    build_rows,
    car_quali_gaps,
    choose_weight,
    predictions,
    DRIVER_FORM_RACES,
    DRIVER_SHRINK,
    out_of_position,
    car_form_histories,
    choose_recovery_weight,
    practice_beta,
    sprint_beta,
    team_form,
    team_residual_history,
    with_car_form,
    with_car_pace,
    with_driver_form,
    with_practice_correction,
    with_sprint_correction,
)
from race_pace_from_quali_v1 import (
    SESSION_WET_SQL,
    best_quali_times,
    fit_model,
    load_observations,
    quali_gaps,
    quali_session_times,
)
from race_status import DNF_STATUSES
from race_strategy_backtest_v2 import grid_slot_rates
from race_strategy_precedent_v1 import build_strategy, historical_strategy_options, load_precedents
from race_strategy_simulator_v1 import Distribution, Strategy, StrategyStint
from race_strategy_simulator_v2 import GREEN, SC, VSC, CarSpec, EventModel, TrackModel, simulate_race

MODEL_VERSION = "forecast_v1"
WHAT_IF_THRESHOLD = 0.45   # s/lap to pass; chosen by walk-forward calibration on 2023 and 2024
# Race-day pace spread multiplier for what-ifs. 1.0 (the full measured spread) made the
# field far too random (2026 R12 pole sitter: baseline P6.5, 18% win vs 67% in the
# forecast). 0.25 was the walk-forward-calibrated value with the best win/podium Brier in
# the all-driver backtest. Applied at replay time, so stored forecasts need no recompute.
WHAT_IF_NOISE_SCALE = 0.25
SLOT_SHRINK_STARTS = 10    # grid-slot rates: era evidence weighted against 10 starts of the all-era rate

# Research switches. Each is flipped BY HAND only after its walk-forward backtest passes;
# nothing is promoted automatically.
#   STRATEGY_METHOD   "era_mode": one era-wide most-common strategy for every driver (validated)
#                     "hierarchy": per-driver track/grid-band precedent with real stints.
#                     2024-25 backtest: WORSE at the full sequence (0.217 vs 0.307) -> off.
#   OPTIMISER_ENABLED tyre model v3 optimiser shown as an extra block. 2024-25: the wear model
#                     beats a flat model (gate 1 PASS) but the optimiser's picks do not match
#                     what teams run (sequence 0.055, gate 2 FAIL) -> off.
#   WET_SWITCH        for upcoming races, report the rain forecast and warn when rain is
#                     expected. No wet strategy is shown: the wet precedent (method W) did not
#                     beat the baseline in 2022-25 wet races (sequence accuracy 0.015).
STRATEGY_METHOD = "era_mode"
# Pace used by the blend: "car_pace" or "own_lap" (the previous method).
# car_pace (race_form_signal_v1, v3): a driver >1% off the teammate in their last common
# qualifying session, or with no time, gets the teammate's pace + 0.3%. Fires for ~6% of
# drivers. prediction_scoreboard_v1 2018-2026 (177 races): finishers off by 10+ places
# 2.5% -> 2.1% (CI excludes 0), worse nowhere. Kept only if the recomputed 2025-26 stored
# forecasts also pass --compare (v1 was rolled back for failing exactly that check).
PACE_METHOD = "car_pace"
# Practice long-run pace (race_practice_pace_v1): pace += beta * practice surprise (long-run
# pace minus quali-implied pace, centred per race), beta fitted on all earlier seasons.
# Scoreboard 2018-2026: signal r=+0.35 (CI above 0), 10+ place misses 2.1% -> 1.8% (CI
# excludes 0), worse nowhere. "none" switches it off. Kept only if the recomputed 2025-26
# stored forecasts also pass --compare.
PRACTICE_METHOD = "correction"
# Team form residuals against CAR pace (a qualifying crash is no longer five races of fake
# race form), plus each driver's race-vs-qualifying habit relative to the teammate, plus the
# sprint-race pace correction on sprint weekends (beta fitted on earlier seasons).
# Scoreboard 2018-2026, 180 races (blend_combo): average error 3.347 -> 3.314, finishers
# 2.749 -> 2.716 (both CIs exclude 0), order correlation better, worse nowhere. "team" /
# "none" switch them off.
FORM_METHOD = "car_and_driver"
SPRINT_METHOD = "correction"
# Drivers starting >= 3 places behind their qualifying position (grid penalty, pit lane) get
# their own blend weight, chosen on the last 3 seasons' such drivers. Scoreboard 2018-2026:
# those finishers 4.04 -> 3.54 places, -0.45 [-0.79, -0.12] over 94 races, worse nowhere.
# "none" switches it off.
RECOVERY_METHOD = "own_weight"
RECOVERY_SEASONS = 3
OPTIMISER_ENABLED = False
WET_SWITCH = True

VALIDATION = {
    "source": "prediction_scoreboard_v1 walk-forward, all races 2018-2026, 3621 drivers in 180 races",
    "predicted_position": {"method": "grid/pace blend with car pace, practice and sprint pace, car-pace team "
                                     "form, driver form and a recovery weight for out-of-position starters",
                           "mean_abs_error_places": 3.308, "grid_only_mean_abs_error_places": 3.490,
                           "finishers_mean_abs_error_places": 2.712, "finishers_off_by_10_or_more": 0.019,
                           "winner_correct": 0.572, "podium_overlap": 0.691},
    "p_win": {"method": "historical rate for grid slot", "brier_score": 0.033},
    "p_podium": {"method": "historical rate for grid slot", "brier_score": 0.069},
    "strategy": {"method": "era's most common dry strategy",
                 "truth": "real stints (same-compound stops count); race_strategy_prediction_backtest_v1, "
                          "dry races 2024-2025, 670 cars in 37 races",
                 "stop_count_accuracy": 0.472, "sequence_accuracy": 0.307,
                 "first_stop_mean_abs_error_laps": 9.28, "first_stop_within_3_laps": 0.211},
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

MIN_ERA_FINISHES = 200     # below this, grid-slot rates and DNF rate borrow earlier eras


def production_pace(rows: Sequence[DriverForm]) -> list[DriverForm]:
    """Car-pace step of production (PACE_METHOD)."""
    return with_car_pace(rows) if PACE_METHOD == "car_pace" else list(rows)


def production_transform(history: Sequence[DriverForm]):
    """The full production pace rule given rows of all earlier seasons: car pace, the
    practice correction, car-pace team form + driver form, the sprint correction; betas
    fitted on `history`. The scoreboard scores exactly this.
    Returns a function rows -> rows with attributes `beta` and `sprint_beta`."""
    beta = practice_beta(production_pace(history)) if PRACTICE_METHOD == "correction" else 0.0

    def pace(rows: Sequence[DriverForm]) -> list[DriverForm]:
        paced = production_pace(rows)
        return with_practice_correction(paced, beta) if beta > 0 else paced

    beta_s = sprint_beta(pace(history)) if SPRINT_METHOD == "correction" else 0.0

    def transform(rows: Sequence[DriverForm]) -> list[DriverForm]:
        out = pace(rows)
        if FORM_METHOD == "car_and_driver":
            out = with_driver_form(with_car_form(out))
        return with_sprint_correction(out, beta_s) if beta_s > 0 else out
    transform.beta = beta
    transform.sprint_beta = beta_s
    return transform


def production_recovery_weight(history: Sequence[DriverForm], season: int, transform, weight: float) -> float | None:
    """Blend weight for out-of-position drivers, chosen on the last RECOVERY_SEASONS seasons."""
    if RECOVERY_METHOD != "own_weight":
        return None
    recent = defaultdict(list)
    for r in history:
        if season - RECOVERY_SEASONS <= r.season_year < season:
            recent[r.race_id].append(r)
    return choose_recovery_weight({k: transform(v) for k, v in recent.items()}, weight)


_HISTORY_CACHE: dict[tuple[str, int], list[DriverForm]] = {}


def history_rows(db: Any, observations, season: int) -> list[DriverForm]:
    """Form rows for every race of seasons before `season` (with practice pace), cached per
    database and season because a --season batch forecasts many races of one season."""
    key = (str(db.engine.url), season)
    if key not in _HISTORY_CACHE:
        _HISTORY_CACHE[key] = [r for r in build_rows(db, observations, start_year=2018, end_year=season - 1,
                                                     include_wet=True,
                                                     with_practice=PRACTICE_METHOD == "correction",
                                                     with_sprint=SPRINT_METHOD == "correction")
                               if r.season_year < season]
    return _HISTORY_CACHE[key]


_SAMPLES_CACHE: dict[tuple[str, int], list] = {}


def confidence_samples(db: Any, observations, season: int) -> list:
    """Walk-forward errors of the production blend on every season before `season`."""
    from race_confidence_v1 import backtest_samples

    key = (str(db.engine.url), season)
    if key not in _SAMPLES_CACHE:
        wet_races = {int(r) for r in db.execute(text(f"""
            SELECT s.race_id FROM sessions s LEFT JOIN session_weather sw ON sw.session_id = s.id
            WHERE s.session_type = 'R' AND {SESSION_WET_SQL}""")).scalars()}
        _SAMPLES_CACHE[key] = backtest_samples(history_rows(db, observations, season), production_transform,
                                               choose_weight, predictions, wet_races, production_recovery_weight)
    return _SAMPLES_CACHE[key]


def quali_was_wet(db: Any, race_id: int) -> bool:
    return bool(db.execute(text(f"""
        SELECT {SESSION_WET_SQL} FROM sessions s LEFT JOIN session_weather sw ON sw.session_id = s.id
        WHERE s.race_id = :r AND s.session_type = 'Q'"""), {"r": race_id}).scalar())


def shrunk_slot_rates(era_history, all_history, *, field_size: int, k: float = SLOT_SHRINK_STARTS) -> dict:
    """P(win), P(podium) per grid slot: this era's record pulled toward the all-era rate.

    rate = (era successes + k * all-era rate) / (era starts + k). With little era data
    (a new regulation era) the long-run rate dominates; as the era fills in, its own
    record takes over. Avoids e.g. a 0.4% win chance from P3 after 12 races of 2026.
    Rates are then made non-increasing down the grid (weighted by evidence per slot).
    """
    prior = grid_slot_rates(all_history, field_size=field_size)
    counts = defaultdict(lambda: [0, 0, 0])
    for grid, finish in era_history:
        c = counts[grid]
        c[0] += 1
        c[1] += int(finish == 1)
        c[2] += int(finish <= 3)
    slots = sorted(prior)
    weights = [counts[s][0] + k for s in slots]
    wins = non_increasing([(counts[s][1] + k * prior[s][0]) / (counts[s][0] + k) for s in slots], weights)
    pods = non_increasing([(counts[s][2] + k * prior[s][1]) / (counts[s][0] + k) for s in slots], weights)
    return {s: (w, p) for s, w, p in zip(slots, wins, pods)}


def non_increasing(values: Sequence[float], weights: Sequence[float]) -> list[float]:
    """Weighted isotonic fit (pool adjacent violators): a worse grid slot never gets a
    higher rate. With ~12 races of a new era, P4 could otherwise out-podium P3 by luck."""
    blocks: list[list[float]] = []            # [weighted mean, weight, count]
    for v, w in zip(values, weights):
        blocks.append([v, w, 1])
        while len(blocks) > 1 and blocks[-2][0] < blocks[-1][0]:
            (v2, w2, n2), (v1, w1, n1) = blocks.pop(), blocks.pop()
            blocks.append([(v1 * w1 + v2 * w2) / (w1 + w2), w1 + w2, n1 + n2])
    return [v for v, _, n in blocks for _ in range(n)]


def _today():
    from datetime import date
    return date.today()


def _race_conditions(track_id: int, race_date) -> dict:
    """Weather for an upcoming race (fetch_race_forecast needs DATABASE_URL and network)."""
    from fetch_race_forecast import predict_race_conditions
    return predict_race_conditions(track_id, race_date)


def extra_strategy_blocks(db: Any, race: dict, total_laps: int, rows: Sequence, payload: dict,
                          notes: list[str]) -> dict[int, dict]:
    """Per-driver strategies, optimiser and wet blocks, each behind its research switch.

    Mutates `payload` (the forecast's strategy block) and returns {driver_id: strategy}
    when STRATEGY_METHOD is "hierarchy"; otherwise every driver shares `most_common`.
    """
    from race_strategy_timing_v1 import load_car_strategies, predict_strategy

    as_of, era, track = race["race_date"], race["regulation_era"], race["track_id"]
    per_driver: dict[int, dict] = {}
    history = load_car_strategies(db, before_date=as_of) if STRATEGY_METHOD == "hierarchy" else []

    if STRATEGY_METHOD == "hierarchy":
        payload["method"] = "hierarchy"
        for r in rows:
            p = predict_strategy(history, track_id=track, regulation_era=era, grid=r.grid, total_laps=total_laps)
            if p.strategy is not None:
                per_driver[r.driver_id] = {**strategy_to_json(p.strategy), "source": p.source}

    if OPTIMISER_ENABLED:
        from race_strategy_pit_loss_v2 import estimate_pit_loss, load_observations as load_pit_losses
        from race_tyre_model_v3 import fit_for_race, load_tyre_observations, optimise

        model = fit_for_race(load_tyre_observations(db, end_year=race["season_year"]),
                             track_id=track, regulation_era=era, before_date=as_of)
        loss = estimate_pit_loss(load_pit_losses(db, start_year=2018, end_year=race["season_year"]),
                                 track_id=track, regulation_era=era, before_date=as_of)
        if model is None or loss is None:
            notes.append("model-optimal strategy: not enough tyre or pit-loss evidence")
        else:
            payload["model_optimal"] = {
                "source": model.source, "pit_loss_seconds": loss.median_seconds,
                "options": [{**strategy_to_json(s), "seconds_slower_than_best": gap}
                            for s, gap in optimise(model, total_laps=total_laps, pit_loss=loss.median_seconds)],
            }

    if WET_SWITCH and as_of >= _today():
        try:
            weather = _race_conditions(track, as_of)
        except Exception as exc:                      # network, API or missing coordinates
            weather = {"status": "error", "message": str(exc)}
        if weather.get("status") != "ok":
            notes.append(f"weather: unavailable ({weather.get('message', 'unknown error')}); dry strategy only")
        else:
            payload["weather"] = {k: weather.get(k) for k in (
                "tier", "rain_expected", "rain_onset_lap", "wet_strategy_trigger", "target_track_temp")}
            if weather.get("wet_strategy_trigger"):
                notes.append("weather: rain forecast during the race. Strategies in wet races are not "
                             "predictable from history (best backtest sequence accuracy 8.5%); the dry "
                             "strategy, positions and what-ifs are low confidence")
    return per_driver


_CAR_STRATEGIES: dict[str, list] = {}


def predicted_strategy(db: Any, race_id: int, as_of, era: str, total_laps: int) -> dict | None:
    """The strategy service's pick for this race (method I: stop count from this weekend's
    practice tyre wear; era mode before practice), same history cut-off as the forecast."""
    from race_strategy_timing_v1 import load_car_strategies, predict_from_wear
    from strategy_service_v7 import db_url, practice_wear

    url = db_url(db)
    if url not in _CAR_STRATEGIES:
        _CAR_STRATEGIES[url] = load_car_strategies(db)
    history = [c for c in _CAR_STRATEGIES[url] if c.race_date < as_of]
    wear = practice_wear(db, {race_id, *(c.race_id for c in history)})
    p = predict_from_wear(history, wear=wear.get(race_id), wear_by_race=wear, regulation_era=era,
                          total_laps=total_laps)
    if p.strategy is None:
        return None
    return {**strategy_to_json(p.strategy), "source": p.source}


def provisional_grid(db: Any, race_id: int) -> dict[int, int]:
    """driver_id -> provisional grid slot (fetch_starting_grid_v1.py), {} if none stored."""
    if not db.execute(text("SELECT to_regclass('race_provisional_grid') IS NOT NULL")).scalar():
        return {}
    return {int(d): int(p) for d, p in db.execute(text("""
        SELECT re.driver_id, pg.grid_position FROM race_provisional_grid pg
        JOIN race_entries re ON re.id = pg.race_entry_id WHERE pg.race_id = :r
    """), {"r": race_id})}


def compute_forecast(db: Any, race_id: int) -> dict:
    """Forecast from races strictly before the race date.

    In a new regulation era some evidence does not exist yet; each fallback used is
    recorded in `data_notes` (stored and returned by the API) instead of hidden.
    """
    from race_strategy_calibration_v1 import calibrate_event_hazards
    from race_strategy_data_adapter_v1 import load_event_observations
    from race_strategy_pit_calibration_v1 import calibrate_total_pit_lane_from_db
    from race_strategy_pit_loss_v2 import estimate_pit_loss, load_observations as load_pit_losses

    notes: list[str] = []
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

    # grid: official starting slot where the race has one, else qualifying position
    field = db.execute(text("""
        SELECT re.driver_id, re.team_id, q.final_position, rr.starting_grid_position, dr.name AS driver_name
        FROM qualifying_results q
        JOIN sessions sq ON sq.id = q.session_id AND sq.session_type = 'Q'
        JOIN race_entries re ON re.id = q.race_entry_id
        JOIN drivers dr ON dr.id = re.driver_id
        LEFT JOIN sessions sr ON sr.race_id = sq.race_id AND sr.session_type = 'R'
        LEFT JOIN race_results rr ON rr.session_id = sr.id AND rr.race_entry_id = re.id
        WHERE sq.race_id = :r AND re.role = 'race_driver'
    """), {"r": race_id}).mappings().all()
    times = best_quali_times(db, race_id)
    if len(field) < 10 or not any(times.values()):
        raise ForecastError("qualifying results or lap times not available yet")
    back = len(field) + 1
    provisional = provisional_grid(db, race_id)
    grid, from_start = {}, 0
    for f in field:
        start = f["starting_grid_position"]
        if start is not None:
            grid[int(f["driver_id"])] = int(start) if start > 0 else back     # 0 = pit-lane start
            from_start += 1
        else:
            grid[int(f["driver_id"])] = int(f["final_position"] or back)
    grid_source = ("starting_grid" if from_start == len(field)
                   else "qualifying" if from_start == 0 else "mixed")
    if grid_source == "qualifying" and provisional:
        # official grid not published in the results yet: the provisional grid (penalties
        # applied); a qualified driver missing from it starts from the back (pit lane)
        unlisted = [f["driver_name"] for f in field if int(f["driver_id"]) not in provisional]
        for f in field:
            grid[int(f["driver_id"])] = provisional.get(int(f["driver_id"]), back)
        grid_source = "provisional_grid"
        moved = [f["driver_name"] for f in field
                 if f["final_position"] and grid[int(f["driver_id"])] - int(f["final_position"]) >= 3]
        notes.append("grid: provisional starting grid (OpenF1) with penalties"
                     + (f"; starting well behind their qualifying position: {', '.join(moved)}" if moved else "")
                     + (f"; not on it (pit lane?): {', '.join(unlisted)}" if unlisted else ""))
    elif grid_source == "qualifying":
        notes.append("grid: qualifying order -- no official or provisional grid yet, so grid penalties are not in")
    if grid_source == "mixed":
        notes.append(f"grid: official slot for {from_start}/{len(field)} drivers, qualifying position for the rest")

    # pace = qualifying-implied gap + recent team form, all from races before as_of
    observations = [o for o in load_observations(db, start_year=2018, end_year=season)
                    if o.race_date is not None and o.race_date < as_of]
    try:
        model = fit_model([o for o in observations if o.regulation_era == era])
    except ValueError:
        earlier = [o for o in observations if o.regulation_era != era]
        if not earlier:
            raise ForecastError("no race-pace history at all")
        previous_era = max(earlier, key=lambda o: o.race_date).regulation_era
        model = fit_model([o for o in earlier if o.regulation_era == previous_era])
        notes.append(f"pace model: too few {era} races yet, using {previous_era} relation")
    gaps = quali_gaps(times)
    pole = min(t for t in times.values() if t)
    back_gap = max(gaps.values()) if gaps else 0.03
    history = team_residual_history(observations, era=era, before=as_of)
    if not history:
        notes.append("team race form: no earlier races in this era, form = 0 for every team")
    own_gaps = {int(f["driver_id"]): gaps.get(int(f["driver_id"]), back_gap) for f in field}
    pace_gaps = (car_quali_gaps(own_gaps, {int(f["driver_id"]): int(f["team_id"]) for f in field},
                                quali_session_times(db, race_id))
                 if PACE_METHOD == "car_pace" else own_gaps)
    names = {int(f["driver_id"]): f["driver_name"] for f in field}
    for d in sorted(pace_gaps):
        if pace_gaps[d] < own_gaps[d]:
            notes.append(f"car pace: {names[d]}'s qualifying lap was missing or well off the teammate's; "
                         "using the teammate's pace + 0.3%")
    practice = {}
    if PRACTICE_METHOD == "correction":
        from race_practice_pace_v1 import load_practice_gaps
        practice, practice_session = load_practice_gaps(db, race_id)
    sprint = {}
    if SPRINT_METHOD == "correction":
        from race_practice_pace_v1 import load_sprint_gaps
        sprint = load_sprint_gaps(db, race_id)
    car_teams, car_drivers = (car_form_histories(db, observations, era=era, before=as_of)
                              if FORM_METHOD == "car_and_driver" else ({}, {}))
    rows = []
    for f in sorted(field, key=lambda f: (grid[int(f["driver_id"])], f["driver_id"])):
        d, team = int(f["driver_id"]), int(f["team_id"])
        rows.append(DriverForm(race_id, season, d, grid[d], 0, True, model.race_gap(pace_gaps[d]),
                               team_form(history.get(team, []), team, as_of), None, team, None, practice.get(d),
                               False,
                               team_form(car_teams.get(team, []), team, as_of),
                               team_form(car_drivers.get(d, []), d, as_of, races=DRIVER_FORM_RACES,
                                         shrink=DRIVER_SHRINK),
                               sprint.get(d), int(f["final_position"]) if f["final_position"] else None))

    past_rows = history_rows(db, observations, season)
    transform = production_transform(past_rows)
    rows = transform(rows)     # car pace is already in; this adds practice, car/driver form and sprint
    if PRACTICE_METHOD == "correction":
        with_long_run = sum(r.practice_gap is not None for r in rows)
        notes.append(f"practice: long-run pace for {with_long_run}/{len(rows)} drivers"
                     + (f" ({practice_session})" if practice else "") + f", correction beta {transform.beta:.2f}"
                     if with_long_run else "practice: no usable long runs (wet or no practice), qualifying pace only")
    prev_by_race = defaultdict(list)
    for r in past_rows:
        if r.season_year == season - 1:
            prev_by_race[r.race_id].append(r)
    if SPRINT_METHOD == "correction" and sprint:
        notes.append(f"sprint: race pace from the sprint for {len(sprint)}/{len(rows)} drivers, "
                     f"correction beta {transform.sprint_beta:.2f}")
    weight = choose_weight({k: transform(v) for k, v in prev_by_race.items()}) if prev_by_race else 0.5
    if not prev_by_race:
        notes.append("blend weight: no previous-season races scored, using 0.5")
    recovery = production_recovery_weight(past_rows, season, transform, weight)
    moved = [names[r.driver_id] for r in rows if out_of_position(r)]
    if recovery is not None and moved:
        notes.append(f"out of position (grid penalty / pit lane): {', '.join(moved)}; recovery weight {recovery:.1f}")
    predicted = predictions(rows, weight, recovery)["blend"]

    finishes_sql = """
        SELECT rr.starting_grid_position, rr.finishing_position, rr.status
        FROM race_results rr
        JOIN sessions s ON s.id = rr.session_id AND s.session_type = 'R'
        JOIN races r ON r.id = s.race_id
        WHERE r.race_date < :d AND (CAST(:era AS TEXT) IS NULL OR r.regulation_era = :era)
          AND rr.starting_grid_position > 0 AND rr.finishing_position IS NOT NULL
    """
    era_finishes = db.execute(text(finishes_sql), {"era": era, "d": as_of}).all()
    all_finishes = db.execute(text(finishes_sql), {"era": None, "d": as_of}).all()
    finishes = era_finishes
    if len(era_finishes) < MIN_ERA_FINISHES:
        finishes = all_finishes
        notes.append(f"win/podium rates and retirement rate: fewer than {MIN_ERA_FINISHES} {era} results, "
                     "using all earlier eras")
    slot_rates = shrunk_slot_rates(
        [(int(g), int(f)) for g, f, _ in finishes], [(int(g), int(f)) for g, f, _ in all_finishes],
        field_size=len(field))

    all_precedents = load_precedents(db, before_date=as_of)
    options = historical_strategy_options(
        (p.strategy for p in all_precedents if p.regulation_era == era), total_laps, top_k=4)
    if not options:
        earlier_eras = [p for p in all_precedents if p.regulation_era != era]
        if earlier_eras:
            last_era = max(earlier_eras, key=lambda p: p.season_year).regulation_era
            options = historical_strategy_options(
                (p.strategy for p in earlier_eras if p.regulation_era == last_era), total_laps, top_k=4)
            notes.append(f"strategy: no dry {era} races yet, using {last_era} strategies")
    if not options:
        raise ForecastError("no historical dry strategies")
    total_weight = sum(w for _, w in options)
    strategy_payload = {
        "method": "era_mode",
        "most_common": strategy_to_json(options[0][0]),
        "alternatives": [strategy_to_json(s, round(w / total_weight, 3)) for s, w in options],
    }
    race_plan = predicted_strategy(db, race_id, as_of, era, total_laps)
    if race_plan is not None:
        strategy_payload["predicted"] = race_plan
    driver_strategies = extra_strategy_blocks(db, race, total_laps, rows, strategy_payload, notes)

    # simulator snapshot for what-if scenarios
    spread_rows = [r for rs in prev_by_race.values() for r in rs if r.residual is not None]
    spread = float(np.std([r.residual - r.form for r in spread_rows])) if len(spread_rows) >= 30 else model.residual_std
    reference_lap = pole * model.race_to_pole_ratio
    pit_observations = load_pit_losses(db, start_year=2018, end_year=season)
    losses = {}
    earlier_eras = sorted({o.regulation_era for o in pit_observations
                           if o.regulation_era != era and o.race_date < as_of},
                          key=lambda e: max(o.race_date for o in pit_observations if o.regulation_era == e),
                          reverse=True)
    for condition in ("green", "sc", "vsc"):
        for scope in (era, *earlier_eras[:1]):
            est = estimate_pit_loss(pit_observations, track_id=race["track_id"], regulation_era=scope,
                                    condition=condition, before_date=as_of)
            if est is not None:
                losses[condition] = Distribution(est.median_seconds, est.robust_std_seconds, 5.0, 60.0)
                if scope != era:
                    notes.append(f"pit loss ({condition}): no {era} evidence yet, using {scope}")
                break
    if "green" not in losses:
        for scope in (era, None):
            try:
                losses["green"] = calibrate_total_pit_lane_from_db(
                    db, start_year=2018, end_year=season - 1, era=scope).total_pit_lane_seconds
                notes.append(f"pit loss: v1 pit-lane time ({scope or 'all eras'})")
                break
            except ValueError:
                continue
        else:
            raise ForecastError("no pit-stop history")
    for condition in ("sc", "vsc"):
        if condition not in losses:
            losses[condition] = losses["green"]
            notes.append(f"pit loss under {condition.upper()}: no evidence, no discount applied")
    event_obs, _ = load_event_observations(db, era=era, start_year=2018, end_year=season - 1)
    if not event_obs:
        event_obs, _ = load_event_observations(db, era=None, start_year=2018, end_year=season - 1)
        notes.append("Safety Car / VSC / red-flag rates: no earlier seasons in this era, using all eras")
    hazards, _ = calibrate_event_hazards(event_obs)
    dnf = (sum(s in DNF_STATUSES for *_, s in finishes) + 1) / (len(finishes) + 2)

    from race_confidence_v1 import ErrorTable
    try:
        rain = bool((strategy_payload.get("weather") or {}).get("wet_strategy_trigger"))
        confidence = ErrorTable.fit(confidence_samples(db, observations, season)).label(
            wet_quali=quali_was_wet(db, race_id), rain_expected=rain,
            predicted={r.driver_id: p for r, p in zip(rows, predicted)}, field=len(rows))
    except ValueError as exc:
        confidence = None
        notes.append(f"confidence: {exc}")

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
        if confidence:
            drivers[-1]["range"] = confidence["ranges"][str(r.driver_id)]
        if r.driver_id in driver_strategies:
            drivers[-1]["strategy"] = driver_strategies[r.driver_id]
    return {
        "race_id": race_id, "as_of_date": as_of, "grid_source": grid_source,
        "drivers": drivers, "strategy": strategy_payload,
        "inputs": {
            "total_laps": total_laps, "blend_weight": weight, "drivers": drivers,
            "strategy_options": [strategy_to_json(s, w) for s, w in options],
            "strategy_extras": {k: v for k, v in strategy_payload.items() if k not in ("most_common", "alternatives")},
            "pit_loss": {k: _dist_json(v) for k, v in losses.items()},
            "hazards": {k: float(v) for k, v in hazards.items()}, "dnf_probability": dnf,
            "overtake_threshold": WHAT_IF_THRESHOLD, "noise_scale": WHAT_IF_NOISE_SCALE,
            "confidence": {k: v for k, v in confidence.items() if k != "ranges"} if confidence else None,
            "data_notes": notes,
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
               "w": d["p_win"], "pod": d["p_podium"], "s": json.dumps(d.get("strategy") or forecast["strategy"]["most_common"])})
    return forecast_id


# --- what-if --------------------------------------------------------------------

@dataclass(frozen=True)
class Scenario:
    safety_car_laps: tuple[int, ...] = ()
    vsc_laps: tuple[int, ...] = ()
    strategies: tuple[tuple[int, dict], ...] = ()   # (driver_id, {"sequence": [...], "stop_laps": [...]})


def _position_summary(positions: np.ndarray) -> dict:
    counts = np.bincount(positions.astype(int))
    return {"expected_position": round(float(positions.mean()), 2),
            "median_position": int(np.median(positions)),
            "most_likely_position": int(counts.argmax()),
            "p_win": round(float((positions == 1).mean()), 4),
            "p_podium": round(float((positions <= 3).mean()), 4)}


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

    scale = WHAT_IF_NOISE_SCALE   # current calibrated value (stored inputs keep the one used at compute time)
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
            "baseline": _position_summary(b),
            "scenario": _position_summary(w),
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
                notes = forecast["inputs"]["data_notes"]
                print(f"race {race_id}: stored forecast {forecast_id} ({forecast['grid_source']}), "
                      f"predicted winner driver {leader['driver_id']}"
                      + (f"  notes: {'; '.join(notes)}" if notes else ""))
            except ForecastError as exc:
                failures += 1
                print(f"race {race_id}: SKIP {exc}")
            except Exception as exc:  # one bad race must not stop the batch
                failures += 1
                print(f"race {race_id}: ERROR {type(exc).__name__}: {exc}")
    return 1 if failures and len(race_ids) == 1 else 0


if __name__ == "__main__":
    raise SystemExit(main())
