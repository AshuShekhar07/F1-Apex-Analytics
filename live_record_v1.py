"""The live record: race-winner predictions frozen BEFORE the race, at both stages, then scored.

A replay (re-predicting past races with today's model) can be tuned after the fact; a frozen
prediction cannot. This module keeps the frozen ones:

  freeze   pre_qualifying   frozen while the race's qualifying is not loaded yet
           post_qualifying  frozen once the grid is known (qualifying / provisional grid)
           Each stage is overwritten while it is still open (newer data before the session),
           and never touched once the race has results.
  score    for raced weekends with frozen predictions: did the favourite win, was the
           winner in our top 3, what chance did we give him, and how the confidence levels held

update_latest_races.py freezes the next race on every run, so the scheduled pipeline
(run_pipeline.py) builds the record without anyone remembering to.

    python live_record_v1.py                  # the scorecard
    python live_record_v1.py --freeze-next    # freeze the next race now
"""

from __future__ import annotations

import argparse
import os
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

MODEL_VERSION = "winner_v1"
STAGES = ("pre_qualifying", "post_qualifying")


def ensure_table(db: Any) -> None:
    db.execute(text((Path(__file__).resolve().parent / "migration_winner_prediction_live_v1.sql").read_text()))


def race_has_results(db: Any, race_id: int) -> bool:
    return bool(db.execute(text("""
        SELECT 1 FROM sessions s JOIN race_results rr ON rr.session_id = s.id
        WHERE s.race_id = :r AND s.session_type = 'R' LIMIT 1"""), {"r": race_id}).scalar())


def qualifying_loaded(db: Any, race_id: int) -> bool:
    return bool(db.execute(text("""
        SELECT 1 FROM sessions s JOIN qualifying_results q ON q.session_id = s.id
        WHERE s.race_id = :r AND s.session_type = 'Q' LIMIT 1"""), {"r": race_id}).scalar())


def frozen_stages(prediction: dict, *, qualifying_done: bool) -> list[str]:
    """Stages that may be (re)frozen now: before qualifying only the pre-qualifying stage;
    after it only the post-qualifying stage (the pre-qualifying one is final by then)."""
    if qualifying_done:
        return ["post_qualifying"] if "post_qualifying" in prediction["stages"] else []
    return ["pre_qualifying"] if "pre_qualifying" in prediction["stages"] else []


def freeze(db: Any, race_id: int) -> list[str]:
    """Freeze what can be frozen for this race now; returns the stages written."""
    from winner_prediction_v1 import PredictionError, predict

    ensure_table(db)
    if race_has_results(db, race_id):
        return []
    try:
        prediction = predict(db, race_id, names=False)
    except PredictionError:
        return []
    written = []
    for stage in frozen_stages(prediction, qualifying_done=qualifying_loaded(db, race_id)):
        block = prediction["stages"][stage]
        db.execute(text("""DELETE FROM winner_prediction_live
                           WHERE race_id = :r AND stage = :s AND model_version = :v"""),
                   {"r": race_id, "s": stage, "v": MODEL_VERSION})
        for rank, d in enumerate(block["drivers"], start=1):
            db.execute(text("""
                INSERT INTO winner_prediction_live (race_id, stage, model_version, driver_id, rank, grid,
                                                    p_win, p_podium, confidence_level, confidence_basis)
                VALUES (:r, :s, :v, :d, :rank, :g, :w, :p, :lvl, :basis)"""),
                {"r": race_id, "s": stage, "v": MODEL_VERSION, "d": d["driver_id"], "rank": rank, "g": d["grid"],
                 "w": d["p_win"], "p": d["p_podium"], "lvl": block["confidence"]["level"],
                 "basis": block["confidence"]["basis"]})
        written.append(stage)
    return written


def score(db: Any) -> dict:
    """Every raced weekend with a frozen prediction, per stage, and the totals."""
    ensure_table(db)
    rows = db.execute(text("""
        SELECT l.race_id, l.stage, l.driver_id, l.rank, l.p_win, l.confidence_level, l.frozen_at,
               r.season_year, r.round_number, t.name AS track, d.name AS driver_name,
               rr.finishing_position
        FROM winner_prediction_live l
        JOIN races r ON r.id = l.race_id
        JOIN tracks t ON t.id = r.track_id
        JOIN drivers d ON d.id = l.driver_id
        JOIN sessions s ON s.race_id = l.race_id AND s.session_type = 'R'
        LEFT JOIN race_entries re ON re.race_id = l.race_id AND re.driver_id = l.driver_id
        LEFT JOIN race_results rr ON rr.session_id = s.id AND rr.race_entry_id = re.id
        WHERE l.model_version = :v
          AND EXISTS (SELECT 1 FROM race_results x WHERE x.session_id = s.id)
        ORDER BY r.race_date, l.stage, l.rank"""), {"v": MODEL_VERSION}).mappings().all()
    by_call = defaultdict(list)
    for r in rows:
        by_call[(r["race_id"], r["stage"])].append(r)
    calls = []
    for (race_id, stage), drivers in by_call.items():
        fav = min(drivers, key=lambda d: d["rank"])
        winner = next((d for d in drivers if d["finishing_position"] == 1), None)
        calls.append({
            "race_id": race_id, "season": fav["season_year"], "round": fav["round_number"], "track": fav["track"],
            "stage": stage, "frozen_at": fav["frozen_at"], "confidence": fav["confidence_level"],
            "favourite": fav["driver_name"], "favourite_p_win": float(fav["p_win"]),
            "winner": winner["driver_name"] if winner else None,
            "winner_rank": winner["rank"] if winner else None,
            "winner_p_win": float(winner["p_win"]) if winner else 0.0,
            "favourite_won": bool(winner and winner["driver_id"] == fav["driver_id"]),
            "winner_in_top3": bool(winner and winner["rank"] <= 3),
        })
    summary = {}
    for stage in STAGES:
        sel = [c for c in calls if c["stage"] == stage]
        if not sel:
            continue
        levels = {}
        for level in ("high", "medium", "low"):
            lv = [c for c in sel if c["confidence"] == level]
            if lv:
                levels[level] = {"calls": len(lv), "favourite_won": sum(c["favourite_won"] for c in lv)}
        summary[stage] = {"calls": len(sel), "favourite_won": sum(c["favourite_won"] for c in sel),
                          "winner_in_top3": sum(c["winner_in_top3"] for c in sel),
                          "mean_favourite_p_win": round(mean(c["favourite_p_win"] for c in sel), 4),
                          "by_confidence": levels}
    return {"model_version": MODEL_VERSION, "summary": summary, "calls": calls}


def print_scorecard(record: dict) -> None:
    print("=== LIVE RECORD: race-winner predictions frozen before the race ===")
    if not record["calls"]:
        print("  no frozen prediction has been scored yet (the first one is scored after its race)")
        return
    for c in record["calls"]:
        verdict = "RIGHT" if c["favourite_won"] else f"winner {c['winner'] or '?'} was our #{c['winner_rank'] or '-'}"
        print(f"  {c['season']} R{c['round']:<2} {c['track'][:20]:20} {c['stage']:16} {c['confidence']:6} "
              f"{c['favourite']:22} {c['favourite_p_win']:5.1%}  {verdict}")
    for stage, s in record["summary"].items():
        levels = ", ".join(f"{lvl} {v['favourite_won']}/{v['calls']}" for lvl, v in s["by_confidence"].items())
        print(f"  {stage}: favourite won {s['favourite_won']}/{s['calls']}, winner in our top 3 "
              f"{s['winner_in_top3']}/{s['calls']} (by confidence: {levels})")


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
