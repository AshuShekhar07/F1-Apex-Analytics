"""Read-only check of what production says: predicted race winners vs the real ones, and the
title chances round by round. Nothing is computed or written.

    python production_check_v1.py --season 2026 --titles 2025
"""

from __future__ import annotations

import argparse
import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from championship_model_v1 import MODEL_VERSION as TITLE_VERSION
from race_forecast_v1 import MODEL_VERSION as FORECAST_VERSION


def race_winners(db, season: int) -> None:
    rows = db.execute(text("""
        SELECT r.round_number, t.name AS track,
               fav.name AS predicted, fav.p_win, fav.grid AS predicted_grid,
               win.name AS winner, win_p.p_win AS winner_p_win, live.name AS live_predicted
        FROM races r
        JOIN tracks t ON t.id = r.track_id
        JOIN race_forecasts f ON f.race_id = r.id AND f.model_version = :v
        LEFT JOIN LATERAL (
            SELECT d.name, fd.p_win, fd.grid FROM race_forecast_drivers fd JOIN drivers d ON d.id = fd.driver_id
            WHERE fd.forecast_id = f.id ORDER BY fd.p_win DESC, fd.grid LIMIT 1) fav ON TRUE
        LEFT JOIN LATERAL (
            SELECT d.id, d.name FROM sessions s
            JOIN race_results rr ON rr.session_id = s.id AND rr.finishing_position = 1
            JOIN race_entries re ON re.id = rr.race_entry_id JOIN drivers d ON d.id = re.driver_id
            WHERE s.race_id = r.id AND s.session_type = 'R' LIMIT 1) win ON TRUE
        LEFT JOIN race_forecast_drivers win_p ON win_p.forecast_id = f.id AND win_p.driver_id = win.id
        LEFT JOIN LATERAL (
            SELECT d.name FROM race_forecast_live l JOIN drivers d ON d.id = l.driver_id
            WHERE l.race_id = r.id AND l.model_version = :v ORDER BY l.p_win DESC, l.grid LIMIT 1) live ON TRUE
        WHERE r.season_year = :s ORDER BY r.round_number"""), {"s": season, "v": FORECAST_VERSION}).mappings().all()
    print(f"=== {season} RACE WINNERS: favourite (highest win chance) vs the real winner ===")
    print("  (recomputed with the current model from races before each one; 'live' = what was "
          "frozen before the race with the model of that time)")
    hits = done = 0
    for r in rows:
        if r["winner"]:
            done += 1
            hits += r["predicted"] == r["winner"]
        mark = "" if not r["winner"] else ("  RIGHT" if r["predicted"] == r["winner"] else "")
        winner = (f"winner {r['winner']} (we gave {float(r['winner_p_win'] or 0):.0%})" if r["winner"]
                  else "not raced yet")
        print(f"  R{r['round_number']:<2} {r['track'][:22]:22} favourite {r['predicted']:20} "
              f"{float(r['p_win']):5.0%} from P{r['predicted_grid']:<2}  {winner}{mark}"
              + (f"  [live: {r['live_predicted']}]" if r["live_predicted"] else ""))
    if done:
        print(f"  favourite won {hits}/{done} races ({hits / done:.0%}); backtest 2019-2025: 57%")


def title_history(db, season: int) -> None:
    print(f"\n=== {season} TITLE CHANCES AFTER EACH ROUND (top 4 at the end) ===")
    for kind, name_join in (("drivers", "JOIN drivers n ON n.id = cp.entity_id"),
                            ("constructors", "JOIN teams n ON n.id = cp.entity_id")):
        rows = db.execute(text(f"""
            SELECT cp.after_round, cp.entity_id, n.name, cp.p_title, cp.points_now FROM championship_projections cp
            {name_join}
            WHERE cp.season_year = :s AND cp.model_version = :v AND cp.kind = :k
            ORDER BY cp.after_round"""), {"s": season, "v": TITLE_VERSION, "k": kind}).mappings().all()
        if not rows:
            print(f"  {kind}: nothing stored (run championship_projection_v1.py --season {season} --all-rounds)")
            continue
        last = max(r["after_round"] for r in rows)
        top = [r["entity_id"] for r in sorted((r for r in rows if r["after_round"] == last),
                                              key=lambda r: -float(r["points_now"]))][:4]
        names = {r["entity_id"]: r["name"] for r in rows}
        chance = {(r["after_round"], r["entity_id"]): float(r["p_title"]) for r in rows}
        print(f"  {kind}:  round " + " ".join(f"{names[e][:14]:>15}" for e in top))
        for rnd in sorted({r["after_round"] for r in rows}):
            print(f"  {'':{len(kind)}}   {rnd:5} " + " ".join(f"{chance.get((rnd, e), 0):15.1%}" for e in top))


def team_id_changes(db, seasons) -> None:
    """Drivers whose team id changes within a season for only a few races -- usually a
    duplicate team row, which splits a team's points in the championship model."""
    print("\n=== TEAM IDS USED FOR ONLY PART OF A SEASON (possible duplicate team rows) ===")
    rows = db.execute(text("""
        WITH entries AS (
            SELECT r.season_year, r.round_number, re.driver_id, re.team_id
            FROM race_entries re JOIN races r ON r.id = re.race_id
            WHERE re.role = 'race_driver' AND r.season_year = ANY(:s)),
        usage AS (
            SELECT season_year, driver_id, team_id, COUNT(*) AS races, array_agg(round_number ORDER BY round_number) AS rounds
            FROM entries GROUP BY season_year, driver_id, team_id),
        main AS (
            SELECT DISTINCT ON (season_year, driver_id) season_year, driver_id, team_id AS main_team
            FROM usage ORDER BY season_year, driver_id, races DESC)
        SELECT u.season_year, d.name AS driver, t.id AS team_id, t.name AS team, u.rounds,
               m.main_team, mt.name AS main_team_name
        FROM usage u JOIN main m USING (season_year, driver_id)
        JOIN drivers d ON d.id = u.driver_id JOIN teams t ON t.id = u.team_id JOIN teams mt ON mt.id = m.main_team
        WHERE u.team_id <> m.main_team ORDER BY u.season_year, u.rounds"""), {"s": list(seasons)}).mappings().all()
    if not rows:
        print("  none")
    for r in rows:
        print(f"  {r['season_year']} {r['driver']:22} team id {r['team_id']} '{r['team']}' in rounds {r['rounds']}; "
              f"rest of the season team id {r['main_team']} '{r['main_team_name']}'")
    teams = db.execute(text("""
        SELECT t.name, array_agg(DISTINCT t.id ORDER BY t.id) AS ids FROM teams t
        JOIN race_entries re ON re.team_id = t.id JOIN races r ON r.id = re.race_id
        WHERE r.season_year = ANY(:s) GROUP BY t.name HAVING COUNT(DISTINCT t.id) > 1"""), {"s": list(seasons)}).all()
    for name, ids in teams:
        print(f"  team name '{name}' has several ids: {ids}")


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--season", type=int, default=2026, help="race winners of this season")
    parser.add_argument("--titles", type=int, nargs="*", default=[2025, 2026], help="title histories to show")
    args = parser.parse_args()
    with create_engine(os.environ["DATABASE_URL"]).connect() as db:
        race_winners(db, args.season)
        for season in args.titles:
            title_history(db, season)
        team_id_changes(db, sorted({args.season, *args.titles}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
