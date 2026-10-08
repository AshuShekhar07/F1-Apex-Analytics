"""Load the latest race weekends end to end, check them, then forecast them.

One command instead of a dozen scripts in the right order:

  1. load     every loader below, in dependency order. Each one only fills what is
              missing, so re-running is safe and cheap (FastF1 data is cached).
  2. era      new races get the regulation era the rest of their season already has
  3. check    data_quality_v1: a race with a FAIL gets no forecast (and is listed)
  4. forecast every weekend that has qualifying, passes the checks and has no stored
              forecast yet (race_forecast_v1; uses only races before its date); a pre-race
              forecast made from the qualifying order is redone once the provisional grid
              (fetch_starting_grid_v1, penalties applied) is in
  5. titles   drivers' and constructors' title chances after the latest round, if not stored
              yet (championship_projection_v1)
  6. live     the next race's winner prediction frozen for the live record: before
              qualifying, then again once the grid is known (live_record_v1); and its
              predicted winning strategy, before and after practice (strategy_live_record_v1)
  7. score    optional (--score): the prediction scoreboard vs the saved baseline

    python update_latest_races.py                     # current season
    python update_latest_races.py --season 2026
    python update_latest_races.py --skip-load         # checks + forecasts only
    python update_latest_races.py --score scoreboard_baseline_2018.json
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import date

from dotenv import load_dotenv
from sqlalchemy import create_engine, text


def loaders(season: int) -> list[tuple[str, list[str]]]:
    """(description, command) in dependency order. Results and laps first; everything
    else hangs off the sessions and entries that backfill_history creates."""
    y = str(season)
    return [
        ("sessions, entries, laps, qualifying, results", ["backfill_history.py"]),
        ("results missing for drivers who took part", ["repair_missing_race_results_v1.py", "--season", y, "--apply"]),
        ("laps missing for drivers with results", ["repair_session_laps_v1.py", "--season", y, "--apply"]),
        ("weekend format (sprint or not)", ["populate_weekend_format.py"]),
        ("sprint results", ["backfill_sprint_results.py"]),
        ("official starting grid", ["backfill_grid_position.py"]),
        ("provisional starting grid for the next race (OpenF1)", ["fetch_starting_grid_v1.py", "--season", y]),
        ("winner race time", ["backfill_winner_time.py"]),
        ("gaps to the winner", ["backfill_gap_to_winner.py"]),
        ("lap-by-lap positions", ["backfill_race_positions.py"]),
        ("race weather", ["backfill_weather.py"]),
        ("qualifying weather", ["backfill_quali_weather.py"]),
        ("FP2 weather", ["backfill_fp2_weather.py"]),
        ("rain onset lap", ["backfill_rain_onset.py"]),
        ("Safety Car periods", ["backfill_safety_car.py"]),
        ("lap metadata (tyre life, pit in/out, track status)",
         ["backfill_fastf1_enrichment_v1.py", "--mode", "lap-metadata", "--start-year", y, "--end-year", y]),
        ("pit stops", ["backfill_race_strategy_pit_stops_v1.py", "--start-year", y, "--end-year", y]),
        ("tyre stints (derived, rebuilt)", ["extract_race_stints.py"]),
    ]


def run_loaders(season: int, *, from_step: int = 1) -> bool:
    steps = loaders(season)
    for i, (label, cmd) in enumerate(steps, start=1):
        if i < from_step:
            continue
        print(f"\n[{i}/{len(steps)}] {label}: python {' '.join(cmd)}", flush=True)
        started = time.time()
        # -u: unbuffered, so a log file shows each loader's progress as it happens
        code = subprocess.call([sys.executable, "-u", *cmd])
        print(f"[{i}/{len(steps)}] {'done' if code == 0 else f'FAILED (exit {code})'} in {time.time() - started:.0f}s",
              flush=True)
        if code != 0:
            print(f"Stopped. Fix the error above, then resume with --from-step {i}.")
            return False
    return True


def fill_regulation_era(db, season: int) -> list[int]:
    """Races of `season` without an era get the single era their season already uses."""
    eras = db.execute(text("""
        SELECT DISTINCT regulation_era FROM races
        WHERE season_year = :s AND regulation_era IS NOT NULL
    """), {"s": season}).scalars().all()
    if len(eras) != 1:
        return []          # none known, or ambiguous: leave it for a human (the check will FAIL)
    return db.execute(text("""
        UPDATE races SET regulation_era = :e
        WHERE season_year = :s AND regulation_era IS NULL RETURNING id
    """), {"e": eras[0], "s": season}).scalars().all()


def fetch_start_tyres_if_due(db, race_id: int) -> None:
    """Before the race: try OpenF1 for the starting tyres (stored only with enough coverage;
    hand-entered tyres are never overwritten). OpenF1 may publish them only after the start --
    then the pre_start stage it allows is flagged late in the live record and not counted."""
    from live_record_v1 import qualifying_loaded, race_has_results
    from start_tyres_v1 import MIN_COVERAGE, fetch_start_tyres, load_start_tyres, race_entries_by_number, store

    if race_has_results(db, race_id) or not qualifying_loaded(db, race_id):
        return
    if load_start_tyres(db, race_id):
        return
    race_date = db.execute(text("SELECT race_date FROM races WHERE id = :r"), {"r": race_id}).scalar()
    entries = race_entries_by_number(db, race_id)
    try:
        tyres, problems = fetch_start_tyres(race_date, entries)
    except Exception as exc:          # network trouble must not stop the pipeline
        print(f"  starting tyres: OpenF1 not reachable ({exc})")
        return
    if entries and len(tyres) >= MIN_COVERAGE * len(entries):
        store(db, race_id, tyres, "openf1")
        print(f"  starting tyres: stored {len(tyres)} from OpenF1")
    else:
        print(f"  starting tyres: not available yet ({'; '.join(problems) or f'{len(tyres)} cars'})")


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--season", type=int, default=date.today().year)
    parser.add_argument("--skip-load", action="store_true", help="only era fill, checks and forecasts")
    parser.add_argument("--from-step", type=int, default=1, help="resume the loaders at this step")
    parser.add_argument("--no-identity", action="store_true", help="skip the FastF1 driver-identity check")
    parser.add_argument("--score", metavar="BASELINE_JSON", help="run the scoreboard gate afterwards")
    args = parser.parse_args()

    if not args.skip_load and not run_loaders(args.season, from_step=args.from_step):
        return 1

    from data_quality_v1 import check_season, print_report
    from race_forecast_v1 import MODEL_VERSION, ForecastError, compute_forecast, store_forecast

    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.begin() as db:
        filled = fill_regulation_era(db, args.season)
    if filled:
        print(f"\nregulation era set for races {filled}")

    print(f"\n=== DATA QUALITY {args.season} ===")
    with engine.connect() as db:
        checks = check_season(db, args.season, identity=not args.no_identity)
        forecast_ids = set(db.execute(text("SELECT race_id FROM race_forecasts WHERE model_version = :v"),
                                      {"v": MODEL_VERSION}).scalars())
        # a pre-race forecast made from the qualifying order is redone once the provisional
        # grid (penalties) is in -- only while the race has not been run
        upgrade = set() if not db.execute(text("SELECT to_regclass('race_provisional_grid') IS NOT NULL")).scalar() \
            else set(db.execute(text("""
                SELECT f.race_id FROM race_forecasts f
                WHERE f.model_version = :v AND f.grid_source = 'qualifying'
                  AND EXISTS (SELECT 1 FROM race_provisional_grid pg WHERE pg.race_id = f.race_id)
                  AND NOT EXISTS (SELECT 1 FROM sessions s JOIN race_results rr ON rr.session_id = s.id
                                  WHERE s.race_id = f.race_id AND s.session_type = 'R')
            """), {"v": MODEL_VERSION}).scalars())
        forecast_ids -= upgrade
    print_report(checks)

    print(f"\n=== FORECASTS ===")
    made, blocked = [], []
    for c in checks:
        if c.stage == "scheduled" or c.race_id in forecast_ids:
            continue
        if c.fails:
            blocked.append(c)
            print(f"  R{c.round_number} {c.track}: BLOCKED by data checks ({'; '.join(c.fails)})")
            continue
        with engine.begin() as db:
            try:
                forecast = compute_forecast(db, c.race_id)
                store_forecast(db, forecast)
            except ForecastError as exc:
                print(f"  R{c.round_number} {c.track}: not forecastable yet ({exc})")
                continue
        made.append(c)
        print(f"  R{c.round_number} {c.track}: forecast stored (race id {c.race_id}); "
              f"view: python show_forecast.py --race {c.race_id}")
    if not made and not blocked:
        print("  nothing new to forecast")

    print(f"\n=== CHAMPIONSHIP {args.season} ===")
    from championship_projection_v1 import update as update_championship
    try:
        with engine.begin() as db:
            rounds = update_championship(db, args.season)
        print(f"  title chances stored after round(s) {rounds}" if rounds else "  nothing new to project")
    except ValueError as exc:
        print(f"  not projectable yet ({exc})")

    print(f"\n=== LIVE RECORD ===")
    from live_record_v1 import freeze
    from winner_prediction_v1 import PredictionError, next_race_id
    with engine.begin() as db:
        try:
            race_id = next_race_id(db)
            stages = freeze(db, race_id)
            print(f"  next race (id {race_id}): froze {', '.join(stages)}" if stages
                  else f"  next race (id {race_id}): nothing to freeze yet")
        except PredictionError as exc:
            print(f"  nothing to freeze ({exc})")
    from strategy_live_record_v1 import freeze as freeze_strategy
    with engine.begin() as db:
        try:
            race_id = next_race_id(db)
            fetch_start_tyres_if_due(db, race_id)
            stages = freeze_strategy(db, race_id)
            print(f"  next race (id {race_id}) winning strategy: froze {', '.join(stages)}" if stages
                  else f"  next race (id {race_id}) winning strategy: nothing to freeze yet")
        except PredictionError as exc:
            print(f"  winning strategy: nothing to freeze ({exc})")

    if args.score:
        print(f"\n=== SCOREBOARD vs {args.score} ===", flush=True)
        code = subprocess.call([sys.executable, "prediction_scoreboard_v1.py", "--stored", "--compare", args.score])
        if code != 0:
            return code
    return 1 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
