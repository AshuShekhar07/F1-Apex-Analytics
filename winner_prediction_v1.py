"""Race-winner prediction at two stages, with a confidence level that is measured, not set.

  pre_qualifying   grid unknown: strength model only -- car (per season) + driver skill,
                   learned from earlier finishing orders (championship_model_v1)
  post_qualifying  grid known (provisional grid > official grid > qualifying order): strength
                   + grid effect -- the same P(win) as the stored race forecast

Predicted winner = the driver with the highest P(win). Confidence answers "how often did the
favourite actually win in earlier races at this stage, when they had a similar chance?" --
from a walk-forward backtest (every race predicted only from the races before it), stored in
winner_confidence_v1.json by --backtest:

  high    the favourite won >= 55% of such races
  medium  35-55%
  low     < 35%

    python winner_prediction_v1.py --next                 # the next race, both stages if the grid is known
    python winner_prediction_v1.py --race-id 189
    python winner_prediction_v1.py --backtest             # (re)measure the confidence table, 2019 onwards
"""

from __future__ import annotations

import argparse
import json
import math
import os
from datetime import date, timedelta
from pathlib import Path
from statistics import mean
from typing import Any, Sequence

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

import championship_model_v1 as cm

CONFIDENCE_FILE = Path(__file__).resolve().parent / "winner_confidence_v1.json"
STAGES = ("pre_qualifying", "post_qualifying")
BINS = ((0.0, 0.25), (0.25, 0.35), (0.35, 0.45), (0.45, 0.6), (0.6, 1.01))
MIN_BIN_RACES = 15          # fewer races in a bin: use the stage's overall record instead
HIGH, MEDIUM = 0.55, 0.35
BACKTEST_SIMS = 4000
# championship_backtest_v1, 152 races 2019-2025, until --backtest has been run
FALLBACK = {"pre_qualifying": {"races": 152, "favourite_won": 0.487, "bins": []},
            "post_qualifying": {"races": 152, "favourite_won": 0.572, "bins": []}}


class PredictionError(Exception):
    pass


def level(rate: float) -> str:
    return "high" if rate >= HIGH else "medium" if rate >= MEDIUM else "low"


def load_confidence() -> dict:
    try:
        return json.loads(CONFIDENCE_FILE.read_text())["stages"]
    except (OSError, ValueError, KeyError):
        return FALLBACK


def confidence(stage: str, favourite_p: float, table: dict | None = None) -> dict:
    """Measured record of favourites at this stage with a similar chance."""
    rec = (table or load_confidence())[stage]
    match = next((b for b in rec.get("bins", []) if b["lo"] <= favourite_p < b["hi"]), None)
    if match and match["races"] >= MIN_BIN_RACES:
        rate, n, basis = match["favourite_won"], match["races"], (
            f"in {match['races']} earlier races where the favourite had {match['lo']:.0%}-{min(match['hi'], 1):.0%} "
            f"at this stage, the favourite won {match['favourite_won']:.0%}")
    else:
        rate, n, basis = rec["favourite_won"], rec["races"], (
            f"in {rec['races']} earlier races at this stage, the favourite won {rec['favourite_won']:.0%}")
    return {"level": level(rate), "favourite_won_historically": round(rate, 3), "races": n, "basis": basis,
            "stage_record": round(rec["favourite_won"], 3)}


# --- data --------------------------------------------------------------------------

def race_row(db: Any, race_id: int) -> dict:
    row = db.execute(text("""
        SELECT r.id, r.season_year, r.round_number, r.race_date, r.regulation_era, t.name AS track
        FROM races r JOIN tracks t ON t.id = r.track_id WHERE r.id = :r"""), {"r": race_id}).mappings().first()
    if row is None:
        raise PredictionError("race not found")
    return dict(row)


def next_race_id(db: Any, today: date | None = None) -> int:
    """The first race on or after today (or within the last 2 days) without race results."""
    today = today or date.today()
    rid = db.execute(text("""
        SELECT r.id FROM races r
        WHERE r.race_date >= :d
          AND NOT EXISTS (SELECT 1 FROM sessions s JOIN race_results rr ON rr.session_id = s.id
                          WHERE s.race_id = r.id AND s.session_type = 'R')
        ORDER BY r.race_date, r.round_number LIMIT 1"""), {"d": today - timedelta(days=2)}).scalar()
    if rid is None:
        raise PredictionError("no upcoming race in the calendar")
    return int(rid)


def provisional_grid_rows(db: Any, race_id: int) -> dict[int, int]:
    from race_forecast_v1 import provisional_grid
    return provisional_grid(db, race_id)


def known_grid(db: Any, race_id: int) -> tuple[dict[int, int], str | None]:
    """driver -> grid slot and its source, best first; ({}, None) before qualifying."""
    grid = provisional_grid_rows(db, race_id)
    if grid:
        return grid, "provisional_grid"
    official = {int(d): int(g) for d, g in db.execute(text("""
        SELECT re.driver_id, rr.starting_grid_position FROM race_results rr
        JOIN sessions s ON s.id = rr.session_id AND s.session_type = 'R'
        JOIN race_entries re ON re.id = rr.race_entry_id
        WHERE s.race_id = :r AND rr.starting_grid_position > 0"""), {"r": race_id})}
    if official:
        return official, "starting_grid"
    quali = {int(d): int(p) for d, p in db.execute(text("""
        SELECT re.driver_id, q.final_position FROM qualifying_results q
        JOIN sessions s ON s.id = q.session_id AND s.session_type = 'Q'
        JOIN race_entries re ON re.id = q.race_entry_id
        WHERE s.race_id = :r AND q.final_position IS NOT NULL AND re.role = 'race_driver'"""), {"r": race_id})}
    return (quali, "qualifying") if quali else ({}, None)


def entry_list(db: Any, race: dict, weekends: Sequence[cm.Weekend]) -> tuple[dict[int, int], str]:
    """driver -> team: the race's own entry list when loaded, else the current line-up."""
    entries = {int(d): int(t) for d, t in db.execute(text("""
        SELECT driver_id, team_id FROM race_entries WHERE race_id = :r AND role = 'race_driver'"""),
        {"r": race["id"]})}
    if len(entries) >= 10:
        return entries, "entry list"
    done = [w for w in weekends if w.season == race["season_year"] and w.raced and w.race_date < race["race_date"]]
    if not done:
        done = [w for w in weekends if w.raced and w.race_date < race["race_date"]][-1:]
    if not done:
        raise PredictionError("no earlier race to take the line-up from")
    return cm.season_lineup(done), f"line-up of round {done[-1].round} (entry list not loaded yet)"


# --- prediction --------------------------------------------------------------------

def stage_probabilities(weekends: Sequence[cm.Weekend], race: dict, lineup: dict[int, int],
                        grid: dict[int, int] | None) -> cm.RaceProbabilities:
    entrants = [(d, t, (grid or {}).get(d)) for d, t in sorted(lineup.items())]
    out = cm.race_win_chances(weekends, race_date=race["race_date"], era=race["regulation_era"],
                              season=race["season_year"], entrants=entrants)
    if out is None:
        raise PredictionError("too few earlier races for the strength model")
    return out[0]


def predict(db: Any, race_id: int, *, names: bool = True) -> dict:
    """Both stages when the grid is known (to show what qualifying changed), else pre-qualifying."""
    race = race_row(db, race_id)
    weekends = cm.load_weekends(db, start_year=2018, end_year=race["season_year"])
    lineup, lineup_source = entry_list(db, race, weekends)
    grid, grid_source = known_grid(db, race_id)
    driver_names = ({int(i): n for i, n in db.execute(text("SELECT id, name FROM drivers"))} if names else {})
    team_names = ({int(i): n for i, n in db.execute(text("SELECT id, name FROM teams"))} if names else {})
    table = load_confidence()
    stages = {}
    for stage in STAGES:
        if stage == "post_qualifying" and not grid:
            continue
        probs = stage_probabilities(weekends, race, lineup, grid if stage == "post_qualifying" else None)
        ranked = sorted(lineup, key=lambda d: (-probs.p_win[d], (grid or {}).get(d, 99)))
        fav = ranked[0]
        stages[stage] = {
            "favourite": {"driver_id": fav, "driver_name": driver_names.get(fav), "team_name": team_names.get(lineup[fav]),
                          "p_win": round(probs.p_win[fav], 4), "p_podium": round(probs.p_podium[fav], 4)},
            "confidence": confidence(stage, probs.p_win[fav], table),
            "drivers": [{"driver_id": d, "driver_name": driver_names.get(d), "team_name": team_names.get(lineup[d]),
                         "grid": (grid or {}).get(d) if stage == "post_qualifying" else None,
                         "p_win": round(probs.p_win[d], 4), "p_podium": round(probs.p_podium[d], 4)}
                        for d in ranked],
        }
    current = "post_qualifying" if "post_qualifying" in stages else "pre_qualifying"
    return {"race_id": race["id"], "season": race["season_year"], "round": race["round_number"],
            "track": race["track"], "race_date": race["race_date"], "stage": current,
            "grid_source": grid_source, "lineup_source": lineup_source,
            "predicted_winner": stages[current]["favourite"], "confidence": stages[current]["confidence"],
            "stages": stages,
            "method": {"pre_qualifying": "car (per season) + driver skill from earlier finishing orders",
                       "post_qualifying": "the same plus the starting grid (as the race forecast)",
                       "confidence": "how often the favourite won earlier races at the same stage with a similar "
                                     "chance (walk-forward backtest)"}}


# --- backtest -------------------------------------------------------------------------

def backtest(weekends: Sequence[cm.Weekend], start: int, end: int, *, sims: int = BACKTEST_SIMS) -> dict:
    """Every race of start..end predicted at both stages from the races before it only."""
    raced = [w for w in weekends if w.raced]
    strengths_for = cm._cached_strengths(cm.RACE_WINTER_SD)
    grid_coef: dict[int, float] = {}
    records: dict[str, list[tuple[float, bool, float]]] = {s: [] for s in STAGES}
    for idx, w in enumerate(raced):
        if not (start <= w.season <= end) or idx < cm.MIN_HISTORY:
            continue
        before = raced[:idx]
        if w.season not in grid_coef:
            pre_season = [x for x in before if x.season < w.season] or before
            grid_coef[w.season] = cm.fit_grid_coef(pre_season, strengths_for)
        st = strengths_for(before, w)
        dnf = cm.dnf_rate(before, w.era)
        winner = next((r.driver_id for r in w.race if r.finish == 1), None)
        if winner is None:
            continue
        for stage in STAGES:
            entrants = [(r.driver_id, r.team_id, r.grid if stage == "post_qualifying" else None) for r in w.race]
            p = cm.race_probabilities(st, entrants, grid_coef=grid_coef[w.season], dnf=dnf, sims=sims).p_win
            fav = max(p, key=p.get)
            records[stage].append((p[fav], fav == winner, p.get(winner, 0.0)))
    out = {}
    for stage, rows in records.items():
        if not rows:
            continue
        bins = []
        for lo, hi in BINS:
            sel = [r for r in rows if lo <= r[0] < hi]
            if sel:
                bins.append({"lo": lo, "hi": hi, "races": len(sel), "mean_p": round(mean(r[0] for r in sel), 3),
                             "favourite_won": round(mean(r[1] for r in sel), 3)})
        out[stage] = {"races": len(rows), "favourite_won": round(mean(r[1] for r in rows), 3),
                      "mean_favourite_p": round(mean(r[0] for r in rows), 3),
                      "log_loss_winner": round(mean(-math.log(max(r[2], 1e-3)) for r in rows), 3),
                      "bins": bins}
    return out


# --- CLI ---------------------------------------------------------------------------

def print_prediction(pred: dict) -> None:
    print(f"=== {pred['season']} R{pred['round']} {pred['track']} ({pred['race_date']}) ===")
    print(f"  line-up: {pred['lineup_source']}; grid: {pred['grid_source'] or 'not known yet (before qualifying)'}")
    for stage, block in pred["stages"].items():
        fav, conf = block["favourite"], block["confidence"]
        print(f"\n  {stage.replace('_', ' ').upper()}: predicted winner {fav['driver_name']} ({fav['team_name']}) "
              f"-- win {fav['p_win']:.1%}, podium {fav['p_podium']:.1%}")
        print(f"  confidence {conf['level'].upper()}: {conf['basis']}")
        for d in block["drivers"][:6]:
            grid = f"P{d['grid']:<2}" if d["grid"] else "   "
            print(f"    {grid} {d['driver_name'] or d['driver_id']:24} {d['team_name'] or '':20} "
                  f"win {d['p_win']:6.1%}  podium {d['p_podium']:6.1%}")
    print(f"\n  current stage: {pred['stage']} -> predicted winner {pred['predicted_winner']['driver_name']}, "
          f"{pred['predicted_winner']['p_win']:.1%}, confidence {pred['confidence']['level']}")


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--next", action="store_true")
    parser.add_argument("--race-id", type=int)
    parser.add_argument("--backtest", action="store_true")
    parser.add_argument("--start-year", type=int, default=2019)
    args = parser.parse_args()
    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as db:
        if args.backtest:
            weekends = cm.load_weekends(db, start_year=2018)
            end = max(w.season for w in weekends if w.raced)
            stages = backtest(weekends, args.start_year, end)
            CONFIDENCE_FILE.write_text(json.dumps({"source": f"winner_prediction_v1 --backtest, {args.start_year}-{end}, "
                                                             "walk-forward", "stages": stages}, indent=2))
            print(f"=== WINNER BACKTEST {args.start_year}-{end} (each race from the races before it) ===")
            for stage, rec in stages.items():
                print(f"  {stage}: {rec['races']} races, favourite won {rec['favourite_won']:.1%} "
                      f"(average favourite chance {rec['mean_favourite_p']:.1%}), winner log loss {rec['log_loss_winner']}")
                for b in rec["bins"]:
                    print(f"    favourite had {b['lo']:.0%}-{min(b['hi'], 1):.0%}: {b['races']:4} races, "
                          f"said {b['mean_p']:.1%}, won {b['favourite_won']:.1%} -> {level(b['favourite_won'])}")
            print(f"  saved {CONFIDENCE_FILE.name}")
        if args.next or args.race_id:
            try:
                print_prediction(predict(db, args.race_id or next_race_id(db)))
            except PredictionError as exc:
                print(f"no prediction: {exc}")
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
