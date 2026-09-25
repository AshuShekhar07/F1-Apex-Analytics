"""Walk-forward backtest of the layered strategy stack, for EVERY starter.

Layers under test (see README "Strategy stack"):
  1. strategy precedent  (race_strategy_precedent_v1) -- which strategy a car will run
  2. outcome simulator   (race_strategy_simulator_v2) -- finishing position, P(win), P(podium)
     with race pace = quali-implied gap + recent team race-form (race_form_signal_v1),
     precedent strategies for every car, retirements, SC/VSC hazards and pit loss v2.
     The overtake threshold and the race-day pace-spread multiplier are chosen on the
     PREVIOUS season from a fixed grid (walk-forward), unless fixed on the command line.
  3. optimiser -- deliberately OFF: no validated tyre model exists, and the first
     backtest showed the optimiser picking SOFT-MEDIUM-SOFT in all 34 races.

Every metric is compared with a baseline that does not use the layer under test:
  finish position   simulator expected finish   vs  grid position, and vs the
                                                    grid/pace blend (race_form_signal_v1)
  P(win), P(podium) simulator probabilities     vs  historical rate for that grid slot
  strategy          precedent (track/grid band) vs  era's most common strategy

Confidence intervals resample whole RACES (drivers in one race are not
independent). A layer is production-ready only if its interval lies entirely
below zero (lower = better for every metric here).

Walk-forward: every input uses seasons before the target season, except pit
loss, which uses races before the target date.

    python race_strategy_backtest_v2.py --start-year 2024 --end-year 2025 --csv backtest_v2.csv
"""

from __future__ import annotations

import argparse
import csv
import os
import random
from collections import defaultdict
from dataclasses import asdict, dataclass
from statistics import mean
from typing import Any, Sequence

import numpy as np
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from race_pace_from_quali_v1 import load_observations as load_pace_observations
from race_status import DNF_STATUSES
from race_strategy_precedent_v1 import (
    historical_strategy_options,
    load_precedents,
    precedent_options,
)
from race_strategy_simulator_v1 import Distribution, Strategy, StrategyStint, TyreAllocation
from race_strategy_simulator_v2 import CarSpec, EventModel, TrackModel, simulate_race

DEFAULT_ALLOCATION = TyreAllocation({"SOFT": 2, "MEDIUM": 2, "HARD": 2})


@dataclass(frozen=True)
class DriverRow:
    race_id: int
    year: int
    driver_id: int
    grid: int
    actual_finish: int
    status: str | None
    expected_finish: float
    p_win: float
    p_podium: float
    baseline_p_win: float
    baseline_p_podium: float
    actual_sequence: str | None
    precedent_strategy: str | None
    precedent_source: str
    precedent_sequence_match: int | None
    precedent_stop_count_match: int | None
    precedent_first_stop_error: float | None
    era_mode_strategy: str | None
    era_mode_sequence_match: int | None
    era_mode_stop_count_match: int | None
    era_mode_first_stop_error: float | None
    blend_rank: int | None = None


# --- pure helpers --------------------------------------------------------------

def strategy_metrics(selected: Strategy | None, actual: Strategy | None) -> tuple[int | None, int | None, float | None]:
    """(sequence match, stop-count match, first-stop lap error) against the realised strategy."""
    if selected is None or actual is None:
        return None, None, None
    first = (abs(selected.stop_laps[0] - actual.stop_laps[0])
             if selected.stop_laps and actual.stop_laps else None)
    return (int(selected.sequence == actual.sequence),
            int(len(selected.stop_laps) == len(actual.stop_laps)),
            float(first) if first is not None else None)


def brier(probability: float, outcome: int) -> float:
    return (probability - outcome) ** 2


def grid_slot_rates(history: Sequence[tuple[int, int]], field_size: int = 20) -> dict[int, tuple[float, float]]:
    """P(win), P(podium) by grid slot from (grid, finish) history, Laplace-smoothed."""
    counts = defaultdict(lambda: [0, 0, 0])
    for grid, finish in history:
        c = counts[grid]
        c[0] += 1
        c[1] += int(finish == 1)
        c[2] += int(finish <= 3)
    return {
        g: ((counts[g][1] + 1 / field_size) / (counts[g][0] + 1),
            (counts[g][2] + 3 / field_size) / (counts[g][0] + 1))
        for g in range(1, field_size + 3)
    }


def race_clustered_ci(rows: Sequence[Any], metric, *, seed: int = 11, draws: int = 4000) -> tuple[float, float, float]:
    """Mean of per-race means of metric(row) with a 90% bootstrap interval over races."""
    per_race = defaultdict(list)
    for row in rows:
        value = metric(row)
        if value is not None:
            per_race[row.race_id].append(value)
    race_means = [mean(v) for v in per_race.values() if v]
    if not race_means:
        return float("nan"), float("nan"), float("nan")
    rng = random.Random(seed)
    boots = sorted(mean(rng.choices(race_means, k=len(race_means))) for _ in range(draws))
    return mean(race_means), boots[int(0.05 * draws)], boots[int(0.95 * draws) - 1]


def summarise(rows: Sequence[DriverRow]) -> dict[str, Any]:
    def diff(a, b):
        return lambda r: (a(r) - b(r)) if a(r) is not None and b(r) is not None else None

    sim_err = lambda r: abs(r.expected_finish - r.actual_finish)
    grid_err = lambda r: abs(r.grid - r.actual_finish)
    blend_err = lambda r: abs(r.blend_rank - r.actual_finish) if r.blend_rank is not None else None
    sim_win = lambda r: brier(r.p_win, int(r.actual_finish == 1))
    base_win = lambda r: brier(r.baseline_p_win, int(r.actual_finish == 1))
    sim_pod = lambda r: brier(r.p_podium, int(r.actual_finish <= 3))
    base_pod = lambda r: brier(r.baseline_p_podium, int(r.actual_finish <= 3))
    miss = lambda field: (lambda r: None if getattr(r, field) is None else 1 - getattr(r, field))
    err = lambda field: (lambda r: getattr(r, field))

    return {
        "races": len({r.race_id for r in rows}),
        "drivers": len(rows),
        "finish_mae": (race_clustered_ci(rows, sim_err)[0], race_clustered_ci(rows, grid_err)[0],
                       race_clustered_ci(rows, diff(sim_err, grid_err))),
        **({"finish_mae_vs_blend": (race_clustered_ci(rows, sim_err)[0], race_clustered_ci(rows, blend_err)[0],
                                    race_clustered_ci(rows, diff(sim_err, blend_err)))}
           if all(r.blend_rank is not None for r in rows) else {}),
        "win_brier": (race_clustered_ci(rows, sim_win)[0], race_clustered_ci(rows, base_win)[0],
                      race_clustered_ci(rows, diff(sim_win, base_win))),
        "podium_brier": (race_clustered_ci(rows, sim_pod)[0], race_clustered_ci(rows, base_pod)[0],
                         race_clustered_ci(rows, diff(sim_pod, base_pod))),
        "sequence_miss": (race_clustered_ci(rows, miss("precedent_sequence_match"))[0],
                          race_clustered_ci(rows, miss("era_mode_sequence_match"))[0],
                          race_clustered_ci(rows, diff(miss("precedent_sequence_match"), miss("era_mode_sequence_match")))),
        "stop_count_miss": (race_clustered_ci(rows, miss("precedent_stop_count_match"))[0],
                            race_clustered_ci(rows, miss("era_mode_stop_count_match"))[0],
                            race_clustered_ci(rows, diff(miss("precedent_stop_count_match"), miss("era_mode_stop_count_match")))),
        "first_stop_error_laps": (race_clustered_ci(rows, err("precedent_first_stop_error"))[0],
                                  race_clustered_ci(rows, err("era_mode_first_stop_error"))[0],
                                  race_clustered_ci(rows, diff(err("precedent_first_stop_error"), err("era_mode_first_stop_error")))),
    }


# --- database assembly ---------------------------------------------------------

def _actual_strategies(db: Any, race_id: int) -> dict[int, Strategy]:
    stints = defaultdict(list)
    for driver_id, compound, start, end in db.execute(text("""
        SELECT re.driver_id, UPPER(rs.compound), rs.start_lap, rs.end_lap
        FROM race_stints rs JOIN race_entries re ON re.id = rs.race_entry_id
        WHERE rs.race_id = :r ORDER BY re.driver_id, rs.stint_number
    """), {"r": race_id}):
        if compound and start is not None and end is not None:
            try:
                stints[int(driver_id)].append(StrategyStint(compound, int(start), int(end)))
            except ValueError:
                continue
    return {d: Strategy(" → ".join(s.compound for s in st), tuple(st)) for d, st in stints.items()}


@dataclass
class RaceSetup:
    race: dict
    cars: list          # CarSpec with UNSCALED pace spread
    meta: list          # (entry, grid, options, precedent source)
    losses: dict
    deg: float
    dnf: float
    hazards: dict
    slot_rates: dict
    era_mode: Strategy
    blend_ranks: dict   # driver_id -> blend rank (weight chosen on the previous season)
    pit_sources: str


# Widened after the first calibrated run chose the smallest noise scale (0.25) and
# the lowest thresholds in both seasons, i.e. the optimum sat on the grid edge.
THRESHOLD_GRID = (0.1, 0.2, 0.3, 0.45, 0.6, 1.0)
NOISE_SCALE_GRID = (0.05, 0.1, 0.15, 0.25, 0.5)


def on_grid_edge(threshold: float, noise_scale: float) -> list[str]:
    """Which calibrated parameters landed on the boundary of their search grid."""
    edges = []
    if threshold in (THRESHOLD_GRID[0], THRESHOLD_GRID[-1]):
        edges.append(f"threshold={threshold}")
    if noise_scale in (NOISE_SCALE_GRID[0], NOISE_SCALE_GRID[-1]):
        edges.append(f"noise_scale={noise_scale}")
    return edges


def simulate_setup(setup: RaceSetup, *, threshold: float, noise_scale: float, sims: int, seed: int) -> np.ndarray:
    cars = [CarSpec(c.name, c.grid_position,
                    Distribution(c.base_pace.mean, c.base_pace.std * noise_scale, c.base_pace.lower, c.base_pace.upper),
                    c.strategy_options) for c in setup.cars]
    deg = Distribution(setup.deg, setup.deg * 0.3, 0.0, 0.3)
    track = TrackModel(total_laps=int(setup.race["total_laps"]),
                       tyre_deg_per_lap={c: deg for c in ("SOFT", "MEDIUM", "HARD")},
                       pit_loss=setup.losses, overtake_threshold_seconds=threshold, dnf_probability=setup.dnf)
    events = EventModel(sc_per_lap=setup.hazards["sc_probability_per_lap_dry"],
                        vsc_per_lap=setup.hazards["vsc_probability_per_lap_dry"],
                        red_per_lap=setup.hazards["red_flag_probability_per_lap_dry"])
    return simulate_race(cars, track, events, sims=sims, seed=seed)


def calibrate(setups: Sequence[RaceSetup], *, sims: int, seed: int) -> tuple[float, float, float]:
    """(threshold, noise_scale, mae) minimising finishing MAE on the given (earlier) races."""
    best = None
    for threshold in THRESHOLD_GRID:
        for noise_scale in NOISE_SCALE_GRID:
            errors = []
            for i, setup in enumerate(setups):
                expected = simulate_setup(setup, threshold=threshold, noise_scale=noise_scale,
                                          sims=sims, seed=seed + i).mean(axis=0)
                errors += [abs(x - int(e["finish"])) for x, (e, *_rest) in zip(expected, setup.meta)]
            mae = mean(errors) if errors else float("inf")
            if best is None or mae < best[2]:
                best = (threshold, noise_scale, mae)
    return best


def prepare_setups(db: Any, *, start_year: int, end_year: int, deg_mode: str) -> dict[int, list[RaceSetup]]:
    """Walk-forward race setups by season. Pace = quali-implied gap + recent team form."""
    from race_form_signal_v1 import build_rows, choose_weight, predictions
    from race_strategy_calibration_v1 import calibrate_event_hazards, calibrate_tyre_degradation
    from race_strategy_data_adapter_v1 import load_event_observations, load_tyre_observations
    from race_strategy_pit_calibration_v1 import calibrate_total_pit_lane_from_db
    from race_strategy_pit_loss_v2 import estimate_pit_loss, load_observations as load_pit_losses

    pace_obs = load_pace_observations(db, start_year=2018, end_year=end_year)
    models: dict[int, tuple] = {}
    form_rows = build_rows(db, pace_obs, start_year=start_year - 1, end_year=end_year, models=models)
    by_race = defaultdict(list)
    for r in form_rows:
        by_race[r.race_id].append(r)
    pit_observations = load_pit_losses(db, start_year=2018, end_year=end_year)

    races = db.execute(text("""
        SELECT r.id, r.season_year, r.track_id, r.regulation_era, r.race_date,
               COALESCE(t.total_race_laps, (SELECT MAX(l.lap_number) FROM laps l WHERE l.session_id = s.id)) AS total_laps
        FROM races r
        JOIN tracks t ON t.id = r.track_id
        JOIN sessions s ON s.race_id = r.id AND s.session_type = 'R'
        JOIN session_weather sw ON sw.session_id = s.id AND sw.rainfall = FALSE
        WHERE r.season_year BETWEEN :a AND :b AND r.regulation_era IS NOT NULL
        ORDER BY r.race_date
    """), {"a": start_year - 1, "b": end_year}).mappings().all()

    per_year: dict[tuple[str, int], dict[str, Any]] = {}
    precedents: dict[int, list] = {}
    setups: dict[int, list[RaceSetup]] = defaultdict(list)
    for race in races:
        year, era = race["season_year"], race["regulation_era"]
        race_rows = by_race.get(race["id"])
        if not race_rows or race["id"] not in models or not race["total_laps"]:
            print(f"SKIP race={race['id']}: no pace model, results or distance", flush=True)
            continue
        key = (era, year)
        if key not in per_year:
            tyre_obs, _ = load_tyre_observations(db, era=era, start_year=2018, end_year=year - 1)
            deg_raw, _ = calibrate_tyre_degradation(tyre_obs)
            event_obs, _ = load_event_observations(db, era=era, start_year=2018, end_year=year - 1)
            hazards, _ = calibrate_event_hazards(event_obs)
            finishes = db.execute(text("""
                SELECT rr.starting_grid_position, rr.finishing_position, rr.status
                FROM race_results rr
                JOIN sessions s ON s.id = rr.session_id AND s.session_type = 'R'
                JOIN races r ON r.id = s.race_id
                WHERE r.regulation_era = :era AND r.season_year < :year
                  AND rr.starting_grid_position > 0 AND rr.finishing_position IS NOT NULL
            """), {"era": era, "year": year}).all()
            dry_rates = [d.mean for c, d in deg_raw.items() if c in ("SOFT", "MEDIUM", "HARD")]
            pooled = float(np.median(dry_rates)) if dry_rates else 0.0
            earlier = [r for r in form_rows if r.season_year < year and r.residual is not None]
            spread = float(np.std([r.residual - r.form for r in earlier])) if len(earlier) >= 30 else None
            prev = defaultdict(list)
            for r in form_rows:
                if r.season_year == year - 1:
                    prev[r.race_id].append(r)
            per_year[key] = {
                "deg": 0.0 if deg_mode == "zero" else float(np.clip(pooled, 0.0, 0.15)),
                "hazards": hazards,
                "dnf": (sum(s in DNF_STATUSES for *_, s in finishes) + 1) / (len(finishes) + 2),
                "slot_rates": grid_slot_rates([(int(g), int(f)) for g, f, _ in finishes]),
                "spread": spread,
                "blend_weight": choose_weight(prev) if prev else 0.0,
                "pit_lane_v1": None,
            }
            if year not in precedents:
                precedents[year] = load_precedents(db, before_year=year)
        cfg = per_year[key]
        model, pole = models[race["id"]]
        total_laps = int(race["total_laps"])
        reference_lap = pole * model.race_to_pole_ratio
        spread = cfg["spread"] if cfg["spread"] is not None else model.residual_std

        era_options = historical_strategy_options(
            (r.strategy for r in precedents[year] if r.regulation_era == era), total_laps)
        if not era_options:
            print(f"SKIP race={race['id']}: no historical dry strategies", flush=True)
            continue
        entries = {r.driver_id: r for r in race_rows}
        results = db.execute(text("""
            SELECT re.driver_id, rr.starting_grid_position AS grid, rr.finishing_position AS finish, rr.status
            FROM race_results rr
            JOIN sessions s ON s.id = rr.session_id AND s.session_type = 'R'
            JOIN race_entries re ON re.id = rr.race_entry_id
            WHERE s.race_id = :r AND rr.finishing_position IS NOT NULL
        """), {"r": race["id"]}).mappings().all()
        cars, meta = [], []
        for e in sorted(results, key=lambda e: (entries[int(e["driver_id"])].grid, e["driver_id"])):
            row = entries.get(int(e["driver_id"]))
            if row is None:
                continue
            options, source = precedent_options(precedents[year], track_id=race["track_id"], regulation_era=era,
                                                grid=row.grid, before_year=year, total_laps=total_laps)
            options = options or era_options
            cars.append(CarSpec(
                f"driver:{row.driver_id}", row.grid,
                Distribution(reference_lap * (1 + row.implied_gap + row.form), reference_lap * spread,
                             reference_lap * 0.95, reference_lap * 1.10),
                tuple(options),
            ))
            meta.append((e, row.grid, options, source))

        losses, sources = {}, []
        for condition in ("green", "sc", "vsc"):
            est = estimate_pit_loss(pit_observations, track_id=race["track_id"], regulation_era=era,
                                    condition=condition, before_date=race["race_date"])
            if est is not None:
                losses[condition] = Distribution(est.median_seconds, est.robust_std_seconds, 5.0, 60.0)
                sources.append(f"{condition}:{est.source}")
        if "green" not in losses:
            if cfg["pit_lane_v1"] is None:
                cfg["pit_lane_v1"] = calibrate_total_pit_lane_from_db(
                    db, start_year=2018, end_year=year - 1, era=era).total_pit_lane_seconds
            losses["green"] = cfg["pit_lane_v1"]
            sources.append("green:v1_pit_lane_fallback")
        for condition in ("sc", "vsc"):
            losses.setdefault(condition, losses["green"])

        ordered_rows = [entries[int(e["driver_id"])] for e, *_ in meta]
        blend = predictions(ordered_rows, cfg["blend_weight"])["blend"]
        setups[year].append(RaceSetup(
            dict(race), cars, meta, losses, cfg["deg"], cfg["dnf"], cfg["hazards"], cfg["slot_rates"],
            era_options[0][0], {r.driver_id: b for r, b in zip(ordered_rows, blend)}, ";".join(sources),
        ))
    return setups


def run_backtest(db: Any, *, start_year: int, end_year: int, sims: int, seed: int,
                 overtake_threshold: float | None, noise_scale: float | None, deg_mode: str,
                 calibration_sims: int = 400) -> tuple[list[DriverRow], dict[int, tuple]]:
    """Score every starter of every dry race. Unset parameters are calibrated on the previous season."""
    setups = prepare_setups(db, start_year=start_year, end_year=end_year, deg_mode=deg_mode)
    rows: list[DriverRow] = []
    chosen: dict[int, tuple] = {}
    for year in range(start_year, end_year + 1):
        if not setups.get(year):
            continue
        if overtake_threshold is None or noise_scale is None:
            previous = setups.get(year - 1, [])
            if previous:
                threshold, scale, mae = calibrate(previous, sims=calibration_sims, seed=seed)
            else:
                threshold, scale, mae = 0.8, 1.0, float("nan")
            threshold = overtake_threshold if overtake_threshold is not None else threshold
            scale = noise_scale if noise_scale is not None else scale
        else:
            threshold, scale, mae = overtake_threshold, noise_scale, float("nan")
        chosen[year] = (threshold, scale, float(mae))
        edges = on_grid_edge(threshold, scale) if overtake_threshold is None or noise_scale is None else []
        print(f"{year}: overtake threshold {threshold}s, noise scale {scale} "
              f"(chosen on {year - 1}, MAE there {mae:.3f})"
              + (f"  WARNING: on search-grid edge ({', '.join(edges)})" if edges else ""), flush=True)

        for index, setup in enumerate(setups[year]):
            positions = simulate_setup(setup, threshold=threshold, noise_scale=scale, sims=sims, seed=seed + index)
            actual = _actual_strategies(db, setup.race["id"])  # post-hoc only
            for j, (e, grid, options, source) in enumerate(setup.meta):
                p = positions[:, j]
                realised = actual.get(int(e["driver_id"]))
                predicted = options[0][0] if options else None
                rate = setup.slot_rates.get(grid, (0.0, 0.0))
                rows.append(DriverRow(
                    setup.race["id"], year, int(e["driver_id"]), grid, int(e["finish"]), e["status"],
                    round(float(p.mean()), 3), round(float((p == 1).mean()), 4), round(float((p <= 3).mean()), 4),
                    round(rate[0], 4), round(rate[1], 4),
                    realised.name if realised else None,
                    predicted.name if predicted else None, source, *strategy_metrics(predicted, realised),
                    setup.era_mode.name, *strategy_metrics(setup.era_mode, realised),
                    setup.blend_ranks.get(int(e["driver_id"])),
                ))
            race_rows = rows[-len(setup.meta):]
            print(f"{year} race={setup.race['id']}: drivers={len(setup.meta)} finish MAE sim="
                  f"{mean(abs(r.expected_finish - r.actual_finish) for r in race_rows):.2f} "
                  f"grid={mean(abs(r.grid - r.actual_finish) for r in race_rows):.2f} "
                  f"blend={mean(abs(r.blend_rank - r.actual_finish) for r in race_rows if r.blend_rank):.2f} "
                  f"pit_loss={setup.pit_sources}", flush=True)
    return rows, chosen


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start-year", type=int, default=2024)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument("--sims", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--overtake-threshold", type=float, default=None,
                        help="fix the pass threshold (s/lap); default: calibrate on the previous season")
    parser.add_argument("--noise-scale", type=float, default=None,
                        help="fix the race-day pace spread multiplier; default: calibrate on the previous season")
    parser.add_argument("--deg-mode", choices=("pooled", "zero"), default="zero",
                        help="zero (default: scored better) or pooled compound-agnostic wear")
    parser.add_argument("--csv", default="")
    args = parser.parse_args()

    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as db:
        rows, chosen = run_backtest(db, start_year=args.start_year, end_year=args.end_year, sims=args.sims,
                                    seed=args.seed, overtake_threshold=args.overtake_threshold,
                                    noise_scale=args.noise_scale, deg_mode=args.deg_mode)
    if not rows:
        print("No races scored.")
        return 1
    s = summarise(rows)
    print(f"\n=== BACKTEST V2: {s['drivers']} drivers in {s['races']} races (pace = quali + team form, "
          f"deg {args.deg_mode}, params {chosen}) ===")
    print("metric                  model    baseline   model-baseline [90% CI, races resampled]")
    labels = {
        "finish_mae": ("simulator", "grid"),
        "finish_mae_vs_blend": ("simulator", "blend"),
        "win_brier": ("simulator", "grid-slot rate"),
        "podium_brier": ("simulator", "grid-slot rate"),
        "sequence_miss": ("precedent", "era mode"),
        "stop_count_miss": ("precedent", "era mode"),
        "first_stop_error_laps": ("precedent", "era mode"),
    }
    for metric, (model_name, base_name) in labels.items():
        if metric not in s:
            continue
        model_value, base_value, (d, lo, hi) = s[metric]
        verdict = "BEATS baseline" if hi < 0 else ("worse" if lo > 0 else "no clear difference")
        print(f"{metric:22} {model_value:7.3f}  {base_value:8.3f}   {d:+.3f} [{lo:+.3f}, {hi:+.3f}]  "
              f"{model_name} vs {base_name}: {verdict}")
    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(asdict(rows[0]).keys()))
            writer.writeheader()
            writer.writerows(asdict(r) for r in rows)
        print(f"wrote {len(rows)} driver rows to {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
