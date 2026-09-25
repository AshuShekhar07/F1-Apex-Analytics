"""Walk-forward backtest for strategy simulator v2, scored against EXTERNAL baselines.

The v1 backtest's "model better rate" compared the simulator's chosen strategy
with the simulator's own expected finish for a fixed MEDIUM->HARD plan, so it
never measured anything outside the model. Here every metric is compared with a
simple baseline that does not use the simulator:

  finish position   v2 expected finish      vs  grid position (finish = start)
  win probability   Brier score of v2 P(P1) vs  historical pole-to-win rate
  strategy          sequence / stop count / first-stop lap
                                            vs  the era's most common strategy

Targets match race_strategy_real_backtest_v1 (the pole sitter of each dry race).
Inputs use only seasons before the target (as v1), except pit loss, which uses
races strictly before the target date. Competitor strategies and our candidate
stop windows come from real race_stints history, not evenly spaced guesses.

Paired bootstrap confidence intervals are reported for v2 - baseline. v2 should
only replace v1 if its intervals are below zero out of sample.

    python race_strategy_backtest_v2.py --start-year 2024 --end-year 2025 --csv backtest_v2.csv
"""

from __future__ import annotations

import argparse
import csv
import os
import random
from collections import defaultdict
from dataclasses import asdict, dataclass
from statistics import mean, median
from typing import Any, Iterable, Sequence

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from race_strategy_simulator_v1 import DRY_COMPOUNDS, Distribution, Strategy, StrategyStint, TyreAllocation
from race_strategy_simulator_v2 import CarSpec, EventModel, TrackModel, evaluate_candidates

DEFAULT_ALLOCATION = TyreAllocation({"SOFT": 2, "MEDIUM": 2, "HARD": 2})


@dataclass(frozen=True)
class HistoricalStrategy:
    sequence: tuple[str, ...]
    stop_fractions: tuple[float, ...]  # stop lap / race laps


@dataclass(frozen=True)
class BacktestRow:
    race_id: int
    year: int
    grid: int
    actual_finish: int
    actual_win: int
    actual_sequence: str | None
    v2_selected: str
    v2_expected_finish: float
    v2_p1: float
    v2_sequence_match: int | None
    v2_stop_count_match: int | None
    v2_first_stop_error: float | None
    baseline_p1: float
    baseline_strategy: str
    baseline_sequence_match: int | None
    baseline_stop_count_match: int | None
    baseline_first_stop_error: float | None
    pit_loss_source: str
    competitors: int


# --- pure helpers --------------------------------------------------------------

def _is_legal_dry(sequence: Sequence[str]) -> bool:
    return len(sequence) >= 2 and all(c in DRY_COMPOUNDS for c in sequence) and len(set(sequence)) >= 2


def _strategy(sequence: Sequence[str], stops: Sequence[int], total_laps: int, name_prefix: str) -> Strategy | None:
    stops = [max(2, min(total_laps - 1, int(s))) for s in stops]
    if any(b <= a for a, b in zip(stops, stops[1:])):
        return None
    bounds = [0, *stops, total_laps]
    stints = tuple(StrategyStint(sequence[i], bounds[i] + 1, bounds[i + 1]) for i in range(len(sequence)))
    return Strategy(f"{name_prefix}{' → '.join(sequence)} [{','.join(map(str, stops))}]", stints, source="history")


def historical_strategy_options(
    history: Iterable[HistoricalStrategy], total_laps: int, *, top_k: int = 6
) -> list[tuple[Strategy, float]]:
    """Most frequent legal dry sequences with median stop laps, weighted by frequency."""
    by_sequence: dict[tuple[str, ...], list[tuple[float, ...]]] = defaultdict(list)
    for h in history:
        if _is_legal_dry(h.sequence) and len(h.stop_fractions) == len(h.sequence) - 1:
            by_sequence[h.sequence].append(h.stop_fractions)
    ranked = sorted(by_sequence.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:top_k]
    options = []
    for sequence, fractions in ranked:
        stops = [round(median(f[i] for f in fractions) * total_laps) for i in range(len(sequence) - 1)]
        strategy = _strategy(sequence, stops, total_laps, "")
        if strategy is not None:
            options.append((strategy, float(len(fractions))))
    return options


def candidates_from_options(
    options: Sequence[tuple[Strategy, float]], total_laps: int, offsets: Sequence[int] = (-6, -3, 0, 3, 6)
) -> list[Strategy]:
    """Our candidates: each historical sequence with its stops shifted by each offset."""
    seen, out = set(), []
    for strategy, _ in options:
        for offset in offsets:
            candidate = _strategy(strategy.sequence, [s + offset for s in strategy.stop_laps], total_laps, "")
            if candidate is None:
                continue
            key = tuple((s.compound, s.start_lap, s.end_lap) for s in candidate.stints)
            if key not in seen:
                seen.add(key)
                out.append(candidate)
    return out


def strategy_metrics(selected: Strategy, actual: Strategy | None) -> tuple[int | None, int | None, float | None]:
    """(sequence match, stop-count match, first-stop lap error) against the realised strategy."""
    if actual is None:
        return None, None, None
    first = (abs(selected.stop_laps[0] - actual.stop_laps[0])
             if selected.stop_laps and actual.stop_laps else None)
    return (int(selected.sequence == actual.sequence),
            int(len(selected.stop_laps) == len(actual.stop_laps)),
            float(first) if first is not None else None)


def brier(probability: float, outcome: int) -> float:
    return (probability - outcome) ** 2


def paired_bootstrap_ci(differences: Sequence[float], *, seed: int = 11, draws: int = 4000) -> tuple[float, float, float]:
    """Mean difference with a 90% percentile bootstrap interval (resampling races)."""
    values = [d for d in differences if d is not None]
    if not values:
        return float("nan"), float("nan"), float("nan")
    rng = random.Random(seed)
    means = sorted(mean(rng.choices(values, k=len(values))) for _ in range(draws))
    return mean(values), means[int(0.05 * draws)], means[int(0.95 * draws) - 1]


def summarise(rows: Sequence[BacktestRow]) -> dict[str, Any]:
    finish_v2 = [abs(r.v2_expected_finish - r.actual_finish) for r in rows]
    finish_base = [abs(r.grid - r.actual_finish) for r in rows]
    brier_v2 = [brier(r.v2_p1, r.actual_win) for r in rows]
    brier_base = [brier(r.baseline_p1, r.actual_win) for r in rows]

    def rate(values):
        values = [v for v in values if v is not None]
        return (mean(values), len(values)) if values else (float("nan"), 0)

    return {
        "races": len(rows),
        "finish_mae": {"v2": mean(finish_v2), "grid_baseline": mean(finish_base),
                       "v2_minus_baseline": paired_bootstrap_ci([a - b for a, b in zip(finish_v2, finish_base)])},
        "win_brier": {"v2": mean(brier_v2), "pole_rate_baseline": mean(brier_base),
                      "v2_minus_baseline": paired_bootstrap_ci([a - b for a, b in zip(brier_v2, brier_base)])},
        "sequence_match": {"v2": rate(r.v2_sequence_match for r in rows),
                           "era_mode_baseline": rate(r.baseline_sequence_match for r in rows)},
        "stop_count_match": {"v2": rate(r.v2_stop_count_match for r in rows),
                             "era_mode_baseline": rate(r.baseline_stop_count_match for r in rows)},
        "first_stop_error_laps": {"v2": rate(r.v2_first_stop_error for r in rows),
                                  "era_mode_baseline": rate(r.baseline_first_stop_error for r in rows)},
    }


# --- database assembly ---------------------------------------------------------

def load_history(db: Any, *, era: str, before_year: int) -> list[HistoricalStrategy]:
    """Realised dry-race strategies of cars that covered >= 90% of the race distance."""
    rows = db.execute(text("""
        SELECT rs.race_id, rs.race_entry_id, rs.stint_number, UPPER(rs.compound) AS compound, rs.end_lap,
               MAX(rs.end_lap) OVER (PARTITION BY rs.race_id) AS race_laps
        FROM race_stints rs
        JOIN races r ON r.id = rs.race_id
        JOIN sessions s ON s.race_id = r.id AND s.session_type = 'R'
        JOIN session_weather sw ON sw.session_id = s.id AND sw.rainfall = FALSE
        WHERE r.regulation_era = :era AND r.season_year < :year
        ORDER BY rs.race_id, rs.race_entry_id, rs.stint_number
    """), {"era": era, "year": before_year}).mappings().all()
    by_car: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for row in rows:
        by_car[(row["race_id"], row["race_entry_id"])].append(row)
    history = []
    for stints in by_car.values():
        race_laps = stints[0]["race_laps"]
        if not race_laps or stints[-1]["end_lap"] < 0.9 * race_laps:
            continue
        history.append(HistoricalStrategy(
            tuple(s["compound"] for s in stints),
            tuple(s["end_lap"] / race_laps for s in stints[:-1]),
        ))
    return history


def pole_win_rate(db: Any, *, era: str, before_year: int) -> float:
    row = db.execute(text("""
        SELECT COUNT(*) FILTER (WHERE rr.finishing_position = 1) AS wins, COUNT(*) AS n
        FROM race_results rr
        JOIN sessions s ON s.id = rr.session_id AND s.session_type = 'R'
        JOIN races r ON r.id = s.race_id
        WHERE rr.starting_grid_position = 1 AND r.regulation_era = :era AND r.season_year < :year
    """), {"era": era, "year": before_year}).mappings().one()
    return (row["wins"] + 1) / (row["n"] + 2)  # Laplace smoothing for thin eras


def run_backtest(db: Any, *, start_year: int, end_year: int, sims: int, seed: int,
                 overtake_threshold: float, objective: str) -> list[BacktestRow]:
    # imported here so the pure helpers above stay importable without v1's DB stack
    from race_strategy_calibration_v1 import calibrate_event_hazards, calibrate_tyre_degradation
    from race_strategy_data_adapter_v1 import load_event_observations, load_tyre_observations
    from race_strategy_pace_calibration_v3 import build_residual_observations, predict_target_pace
    from race_strategy_pace_v3_db_adapter import load_pace_observations
    from race_strategy_pit_calibration_v1 import calibrate_total_pit_lane_from_db
    from race_strategy_pit_loss_v2 import estimate_pit_loss, load_observations as load_pit_losses
    from race_strategy_real_backtest_v1 import list_targets, load_actual_target_strategy

    targets = list_targets(db, start_year=start_year, end_year=end_year)
    pace_rows, _ = load_pace_observations(db, start_year=2018, end_year=end_year - 1)
    pit_observations = load_pit_losses(db, start_year=2018, end_year=end_year)
    dates = dict(db.execute(text("SELECT id, race_date FROM races")).all())

    rows: list[BacktestRow] = []
    for index, target in enumerate(targets):
        try:
            year, era = target.year, target.era
            residuals = build_residual_observations(tuple(r for r in pace_rows if r.season_year < year))

            def pace(driver_id: int, team_id: int) -> Distribution:
                p = predict_target_pace(residuals, target_track_id=target.track_id, target_era=era,
                                        target_driver_key=str(driver_id), target_team_key=str(team_id),
                                        as_of_year=year, min_track_races=1, min_team_races=1, min_driver_races=1)
                return Distribution(p.mean_seconds, max(0.0, p.std_seconds), 40.0, 150.0)

            history = load_history(db, era=era, before_year=year)
            options = historical_strategy_options(history, target.total_laps)
            if not options:
                raise ValueError("no historical dry strategies for this era")

            tyre_obs, _ = load_tyre_observations(db, era=era, start_year=2018, end_year=year - 1)
            deg, _ = calibrate_tyre_degradation(tyre_obs)
            event_obs, _ = load_event_observations(db, era=era, start_year=2018, end_year=year - 1)
            hazards, _ = calibrate_event_hazards(event_obs)

            before = dates[target.race_id]
            losses, source = {}, []
            for condition in ("green", "sc", "vsc"):
                est = estimate_pit_loss(pit_observations, track_id=target.track_id, regulation_era=era,
                                        condition=condition, before_date=before)
                if est is not None:
                    losses[condition] = Distribution(est.median_seconds, est.robust_std_seconds, 5.0, 60.0)
                    source.append(f"{condition}:{est.source}")
            if "green" not in losses:
                v1 = calibrate_total_pit_lane_from_db(db, start_year=2018, end_year=year - 1, era=era)
                losses["green"] = v1.total_pit_lane_seconds
                source.append("green:v1_pit_lane_fallback")
            for condition in ("sc", "vsc"):  # no discount without evidence
                if condition not in losses:
                    losses[condition] = losses["green"]
                    source.append(f"{condition}:=green")

            track = TrackModel(total_laps=target.total_laps, tyre_deg_per_lap=deg, pit_loss=losses,
                               overtake_threshold_seconds=overtake_threshold)
            events = EventModel(sc_per_lap=hazards["sc_probability_per_lap_dry"],
                                vsc_per_lap=hazards["vsc_probability_per_lap_dry"],
                                red_per_lap=hazards["red_flag_probability_per_lap_dry"])

            grid_rows = db.execute(text("""
                SELECT re.driver_id, re.team_id, COALESCE(rr.starting_grid_position, q.final_position) AS grid
                FROM race_entries re
                JOIN sessions sq ON sq.race_id = re.race_id AND sq.session_type = 'Q'
                JOIN qualifying_results q ON q.session_id = sq.id AND q.race_entry_id = re.id
                JOIN sessions sr ON sr.race_id = re.race_id AND sr.session_type = 'R'
                JOIN race_results rr ON rr.session_id = sr.id AND rr.race_entry_id = re.id
                WHERE re.race_id = :r AND COALESCE(rr.starting_grid_position, q.final_position) > 0
            """), {"r": target.race_id}).mappings().all()
            competitors = []
            for g in grid_rows:
                if g["driver_id"] == target.driver_id:
                    continue
                try:
                    competitors.append(CarSpec(f"driver:{g['driver_id']}", int(g["grid"]),
                                               pace(g["driver_id"], g["team_id"]), tuple(options)))
                except ValueError:
                    continue
            if len(competitors) < 5:
                raise ValueError(f"only {len(competitors)} competitors with pace history")

            us = CarSpec("target", target.starting_grid, pace(target.driver_id, target.team_id), ())
            results = evaluate_candidates(us, candidates_from_options(options, target.total_laps), competitors,
                                          track, events, sims=sims, seed=seed + index,
                                          allocation=DEFAULT_ALLOCATION, objective=objective)
            best = results[0]
            actual = load_actual_target_strategy(db, target)  # post-hoc only
            baseline_strategy = options[0][0]
            v2m = strategy_metrics(best.strategy, actual)
            basem = strategy_metrics(baseline_strategy, actual)
            rows.append(BacktestRow(
                target.race_id, year, target.starting_grid, target.actual_finish, int(target.actual_finish == 1),
                " → ".join(actual.sequence) if actual else None,
                best.strategy.name, round(best.expected_finish, 3), round(best.win_probability, 4), *v2m,
                round(pole_win_rate(db, era=era, before_year=year), 4), baseline_strategy.name, *basem,
                ";".join(source), len(competitors),
            ))
            print(f"{year} race={target.race_id}: v2={best.strategy.name} P1={best.win_probability:.3f} "
                  f"E={best.expected_finish:.2f} actual={target.actual_finish} "
                  f"({rows[-1].actual_sequence}) pit_loss={';'.join(source)}", flush=True)
        except (ValueError, KeyError) as exc:
            print(f"SKIP race={target.race_id}: {exc}", flush=True)
    return rows


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start-year", type=int, default=2024)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument("--sims", type=int, default=800)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--overtake-threshold", type=float, default=0.8,
                        help="seconds/lap pace advantage needed to pass (UNVALIDATED default)")
    parser.add_argument("--objective", choices=("p1", "expected_finish"), default="p1")
    parser.add_argument("--csv", default="")
    args = parser.parse_args()

    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as db:
        rows = run_backtest(db, start_year=args.start_year, end_year=args.end_year, sims=args.sims,
                            seed=args.seed, overtake_threshold=args.overtake_threshold, objective=args.objective)
    if not rows:
        print("No races scored.")
        return 1
    summary = summarise(rows)
    print(f"\n=== BACKTEST V2 ({summary['races']} races, overtake threshold {args.overtake_threshold}s) ===")
    for metric in ("finish_mae", "win_brier"):
        m = summary[metric]
        base_key = next(k for k in m if k.endswith("baseline"))
        diff, low, high = m["v2_minus_baseline"]
        print(f"{metric:22} v2={m['v2']:.3f}  {base_key}={m[base_key]:.3f}  "
              f"diff={diff:+.3f} 90% CI [{low:+.3f}, {high:+.3f}]")
    for metric in ("sequence_match", "stop_count_match", "first_stop_error_laps"):
        m = summary[metric]
        print(f"{metric:22} v2={m['v2'][0]:.3f}  era_mode_baseline={m['era_mode_baseline'][0]:.3f}  (n={m['v2'][1]})")
    print("v2 beats a baseline only where the CI lies entirely below zero (lower is better for MAE/Brier/error).")
    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(asdict(rows[0]).keys()))
            writer.writeheader()
            writer.writerows(asdict(r) for r in rows)
        print(f"wrote {len(rows)} races to {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
