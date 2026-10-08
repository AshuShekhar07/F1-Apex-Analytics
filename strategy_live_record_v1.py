"""The live record for the predicted winning strategy: frozen BEFORE the race, then scored.

The hybrid (race_winning_strategy_v1.predict_hybrid) depends on this weekend's practice tyre
wear, so it is frozen at two stages:

  pre_practice   frozen while the practice tyre wear is not measured yet; rewritten until it is
  post_practice  frozen once the wear is measured (FP2, else FP1); rewritten until the race
  pre_start      once front-grid starting tyres are stored (start_tyres_v1): one plan per starting
                 tyre (race_winning_strategy_v1.predict_pre_start, PS1 rule); rewritten until the
                 race. Its UTC freeze time is kept: a pre_start plan frozen after the race's start
                 time (sessions.start_time, FastF1's UTC session date) is shown but NOT counted.
  No stage is touched once the race has results.

Scored per raced weekend and stage (front grid P1-P5, the race's real stints):
  stop count right / tyre order right    : front-grid cars with no stop under SC/VSC/red flag
  green stops inside their pit window    : the plan's windows, SC/VSC stops not counted
  winner's tyre order                    : the race winner ran the predicted plan

update_latest_races.py freezes the next race on every run.

    python strategy_live_record_v1.py                  # the scorecard
    python strategy_live_record_v1.py --freeze-next    # freeze the next race now
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

MODEL_VERSION = "winning_strategy_h1"
STAGES = ("pre_practice", "post_practice", "pre_start")


def ensure_table(db: Any) -> None:
    db.execute(text((Path(__file__).resolve().parent / "migration_winning_strategy_live_v1.sql").read_text()))


def freeze_stage(practice_wear: float | None) -> str:
    return "post_practice" if practice_wear is not None else "pre_practice"


def freeze(db: Any, race_id: int) -> list[str]:
    """Freeze the hybrid winning strategy for this race now; returns the stages written."""
    from live_record_v1 import race_has_results
    from race_winning_strategy_v1 import predict_for_race, strategy_to_json

    ensure_table(db)
    if race_has_results(db, race_id):
        return []
    prediction, _, _, race = predict_for_race(db, race_id, "hybrid")
    if prediction is None:
        return []
    stage = freeze_stage(race.get("practice_wear"))
    if stage == "pre_practice" and db.execute(text("""
            SELECT 1 FROM winning_strategy_live
            WHERE race_id = :r AND stage = 'post_practice' AND model_version = :v"""),
            {"r": race_id, "v": MODEL_VERSION}).scalar():
        return []      # wear was measured before; never step back to the pre-practice stage
    db.execute(text("""
        INSERT INTO winning_strategy_live (race_id, stage, model_version, prediction, confidence_level)
        VALUES (:r, :s, :v, CAST(:p AS JSONB), :c)
        ON CONFLICT (race_id, stage, model_version)
        DO UPDATE SET prediction = EXCLUDED.prediction, confidence_level = EXCLUDED.confidence_level,
                      frozen_at = now()"""),
        {"r": race_id, "s": stage, "v": MODEL_VERSION, "p": json.dumps(strategy_to_json(prediction)),
         "c": prediction.confidence})
    return [stage] + freeze_pre_start(db, race_id)


def freeze_pre_start(db: Any, race_id: int) -> list[str]:
    """Freeze the per-starting-tyre plans once front-grid starting tyres are stored."""
    from race_winning_strategy_v1 import pre_start_to_json, predict_pre_start_for_race

    result = predict_pre_start_for_race(db, race_id)
    if result is None or not any(result["plans"].values()):
        return []
    pole = result["plans"].get(result["cars"][0]["start"])
    db.execute(text("""
        INSERT INTO winning_strategy_live (race_id, stage, model_version, prediction, confidence_level, frozen_at_utc)
        VALUES (:r, 'pre_start', :v, CAST(:p AS JSONB), :c, (now() AT TIME ZONE 'UTC'))
        ON CONFLICT (race_id, stage, model_version)
        DO UPDATE SET prediction = EXCLUDED.prediction, confidence_level = EXCLUDED.confidence_level,
                      frozen_at = now(), frozen_at_utc = EXCLUDED.frozen_at_utc"""),
        {"r": race_id, "v": MODEL_VERSION, "p": json.dumps(pre_start_to_json(result)),
         "c": pole.confidence if pole else "low"})
    return ["pre_start"]


def score_plans(plans: dict, race_cars, laps) -> dict:
    """Score front-grid cars against plans keyed by starting tyre (key None: one plan for every
    car). laps: the race's SC/VSC/red-flag laps, None when unknown."""
    from race_winning_strategy_v1 import is_front, review_race, review_to_json, stop_in_laps

    out = {"green_front_cars": 0, "stop_count_right": 0, "tyre_order_right": 0, "stops_in_window": 0,
           "stops_with_window": 0, "reviews": {}}
    for start, plan in plans.items():
        if plan is None:
            continue
        cars = [c for c in race_cars if start is None or (c.sequence and c.sequence[0] == start)]
        review = review_race(plan, cars, laps)
        green = [c for c in cars if is_front(c) and laps is not None
                 and not any(lap in laps for lap in stop_in_laps(c))]
        # as in the backtest: window hits only where the SC/VSC windows are known
        hits, chances = review.window_hits if laps is not None else (0, 0)
        out["green_front_cars"] += len(green)
        out["stop_count_right"] += sum(len(c.stints) - 1 == plan.stops for c in green)
        out["tyre_order_right"] += sum(c.sequence == plan.sequence for c in green)
        out["stops_in_window"] += hits
        out["stops_with_window"] += chances
        out["reviews"][start or "all"] = review_to_json(review)
    winner = next((c for c in race_cars if c.finish == 1), None)
    plan = None if winner is None else plans.get(None, plans.get(winner.sequence[0] if winner.sequence else None))
    out["winner_ran_plan"] = (winner.sequence == plan.sequence) if winner is not None and plan is not None else None
    return out


def _race_start_utc(db: Any, race_id: int):
    return db.execute(text("SELECT start_time FROM sessions WHERE race_id = :r AND session_type = 'R'"),
                      {"r": race_id}).scalar()


def score(db: Any) -> dict:
    """Every raced weekend with a frozen winning strategy, per stage, and the totals."""
    from race_strategy_timing_v1 import load_car_strategies
    from race_winning_strategy_v1 import load_neutralised_laps, strategy_from_json

    ensure_table(db)
    frozen = db.execute(text("""
        SELECT l.race_id, l.stage, l.prediction, l.confidence_level, l.frozen_at, l.frozen_at_utc,
               r.season_year, r.round_number, t.name AS track
        FROM winning_strategy_live l
        JOIN races r ON r.id = l.race_id JOIN tracks t ON t.id = r.track_id
        WHERE l.model_version = :v
          AND EXISTS (SELECT 1 FROM sessions s JOIN race_results x ON x.session_id = s.id
                      WHERE s.race_id = l.race_id AND s.session_type = 'R')
        ORDER BY r.race_date, l.stage"""), {"v": MODEL_VERSION}).mappings().all()
    calls = []
    if frozen:
        years = [f["season_year"] for f in frozen]
        cars = load_car_strategies(db, start_year=min(years), end_year=max(years), wet_rule="tyres")
        neutralised = load_neutralised_laps(db, {f["race_id"] for f in frozen})
        for f in frozen:
            data = f["prediction"] if isinstance(f["prediction"], dict) else json.loads(f["prediction"])
            if f["stage"] == "pre_start":
                plans = {c: strategy_from_json(p) for c, p in data["plans"].items()}
                predicted = [f"{c} start: " + " -> ".join(p.sequence) for c, p in plans.items()]
            else:
                plan = strategy_from_json(data)
                plans, predicted = {None: plan}, list(plan.sequence)
            call = {"race_id": f["race_id"], "season": f["season_year"], "round": f["round_number"],
                    "track": f["track"], "stage": f["stage"], "frozen_at": f["frozen_at"],
                    "confidence": f["confidence_level"], "predicted": predicted, "scored": False}
            race_cars = [c for c in cars if c.race_id == f["race_id"]]
            start_utc = _race_start_utc(db, f["race_id"]) if f["stage"] == "pre_start" else None
            if f["stage"] == "pre_start" and start_utc is not None and f["frozen_at_utc"] is not None \
                    and f["frozen_at_utc"] >= start_utc:
                call["why_not_scored"] = f"frozen at {f['frozen_at_utc']} UTC, after the race start ({start_utc} UTC)"
            elif race_cars and not race_cars[0].wet:
                result = score_plans(plans, race_cars, neutralised.get(f["race_id"]))
                reviews = result.pop("reviews")
                call.update(result, scored=True, review=reviews.get("all", reviews))
            else:
                call["why_not_scored"] = "wet race" if race_cars else "no lap data for the race"
            calls.append(call)
    summary = {}
    for stage in STAGES:
        sel = [c for c in calls if c["stage"] == stage and c["scored"]]
        if sel:
            summary[stage] = {key: sum(c[key] for c in sel) for key in
                              ("green_front_cars", "stop_count_right", "tyre_order_right", "stops_in_window",
                               "stops_with_window")}
            summary[stage]["weekends"] = len(sel)
            summary[stage]["winner_ran_plan"] = sum(bool(c["winner_ran_plan"]) for c in sel)
    return {"model_version": MODEL_VERSION, "summary": summary, "calls": calls}


def print_scorecard(record: dict) -> None:
    print("=== LIVE RECORD: predicted winning strategies frozen before the race ===")
    if not record["calls"]:
        print("  no frozen winning strategy has been scored yet (the first one is scored after its race)")
        return
    for c in record["calls"]:
        plan = "; ".join(c["predicted"]) if c["stage"] == "pre_start" else " -> ".join(c["predicted"])
        if not c["scored"]:
            print(f"  {c['season']} R{c['round']:<2} {c['track'][:20]:20} {c['stage']:13} {plan}: "
                  f"not scored ({c['why_not_scored']})")
            continue
        print(f"  {c['season']} R{c['round']:<2} {c['track'][:20]:20} {c['stage']:13} {c['confidence']:6} {plan}: "
              f"stop count {c['stop_count_right']}/{c['green_front_cars']}, tyre order "
              f"{c['tyre_order_right']}/{c['green_front_cars']}, stops in window "
              f"{c['stops_in_window']}/{c['stops_with_window']}, winner ran it: {'yes' if c['winner_ran_plan'] else 'no'}")
    for stage, s in record["summary"].items():
        print(f"  {stage}: {s['weekends']} weekend(s); stop count {s['stop_count_right']}/{s['green_front_cars']}, "
              f"tyre order {s['tyre_order_right']}/{s['green_front_cars']}, stops in window "
              f"{s['stops_in_window']}/{s['stops_with_window']}, winner ran the plan {s['winner_ran_plan']}/{s['weekends']}")


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--freeze-next", action="store_true")
    args = parser.parse_args()
    engine = create_engine(os.environ["DATABASE_URL"])
    if args.freeze_next:
        from winner_prediction_v1 import PredictionError, next_race_id
        with engine.begin() as db:
            try:
                race_id = next_race_id(db)
            except PredictionError as exc:
                print(f"nothing to freeze: {exc}")
                return 0
            stages = freeze(db, race_id)
        print(f"race {race_id}: froze {', '.join(stages) if stages else 'nothing (race run or not predictable)'}")
    with engine.begin() as db:
        print_scorecard(score(db))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
