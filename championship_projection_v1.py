"""Store the drivers' and constructors' title chances after each round (championship_model_v1).

Each round's projection uses only the races up to that round, so backfilling a season gives
the record of what the model would have said as the season went on.

    python championship_projection_v1.py --season 2026                # latest round, if not stored yet
    python championship_projection_v1.py --season 2026 --all-rounds   # every raced round (missing ones)
    python championship_projection_v1.py --season 2026 --recompute    # redo even if stored
"""

from __future__ import annotations

import argparse
import os
from datetime import date

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from championship_model_v1 import MODEL_VERSION, load_weekends, project_season, store_projection


def stored_rounds(db, season: int) -> set[int]:
    if not db.execute(text("SELECT to_regclass('championship_projections') IS NOT NULL")).scalar():
        return set()
    return set(db.execute(text("""SELECT DISTINCT after_round FROM championship_projections
                                   WHERE season_year = :s AND model_version = :v"""),
                          {"s": season, "v": MODEL_VERSION}).scalars())


def update(db, season: int, *, all_rounds: bool = False, recompute: bool = False) -> list[int]:
    """Project the missing rounds (latest only unless all_rounds); returns the rounds written."""
    weekends = load_weekends(db, start_year=2018, end_year=season)
    raced = sorted(w.round for w in weekends if w.season == season and w.raced)
    if not raced:
        return []
    rounds = raced if all_rounds else raced[-1:]
    have = set() if recompute else stored_rounds(db, season)
    written = []
    for rnd in rounds:
        if rnd in have:
            continue
        store_projection(db, project_season(weekends, season, after_round=rnd))
        written.append(rnd)
    return written


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--season", type=int, default=date.today().year)
    parser.add_argument("--all-rounds", action="store_true")
    parser.add_argument("--recompute", action="store_true")
    args = parser.parse_args()
    engine = create_engine(os.environ["DATABASE_URL"])
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
