"""Real-data, leakage-safe dry-race backtest for strategy simulator v1.

Research-only runner. It builds a pre-race snapshot from information that was
known before the target race, calibrates simulator inputs from strictly earlier
races, and scores the frozen recommendation against the realized finish.

The realized target-driver strategy is loaded only after the pre-race snapshot
is constructed and is used strictly for post-hoc evaluation.
"""
from __future__ import annotations

import argparse
import csv
import os
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from race_strategy_calibration_v1 import calibrate_event_hazards, calibrate_tyre_degradation
from race_strategy_data_adapter_v1 import load_event_observations, load_tyre_observations
from race_strategy_pace_calibration_v3 import build_residual_observations, predict_target_pace
from race_strategy_pace_v3_db_adapter import load_pace_observations
from race_strategy_pit_calibration_v1 import calibrate_total_pit_lane_from_db, context_with_total_pit_lane_calibration
from race_strategy_simulator_v1 import (
    CompetitorProfile,
    Distribution,
    RaceContext,
    Strategy,
    StrategyStint,
    TyreAllocation,
    build_candidate_strategies,
)
from race_strategy_walkforward_v1 import InputSnapshot, RaceBacktestCase, RealizedOutcome, RaceBacktestResult, run_case

DEFAULT_ALLOCATION = TyreAllocation({"SOFT": 2, "MEDIUM": 2, "HARD": 2})


@dataclass(frozen=True)
class RaceTarget:
    race_id: int
    year: int
    track_id: int
    era: str
    total_laps: int
    driver_id: int
    team_id: int
    starting_grid: int
    actual_finish: int
    dry_confirmed: bool


def _rows(db: Any, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    result = db.execute(text(sql), params or {})
    return [dict(row) for row in result.mappings().all()]


def list_targets(db: Any, *, start_year: int, end_year: int) -> tuple[RaceTarget, ...]:
    """Return dry races with a qualifying pole sitter and a classified result."""
    rows = _rows(db, """
        SELECT
            r.id AS race_id,
            r.season_year AS year,
            r.track_id,
            r.regulation_era AS era,
            COALESCE(t.total_race_laps, MAX(l.lap_number)) AS total_laps,
            re.driver_id,
            re.team_id,
            COALESCE(rr.starting_grid_position, q.final_position) AS starting_grid,
            rr.finishing_position AS actual_finish
        FROM races r
        JOIN sessions rq ON rq.race_id = r.id AND rq.session_type = 'Q'
        JOIN qualifying_results q ON q.session_id = rq.id AND q.final_position = 1
        JOIN race_entries re ON re.id = q.race_entry_id AND re.race_id = r.id
        JOIN sessions sr ON sr.race_id = r.id AND sr.session_type = 'R'
        JOIN race_results rr ON rr.session_id = sr.id AND rr.race_entry_id = re.id
        LEFT JOIN tracks t ON t.id = r.track_id
        LEFT JOIN laps l ON l.session_id = sr.id AND l.race_entry_id = re.id
        JOIN session_weather sw ON sw.session_id = sr.id
        WHERE r.season_year BETWEEN :start_year AND :end_year
          AND r.race_date IS NOT NULL
          AND r.regulation_era IS NOT NULL
          AND sw.rainfall IS NOT NULL
          AND sw.rainfall = FALSE
          AND rr.finishing_position IS NOT NULL
        GROUP BY r.id, r.season_year, r.track_id, r.regulation_era,
                 t.total_race_laps, re.driver_id, re.team_id,
                 rr.starting_grid_position, q.final_position, rr.finishing_position
        ORDER BY r.race_date, r.id
    """, {"start_year": start_year, "end_year": end_year})
    return tuple(
        RaceTarget(
            race_id=int(r["race_id"]), year=int(r["year"]), track_id=int(r["track_id"]),
            era=str(r["era"]), total_laps=int(r["total_laps"]), driver_id=int(r["driver_id"]),
            team_id=int(r["team_id"]), starting_grid=int(r["starting_grid"]),
            actual_finish=int(r["actual_finish"]), dry_confirmed=True,
        )
        for r in rows
        # no scheduled lap count and no recorded laps: race distance unknown, not scoreable
        if r["total_laps"] is not None
    )


def load_actual_target_strategy(db: Any, target: RaceTarget) -> Strategy | None:
    """Load the target driver's realized race strategy strictly for post-hoc scoring."""
    rows = _rows(db, """
        SELECT rs.stint_number, rs.compound, rs.start_lap, rs.end_lap
        FROM race_stints rs
        JOIN races r ON r.id = rs.race_id
        JOIN race_entries re ON re.id = rs.race_entry_id
        WHERE rs.race_id = :race_id
          AND re.driver_id = :driver_id
        ORDER BY rs.stint_number
    """, {"race_id": target.race_id, "driver_id": target.driver_id})
    stints: list[StrategyStint] = []
    for row in rows:
        if row["compound"] is None or row["start_lap"] is None or row["end_lap"] is None:
            continue
        stints.append(StrategyStint(str(row["compound"]).upper(), int(row["start_lap"]), int(row["end_lap"])))
    if not stints:
        return None
    return Strategy(
        name=" → ".join(st.compound for st in stints),
        stints=tuple(stints),
        source="realized_post_race",
    )


def candidate_sequences() -> tuple[tuple[str, ...], ...]:
    """Bound the experiment to dry 1-stop/2-stop sequences using at least two compounds."""
    compounds = ("SOFT", "MEDIUM", "HARD")
    one_stop = tuple(seq for seq in product(compounds, repeat=2) if len(set(seq)) >= 2)
    two_stop = tuple(seq for seq in product(compounds, repeat=3) if len(set(seq)) >= 2)
    return tuple(seq for seq in one_stop + two_stop if seq[0] in compounds)


def _distribution(mean_value: float, std: float, lower: float | None = None, upper: float | None = None) -> Distribution:
    return Distribution(float(mean_value), float(max(0.0, std)), lower, upper)


def build_case(db: Any, target: RaceTarget, *, pace_rows: tuple, pit_start_year: int = 2023) -> RaceBacktestCase:
    """Build a target case using only information from years before target.year."""
    cutoff = target.year

    tyre_obs, _ = load_tyre_observations(db, era=target.era, start_year=2018, end_year=cutoff - 1)
    tyre_deg, _ = calibrate_tyre_degradation(tyre_obs)
    if set(("SOFT", "MEDIUM", "HARD")) - set(tyre_deg):
        raise ValueError(f"Race {target.race_id}: incomplete historical tyre degradation calibration")

    event_obs, _ = load_event_observations(db, era=target.era, start_year=2018, end_year=cutoff - 1)
    hazards, _ = calibrate_event_hazards(event_obs)

    pit_start = min(pit_start_year, cutoff - 1)
    pit = calibrate_total_pit_lane_from_db(db, start_year=pit_start, end_year=cutoff - 1, era=target.era)

    residuals = build_residual_observations(pace_rows)
    target_pace = predict_target_pace(
        residuals,
        target_track_id=target.track_id,
        target_era=target.era,
        target_driver_key=str(target.driver_id),
        target_team_key=str(target.team_id),
        as_of_year=cutoff,
        min_track_races=1,
        min_team_races=1,
        min_driver_races=1,
    )

    race_context = RaceContext(
        total_laps=target.total_laps,
        starting_grid=target.starting_grid,
        our_base_pace_seconds=_distribution(target_pace.mean_seconds, target_pace.std_seconds, 40.0, 150.0),
        tyre_degradation_per_lap=tyre_deg,
        pit_stop_seconds=Distribution(0.0, 0.0, 0.0, 0.0),
        pit_lane_loss_seconds=pit.total_pit_lane_seconds,
        sc_probability_per_lap=hazards["sc_probability_per_lap_dry"],
        vsc_probability_per_lap=hazards["vsc_probability_per_lap_dry"],
        red_flag_probability_per_lap=hazards["red_flag_probability_per_lap_dry"],
    )

    grid_rows = _rows(db, """
        SELECT re.driver_id, re.team_id,
               COALESCE(rr.starting_grid_position, q.final_position) AS grid_position
        FROM race_entries re
        JOIN races r ON r.id = re.race_id
        JOIN sessions rq ON rq.race_id = r.id AND rq.session_type = 'Q'
        JOIN qualifying_results q ON q.session_id = rq.id AND q.race_entry_id = re.id
        JOIN sessions sr ON sr.race_id = r.id AND sr.session_type = 'R'
        JOIN race_results rr ON rr.session_id = sr.id AND rr.race_entry_id = re.id
        WHERE r.id = :race_id
          AND q.final_position IS NOT NULL
          AND COALESCE(rr.starting_grid_position, q.final_position) IS NOT NULL
        ORDER BY COALESCE(rr.starting_grid_position, q.final_position), re.id
    """, {"race_id": target.race_id})

    competitors: list[CompetitorProfile] = []
    for row in grid_rows:
        driver_id = int(row["driver_id"])
        team_id = int(row["team_id"])
        grid = int(row["grid_position"])
        if driver_id == target.driver_id:
            continue
        try:
            pace = predict_target_pace(
                residuals,
                target_track_id=target.track_id,
                target_era=target.era,
                target_driver_key=str(driver_id),
                target_team_key=str(team_id),
                as_of_year=cutoff,
                min_track_races=1,
                min_team_races=1,
                min_driver_races=1,
            )
        except ValueError:
            continue
        competitors.append(
            CompetitorProfile(
                name=f"driver:{driver_id}",
                grid_position=grid,
                base_pace_seconds=_distribution(pace.mean_seconds, pace.std_seconds, 40.0, 150.0),
                pit_stop_seconds=Distribution(0.0, 0.0, 0.0, 0.0),
                strategic_aggressiveness=0.5,
            )
        )

    if len(competitors) < 5:
        raise ValueError(f"Race {target.race_id}: only {len(competitors)} competitors have historical pace coverage")

    context = context_with_total_pit_lane_calibration(race_context, pit)
    snapshot = InputSnapshot(
        available_year=cutoff,
        context=context,
        competitors=tuple(competitors),
        allocation=DEFAULT_ALLOCATION,
        parameters=__import__("race_strategy_simulator_v1", fromlist=["SimulationParameters"]).SimulationParameters(),
        source="historical_db_pre_race_dry_v1",
    )

    baseline = Strategy(
        "MEDIUM → HARD [50%]",
        (StrategyStint("MEDIUM", 1, max(2, round(target.total_laps * 0.5))),
         StrategyStint("HARD", max(3, round(target.total_laps * 0.5) + 1), target.total_laps)),
        source="fixed_baseline",
    )
    actual_strategy = load_actual_target_strategy(db, target)
    outcome = RealizedOutcome(target.year, target.actual_finish, actual_strategy=actual_strategy)
    return RaceBacktestCase(
        race_id=target.race_id,
        year=target.year,
        prediction_cutoff_year=cutoff,
        snapshot=snapshot,
        outcome=outcome,
        baseline_strategy=baseline,
    )


def build_candidates(case: RaceBacktestCase) -> tuple[Strategy, ...]:
    return tuple(
        build_candidate_strategies(
            case.snapshot.context.total_laps,
            candidate_sequences(),
            case.snapshot.allocation,
            stop_offsets=(-6, -3, 0, 3, 6),
        )
    )


def run_backtest(
    db: Any,
    *,
    start_year: int,
    end_year: int,
    simulations_per_strategy: int,
    seed: int,
    csv_path: str | None = None,
    objective: str = "p1",
) -> tuple[RaceBacktestResult, ...]:
    targets = list_targets(db, start_year=start_year, end_year=end_year)
    if not targets:
        raise ValueError("No eligible dry-race targets found")

    all_pace_rows, _ = load_pace_observations(db, start_year=2018, end_year=end_year - 1)

    results: list[RaceBacktestResult] = []
    skipped: list[tuple[int, str]] = []
    for index, target in enumerate(targets):
        try:
            eligible_pace = tuple(r for r in all_pace_rows if r.season_year < target.year)
            case = build_case(db, target, pace_rows=eligible_pace)
            result = run_case(case, build_candidates(case), simulations_per_strategy=simulations_per_strategy, seed=seed + index, objective=objective)
            results.append(result)
            actual_strategy = case.outcome.actual_strategy
            actual_seq = " → ".join(actual_strategy.sequence) if actual_strategy else "N/A"
            print(
                f"{target.year} race={target.race_id}: selected={result.selected_strategy} "
                f"P1={result.model_p1_probability:.3f} expected={result.model_expected_finish:.2f} "
                f"actual={result.actual_finish_position} realized_strategy={actual_seq} "
                f"sequence_match={result.selected_sequence_match} stop_l1={result.selected_stop_l1_error} "
                f"window_error={result.selected_lap_window_error}"
            )
        except (ValueError, KeyError) as exc:
            skipped.append((target.race_id, str(exc)))
            print(f"SKIP race={target.race_id}: {exc}")

    if not results:
        raise ValueError("No target races could be scored; inspect skip reasons")

    model_errors = [r.selected_distance_from_actual for r in results]
    baseline_errors = [r.baseline_distance_from_actual for r in results]
    better = [m < b for m, b in zip(model_errors, baseline_errors)]
    sequence_matches = [r.selected_sequence_match for r in results if r.selected_sequence_match is not None]
    stop_errors = [r.selected_stop_l1_error for r in results if r.selected_stop_l1_error is not None]
    window_errors = [r.selected_lap_window_error for r in results if r.selected_lap_window_error is not None]

    print("\n=== WALK-FORWARD SUMMARY ===")
    print(f"targets={len(targets)} scored={len(results)} skipped={len(skipped)}")
    print(f"model_MAE={sum(model_errors) / len(model_errors):.3f}")
    print(f"baseline_MAE={sum(baseline_errors) / len(baseline_errors):.3f}")
    print(f"model_better_rate={sum(better) / len(better):.3f}")
    print(f"model_P1_mean={sum(r.model_p1_probability for r in results) / len(results):.3f}")
    print(f"sequence_match_rate={(sum(sequence_matches) / len(sequence_matches)) if sequence_matches else float('nan'):.3f}")
    print(f"mean_stop_l1_error={(sum(stop_errors) / len(stop_errors)) if stop_errors else float('nan'):.3f}")
    print(f"mean_lap_window_error={(sum(window_errors) / len(window_errors)) if window_errors else float('nan'):.3f}")
    print(f"repeated_compound_selected={sum(len(set(r.selected_strategy.split(' [', 1)[0].split(' → '))) < len(r.selected_strategy.split(' [', 1)[0].split(' → ')) for r in results)}")
    if skipped:
        print("\nSkipped races:")
        for race_id, reason in skipped:
            print(f"  race={race_id}: {reason}")

    if csv_path:
        path = Path(csv_path)
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=[
                "race_id", "year", "selected_strategy", "model_expected_finish",
                "model_p1_probability", "actual_finish_position", "baseline_name",
                "baseline_expected_finish", "baseline_distance_from_actual",
                "selected_distance_from_actual", "leakage_safe", "actual_strategy",
                "selected_sequence_match", "selected_stop_l1_error", "selected_lap_window_error",
            ])
            writer.writeheader()
            for row in results:
                target = next((t for t in targets if t.race_id == row.race_id), None)
                actual_strategy = load_actual_target_strategy(db, target) if target is not None else None
                writer.writerow({
                    "race_id": row.race_id,
                    "year": row.year,
                    "selected_strategy": row.selected_strategy,
                    "model_expected_finish": row.model_expected_finish,
                    "model_p1_probability": row.model_p1_probability,
                    "actual_finish_position": row.actual_finish_position,
                    "baseline_name": row.baseline_name,
                    "baseline_expected_finish": row.baseline_expected_finish,
                    "baseline_distance_from_actual": row.baseline_distance_from_actual,
                    "selected_distance_from_actual": row.selected_distance_from_actual,
                    "leakage_safe": row.leakage_safe,
                    "actual_strategy": " → ".join(actual_strategy.sequence) if actual_strategy else "",
                    "selected_sequence_match": row.selected_sequence_match,
                    "selected_stop_l1_error": row.selected_stop_l1_error,
                    "selected_lap_window_error": row.selected_lap_window_error,
                })
    return tuple(results)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run leakage-safe dry-race strategy backtest")
    parser.add_argument("--start-year", type=int, default=2024)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument("--simulations", type=int, default=1000)
    parser.add_argument("--objective", choices=("p1", "expected_finish"), default="p1")
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--csv", type=str, default="")
    args = parser.parse_args()

    load_dotenv(dotenv_path=Path.cwd() / ".env")
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required (load your normal project .env before running)")

    engine = create_engine(database_url)
    with engine.connect() as db:
        run_backtest(
            db,
            start_year=args.start_year,
            end_year=args.end_year,
            simulations_per_strategy=args.simulations,
            seed=args.seed,
            csv_path=args.csv or None,
            objective=args.objective,
        )


if __name__ == "__main__":
    main()
