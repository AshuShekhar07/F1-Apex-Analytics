"""Store the drivers' and constructors' title chances after each round (championship_model_v1).

Each round's projection uses only the races up to that round, so backfilling a season gives
the record of what the model would have said as the season went on.

    python championship_projection_v1.py --season 2026                # latest round, if not stored yet
    python championship_projection_v1.py --season 2026 --all-rounds   # every raced round (missing ones)
    python championship_projection_v1.py --season 2026 --recompute    # redo even if stored
    python championship_projection_v1.py --backtest                   # measure title confidence per stage

Round 0 is the projection before the season: earlier seasons only, line-up from the first
race's entry list when loaded, else last season's final line-up.
"""

from __future__ import annotations

import argparse
import os
from datetime import date

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

import json
from pathlib import Path

from championship_model_v1 import (
    MODEL_VERSION,
    TITLE_CONFIDENCE_FILE,
    load_weekends,
    project_season,
    store_projection,
    title_backtest,
)


def stored_rounds(db, season: int) -> set[int]:
    if not db.execute(text("SELECT to_regclass('championship_projections') IS NOT NULL")).scalar():
        return set()
    return set(db.execute(text("""SELECT DISTINCT after_round FROM championship_projections
                                   WHERE season_year = :s AND model_version = :v"""),
                          {"s": season, "v": MODEL_VERSION}).scalars())


def first_race_entries(db, season: int) -> dict[int, int] | None:
    """driver -> team from the season's first race entry list, if loaded (>= 10 drivers)."""
    rows = db.execute(text("""
        SELECT re.driver_id, re.team_id FROM race_entries re JOIN races r ON r.id = re.race_id
        WHERE r.season_year = :s AND re.role = 'race_driver'
          AND r.round_number = (SELECT MIN(round_number) FROM races WHERE season_year = :s)"""), {"s": season}).all()
    return {int(d): int(t) for d, t in rows} if len(rows) >= 10 else None


def update(db, season: int, *, all_rounds: bool = False, recompute: bool = False) -> list[int]:
    """Project the missing rounds (latest only unless all_rounds; 0 = before the season);
    returns the rounds written."""
    weekends = load_weekends(db, start_year=2018, end_year=season)
    if not any(w.season == season for w in weekends):
        return []
    raced = sorted(w.round for w in weekends if w.season == season and w.raced)
    rounds = [0, *raced] if all_rounds else (raced[-1:] or [0])
    have = set() if recompute else stored_rounds(db, season)
    written = []
    for rnd in rounds:
        if rnd in have:
            continue
        lineup = first_race_entries(db, season) if rnd == 0 else None
        try:
            sp = project_season(weekends, season, after_round=rnd, lineup=lineup)
        except ValueError:
            if rnd == 0:          # not enough earlier seasons for a pre-season projection
                continue
            raise
        store_projection(db, sp)
        written.append(rnd)
    return written


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--season", type=int, default=date.today().year)
    parser.add_argument("--all-rounds", action="store_true")
    parser.add_argument("--recompute", action="store_true")
    parser.add_argument("--backtest", action="store_true", help="measure title confidence per stage, 2019 on")
    args = parser.parse_args()
    engine = create_engine(os.environ["DATABASE_URL"])
    if args.backtest:
        with engine.connect() as db:
            weekends = load_weekends(db, start_year=2018)
        last = max(w.season for w in weekends if w.raced)
        titles = title_backtest(weekends, 2019, last)
        path = Path(__file__).resolve().parent / TITLE_CONFIDENCE_FILE
        path.write_text(json.dumps({"source": f"championship_projection_v1 --backtest, completed seasons 2019-{last}, "
                                              "each round projected from the races before it", "titles": titles}, indent=2))
        print(f"=== TITLE CONFIDENCE (completed seasons from 2019; favourite = highest title chance) ===")
        for kind, stages in titles.items():
            for stage in ("pre_season", "early", "mid", "late"):
                rec = stages.get(stage)
                if rec:
                    print(f"  {kind:12} {stage:10} {rec['rounds']:4} projections: favourite became champion "
                          f"{rec['favourite_won']:.0%} (average favourite chance {rec['mean_favourite_p']:.0%})")
        print(f"  saved {path.name}")
        return 0
    with engine.begin() as db:
        written = update(db, args.season, all_rounds=args.all_rounds, recompute=args.recompute)
    print(f"championship projection {args.season}: " + (f"stored after round(s) {written}" if written
                                                         else "nothing new"))
    with engine.connect() as db:
        if not written:
            return 0
        rnd = written[-1]
        for kind, name_sql in (("drivers", "SELECT name FROM drivers WHERE id = :e"),
                               ("constructors", "SELECT name FROM teams WHERE id = :e")):
            print(f"  {kind} after round {rnd}:")
            for e, p, now, exp in db.execute(text("""
                SELECT entity_id, p_title, points_now, expected_points FROM championship_projections
                WHERE season_year = :s AND after_round = :r AND model_version = :v AND kind = :k
                ORDER BY p_title DESC, points_now DESC LIMIT 5"""),
                    {"s": args.season, "r": rnd, "v": MODEL_VERSION, "k": kind}):
                name = db.execute(text(name_sql), {"e": e}).scalar()
                print(f"    {name:24} title {float(p):6.1%}   points {float(now):5.0f}   expected {float(exp):5.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
