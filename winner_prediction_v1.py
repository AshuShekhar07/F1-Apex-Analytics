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

More data = more confidence: the record is also kept by how much of the season has been
seen (season_start: 0-2 races run this season, early_season: 3-6, established: 7+; races
under brand-new regulations counted separately when there are enough of them). The level
shown is the lower of "how did favourites do with this much data" and "how did favourites
with a similar chance do" -- high only with plenty of data AND a clear favourite.

Win-chance calibration: the strength model learns from whole finishing orders, and the midfield
is far more random than the fight for the win, so its favourite's chance came out too low
(backtest: before qualifying said 25%, the favourite won 44%; after, said 42%, won 59%). Each
stage's P(win) is therefore sharpened, p^k / sum p^k, with k fitted on which drivers actually
won earlier races. The ranking (and so the predicted winner) never changes, only the chance.
Used only when the walk-forward backtest passes: winner log loss better (race-clustered CI
excludes 0) and calibration error not worse; the verdict is stored with the table.

    python winner_prediction_v1.py --next                 # the next race, both stages if the grid is known
    python winner_prediction_v1.py --race-id 189
    python winner_prediction_v1.py --backtest             # (re)measure the confidence table, 2019 onwards
    python winner_prediction_v1.py --calendar             # remaining races and their ids
    python winner_prediction_v1.py --season 2021 --after-round 10   # replay: the next race and both
                                                          # titles as the model saw them then, vs what happened
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
MIN_CALIBRATION_RACES = 20  # earlier races needed to fit the sharpening for a season
DATA_STAGES = (("season_start", 0, 2), ("early_season", 3, 6), ("established", 7, 99))
NEW_RULES_RACES = 3         # fewer races than this under the current regulations = new regulations
LEVEL_ORDER = ("low", "medium", "high")
POWER_BOUNDS = (0.5, 4.0)
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


def sharpen(p_win: dict, k: float) -> dict:
    """p^k / sum p^k: k > 1 makes favourites likelier, k = 1 changes nothing."""
    if k == 1.0:
        return dict(p_win)
    powered = {d: max(p, 0.0) ** k for d, p in p_win.items()}
    total = sum(powered.values()) or 1.0
    return {d: v / total for d, v in powered.items()}


def win_power(stage: str, table: dict | None = None) -> float:
    """The promoted sharpening for this stage, 1.0 when none passed the backtest."""
    cal = (table or load_confidence()).get(stage, {}).get("calibration") or {}
    return float(cal["power"]) if cal.get("promoted") else 1.0


def fit_power(cases: Sequence[tuple[dict, int]]) -> float:
    """k maximising the log chance given to the actual winners."""
    from scipy.optimize import minimize_scalar

    def neg(k):
        return -sum(math.log(max(sharpen(p, k).get(w, 0.0), 1e-6)) for p, w in cases)
    return float(minimize_scalar(neg, bounds=POWER_BOUNDS, method="bounded").x)


def data_stage(weekends: Sequence[cm.Weekend], race_date: date, season: int, era: str | None) -> dict:
    """How much evidence there is for this race: races run this season and under these rules."""
    before = [w for w in weekends if w.raced and w.race_date < race_date]
    this_season = sum(w.season == season for w in before)
    this_era = sum(w.era == era for w in before) if era else len(before)
    bucket = next(name for name, lo, hi in DATA_STAGES if lo <= this_season <= hi)
    return {"races_this_season": this_season, "races_under_these_regulations": this_era,
            "new_regulations": this_era < NEW_RULES_RACES, "bucket": bucket}


def confidence(stage: str, favourite_p: float, table: dict | None = None, data: dict | None = None) -> dict:
    """Measured record of favourites at this qualifying stage: with a similar chance, and with
    as much data as now; the level is the lower of the two."""
    rec = (table or load_confidence())[stage]
    match = next((b for b in rec.get("bins", []) if b["lo"] <= favourite_p < b["hi"]), None)
    if match and match["races"] >= MIN_BIN_RACES:
        rate, n, basis = match["favourite_won"], match["races"], (
            f"in {match['races']} earlier races where the favourite had {match['lo']:.0%}-{min(match['hi'], 1):.0%} "
            f"at this stage, the favourite won {match['favourite_won']:.0%}")
    else:
        rate, n, basis = rec["favourite_won"], rec["races"], (
            f"in {rec['races']} earlier races at this stage, the favourite won {rec['favourite_won']:.0%}")
    out = {"level": level(rate), "favourite_won_historically": round(rate, 3), "races": n, "basis": basis,
           "stage_record": round(rec["favourite_won"], 3)}
    if data:
        by_data = rec.get("by_data_stage", {})
        drec = by_data.get("new_regulations") if data.get("new_regulations") else None
        label = "under brand-new regulations"
        if not drec or drec["races"] < MIN_BIN_RACES:
            drec, label = by_data.get(data["bucket"]), {
                "season_start": "in the first races of a season", "early_season": "after 3-6 races of a season",
                "established": "with 7+ races of the season run"}[data["bucket"]]
        if drec and drec["races"] >= MIN_BIN_RACES:
            data_level = level(drec["favourite_won"])
            out["data_basis"] = (f"{label}, the favourite won {drec['favourite_won']:.0%} of {drec['races']} "
                                 f"earlier races at this stage")
            out["data_record"] = drec["favourite_won"]
            if LEVEL_ORDER.index(data_level) < LEVEL_ORDER.index(out["level"]):
                out["level"] = data_level
        out["data"] = data
    return out


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
    evidence = data_stage(weekends, race["race_date"], race["season_year"], race["regulation_era"])
    stages = {}
    for stage in STAGES:
        if stage == "post_qualifying" and not grid:
            continue
        raw = stage_probabilities(weekends, race, lineup, grid if stage == "post_qualifying" else None)
        p_win = sharpen(raw.p_win, win_power(stage, table))
        probs = cm.RaceProbabilities(p_win, {d: max(raw.p_podium[d], p_win[d]) for d in raw.p_podium}, raw.p_points)
        ranked = sorted(lineup, key=lambda d: (-probs.p_win[d], (grid or {}).get(d, 99)))
        fav = ranked[0]
        stages[stage] = {
            "favourite": {"driver_id": fav, "driver_name": driver_names.get(fav), "team_name": team_names.get(lineup[fav]),
                          "p_win": round(probs.p_win[fav], 4), "p_podium": round(probs.p_podium[fav], 4)},
            "confidence": confidence(stage, probs.p_win[fav], table, evidence),
            "win_calibration_power": win_power(stage, table),
            "drivers": [{"driver_id": d, "driver_name": driver_names.get(d), "team_name": team_names.get(lineup[d]),
                         "grid": (grid or {}).get(d) if stage == "post_qualifying" else None,
                         "p_win": round(probs.p_win[d], 4), "p_podium": round(probs.p_podium[d], 4)}
                        for d in ranked],
        }
    current = "post_qualifying" if "post_qualifying" in stages else "pre_qualifying"
    return {"race_id": race["id"], "season": race["season_year"], "round": race["round_number"],
            "track": race["track"], "race_date": race["race_date"], "stage": current,
            "grid_source": grid_source, "lineup_source": lineup_source, "evidence": evidence,
            "predicted_winner": stages[current]["favourite"], "confidence": stages[current]["confidence"],
            "stages": stages,
            "method": {"pre_qualifying": "car (per season) + driver skill from earlier finishing orders",
                       "post_qualifying": "the same plus the starting grid (as the race forecast)",
                       "confidence": "how often the favourite won earlier races at the same stage with a similar "
                                     "chance (walk-forward backtest)"}}


# --- backtest -------------------------------------------------------------------------

def _summary(rows: Sequence[tuple[float, bool, float]]) -> dict:
    """rows: (favourite's chance, favourite won, winner's chance)."""
    bins = []
    for lo, hi in BINS:
        sel = [r for r in rows if lo <= r[0] < hi]
        if sel:
            bins.append({"lo": lo, "hi": hi, "races": len(sel), "mean_p": round(mean(r[0] for r in sel), 3),
                         "favourite_won": round(mean(r[1] for r in sel), 3)})
    return {"races": len(rows), "favourite_won": round(mean(r[1] for r in rows), 3),
            "mean_favourite_p": round(mean(r[0] for r in rows), 3),
            "log_loss_winner": round(mean(-math.log(max(r[2], 1e-3)) for r in rows), 3), "bins": bins}


def calibration_error(cases: Sequence[tuple[dict, int]]) -> float:
    """Every driver's P(win) binned; average |said - happened| weighted by drivers."""
    pairs = [(p, d == w) for probs, w in cases for d, p in probs.items()]
    total = 0.0
    for lo, hi in ((0, .02), (.02, .05), (.05, .1), (.1, .2), (.2, .35), (.35, .5), (.5, .7), (.7, 1.01)):
        sel = [(p, y) for p, y in pairs if lo <= p < hi]
        if sel:
            total += len(sel) * abs(mean(p for p, _ in sel) - mean(y for _, y in sel))
    return total / len(pairs) if pairs else 0.0


def _rows(cases: Sequence[tuple[dict, int]]) -> list[tuple[float, bool, float]]:
    out = []
    for p, w in cases:
        fav = max(p, key=p.get)
        out.append((p[fav], fav == w, p.get(w, 0.0)))
    return out


def backtest(weekends: Sequence[cm.Weekend], start: int, end: int, *, sims: int = BACKTEST_SIMS) -> dict:
    """Every race of start..end predicted at both stages from the races before it only; then
    the win-chance sharpening for season S fitted on the races before S and judged on S."""
    from race_form_signal_v1 import race_clustered

    raced = [w for w in weekends if w.raced]
    strengths_for = cm._cached_strengths(cm.RACE_WINTER_SD)
    grid_coef: dict[int, float] = {}
    cases: dict[str, list[tuple[int, int, dict, int]]] = {s: [] for s in STAGES}   # season, race, p_win, winner
    buckets: dict[int, list[str]] = {}
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
        evidence = data_stage(raced, w.race_date, w.season, w.era)
        buckets[w.race_id] = [evidence["bucket"]] + (["new_regulations"] if evidence["new_regulations"] else [])
        for stage in STAGES:
            entrants = [(r.driver_id, r.team_id, r.grid if stage == "post_qualifying" else None) for r in w.race]
            p = cm.race_probabilities(st, entrants, grid_coef=grid_coef[w.season], dnf=dnf, sims=sims).p_win
            cases[stage].append((w.season, w.race_id, p, winner))
    out = {}
    for stage, rows in cases.items():
        if not rows:
            continue
        raw = [(p, wn) for _, _, p, wn in rows]
        calibrated, power_by_season = [], {}
        for season in sorted({c[0] for c in rows}):
            earlier = [(p, wn) for s_, _, p, wn in rows if s_ < season]
            k = fit_power(earlier) if len(earlier) >= MIN_CALIBRATION_RACES else 1.0
            power_by_season[season] = round(k, 3)
            calibrated += [(sharpen(p, k), wn) for s_, _, p, wn in rows if s_ == season]
        diffs = {rid: [-math.log(max(c[0].get(c[1], 0), 1e-3)) + math.log(max(p.get(wn, 0), 1e-3))]
                 for (_, rid, p, wn), c in zip(rows, calibrated)}
        d, lo, hi = race_clustered(diffs)
        ece_raw, ece_cal = calibration_error(raw), calibration_error(calibrated)
        promoted = hi < 0 and ece_cal <= ece_raw
        shown = calibrated if promoted else raw
        by_data = {}
        for name in (*(n for n, _, _ in DATA_STAGES), "new_regulations"):
            sel = [c for (_, rid, _, _), c in zip(rows, shown) if name in buckets[rid]]
            if sel:
                by_data[name] = _summary(_rows(sel))
        out[stage] = {**_summary(_rows(shown)), "by_data_stage": by_data,
                      "uncalibrated": _summary(_rows(raw)),
                      "walk_forward_calibrated": _summary(_rows(calibrated)),
                      "calibration": {"power": round(fit_power(raw), 3), "promoted": promoted,
                                      "power_by_season": power_by_season,
                                      "log_loss_change": [round(d, 4), round(lo, 4), round(hi, 4)],
                                      "calibration_error": {"uncalibrated": round(ece_raw, 4),
                                                            "calibrated": round(ece_cal, 4)}}}
    return out


# --- CLI ---------------------------------------------------------------------------

def remaining_races(db: Any, today: date | None = None) -> list[dict]:
    today = today or date.today()
    return [dict(r) for r in db.execute(text("""
        SELECT r.id, r.season_year, r.round_number, r.race_date, t.name AS track,
               EXISTS (SELECT 1 FROM sessions s JOIN qualifying_results q ON q.session_id = s.id
                       WHERE s.race_id = r.id AND s.session_type = 'Q') AS qualifying_loaded
        FROM races r JOIN tracks t ON t.id = r.track_id
        WHERE r.race_date >= :d
          AND NOT EXISTS (SELECT 1 FROM sessions s JOIN race_results rr ON rr.session_id = s.id
                          WHERE s.race_id = r.id AND s.session_type = 'R')
        ORDER BY r.race_date"""), {"d": today - timedelta(days=2)}).mappings()]


def print_calendar(db: Any) -> None:
    print("=== REMAINING RACES ===")
    for r in remaining_races(db):
        print(f"  race id {r['id']:4}  {r['season_year']} R{r['round_number']:<2} {r['track']:24} {r['race_date']}"
              f"  {'qualifying loaded' if r['qualifying_loaded'] else 'before qualifying'}")


def race_id_for(db: Any, season: int, round_number: int) -> int:
    rid = db.execute(text("SELECT id FROM races WHERE season_year = :s AND round_number = :r"),
                     {"s": season, "r": round_number}).scalar()
    if rid is None:
        raise PredictionError(f"{season} round {round_number} is not in the calendar")
    return int(rid)


def actual_result(db: Any, race_id: int) -> list[dict]:
    """Top three of the race as it happened ([] if not run)."""
    return [dict(r) for r in db.execute(text("""
        SELECT rr.finishing_position AS position, d.id AS driver_id, d.name AS driver_name, t.name AS team_name,
               rr.starting_grid_position AS grid
        FROM race_results rr JOIN sessions s ON s.id = rr.session_id AND s.session_type = 'R'
        JOIN race_entries re ON re.id = rr.race_entry_id JOIN drivers d ON d.id = re.driver_id
        JOIN teams t ON t.id = re.team_id
        WHERE s.race_id = :r AND rr.finishing_position BETWEEN 1 AND 3 ORDER BY rr.finishing_position"""),
        {"r": race_id}).mappings()]


def print_replay(db: Any, season: int, after_round: int) -> None:
    """The next race and both titles as the model saw them after `after_round`, with the outcome."""
    race_id = race_id_for(db, season, after_round + 1)
    pred = predict(db, race_id)
    print(f"=== REPLAY: {season} after round {after_round} (only races before round {after_round + 1} are used) ===\n")
    print_prediction(pred)
    result = actual_result(db, race_id)
    if result:
        print("\n  WHAT HAPPENED: " + ", ".join(f"P{r['position']} {r['driver_name']} ({r['team_name']}, from P{r['grid']})"
                                               for r in result))
        winner = result[0]["driver_id"]
        for stage, block in pred["stages"].items():
            chance = next((d["p_win"] for d in block["drivers"] if d["driver_id"] == winner), 0.0)
            rank = next((i for i, d in enumerate(block["drivers"], 1) if d["driver_id"] == winner), None)
            verdict = "RIGHT" if block["favourite"]["driver_id"] == winner else f"winner was our #{rank}"
            print(f"  {stage.replace('_', ' ')}: we gave the winner {chance:.1%} -- {verdict}")

    from championship_model_v1 import load_weekends, project_season, standings, title_confidence

    weekends = load_weekends(db, start_year=2018, end_year=season)
    season_w = [w for w in weekends if w.season == season]
    sp = project_season(weekends, season, after_round=after_round)
    dn = {int(i): n for i, n in db.execute(text("SELECT id, name FROM drivers"))}
    tn = {int(i): n for i, n in db.execute(text("SELECT id, name FROM teams"))}
    final = standings(season_w, season) if all(w.raced for w in season_w) else None
    print(f"\n  TITLES after round {after_round} of {len(season_w)}:")
    for i, (kind, probs, pts, names) in enumerate((("drivers", sp.projection.p_driver_title, sp.driver_points, dn),
                                                    ("constructors", sp.projection.p_team_title, sp.team_points, tn))):
        conf = title_confidence(kind, after_round, len(season_w), favourite_p=max(probs.values()))
        print(f"    {kind}: confidence {conf['level'].upper()} ({conf['basis']}"
              + (f"; {conf['chance_basis']}" if conf.get("chance_basis") else "") + ")")
        for k in sorted(probs, key=lambda k: -probs[k])[:4]:
            print(f"      {names.get(k, k):24} title {probs[k]:6.1%}   points then {pts.get(k, 0):5.0f}")
        if final:
            champ = max(final[i], key=final[i].get)
            print(f"      -> champion: {names.get(champ)} ({final[i][champ]:.0f} pts); we gave {probs.get(champ, 0):.1%} "
                  f"after round {after_round}")


def print_prediction(pred: dict) -> None:
    print(f"=== {pred['season']} R{pred['round']} {pred['track']} ({pred['race_date']}) ===")
    print(f"  line-up: {pred['lineup_source']}; grid: {pred['grid_source'] or 'not known yet (before qualifying)'}")
    ev = pred["evidence"]
    print(f"  evidence: {ev['races_this_season']} races run this season, {ev['races_under_these_regulations']} under "
          f"these regulations ({ev['bucket'].replace('_', ' ')}{', new regulations' if ev['new_regulations'] else ''})")
    for stage, block in pred["stages"].items():
        fav, conf = block["favourite"], block["confidence"]
        print(f"\n  {stage.replace('_', ' ').upper()}: predicted winner {fav['driver_name']} ({fav['team_name']}) "
              f"-- win {fav['p_win']:.1%}, podium {fav['p_podium']:.1%}")
        print(f"  confidence {conf['level'].upper()}: {conf['basis']}"
              + (f"; {conf['data_basis']}" if conf.get("data_basis") else ""))
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
    parser.add_argument("--calendar", action="store_true", help="list the remaining races and their ids")
    parser.add_argument("--season", type=int, help="with --after-round: replay a past point of a season")
    parser.add_argument("--after-round", type=int)
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
                cal = rec["calibration"]
                d, lo, hi = cal["log_loss_change"]
                print(f"\n  {stage.upper()}: {rec['races']} races")
                for label, summ in (("uncalibrated", rec["uncalibrated"]),
                                    ("calibrated (walk-forward)", rec["walk_forward_calibrated"])):
                    print(f"    {label:26} favourite won {summ['favourite_won']:.1%}, average favourite chance "
                          f"{summ['mean_favourite_p']:.1%}, winner log loss {summ['log_loss_winner']}")
                    for b in summ["bins"]:
                        print(f"      favourite had {b['lo']:.0%}-{min(b['hi'], 1):.0%}: {b['races']:4} races, "
                              f"said {b['mean_p']:.1%}, won {b['favourite_won']:.1%} -> {level(b['favourite_won'])}")
                print(f"    sharpening k by season (fitted on earlier seasons): {cal['power_by_season']}; now {cal['power']}")
                for name, summ in rec["by_data_stage"].items():
                    print(f"    {name:14} {summ['races']:4} races: favourite won {summ['favourite_won']:.1%} "
                          f"(average favourite chance {summ['mean_favourite_p']:.1%}) -> {level(summ['favourite_won'])}")
                print(f"    log loss change {d:+.4f} [{lo:+.4f}, {hi:+.4f}], calibration error "
                      f"{cal['calibration_error']['uncalibrated']:.4f} -> {cal['calibration_error']['calibrated']:.4f}"
                      f"  => {'PROMOTED' if cal['promoted'] else 'not promoted (kept uncalibrated)'}")
            print(f"  saved {CONFIDENCE_FILE.name}")
        if args.calendar:
            print_calendar(db)
        if args.season and args.after_round is not None:
            try:
                print_replay(db, args.season, args.after_round)
            except (PredictionError, ValueError) as exc:
                print(f"no replay: {exc}")
                return 1
        if args.next or args.race_id:
            try:
                print_prediction(predict(db, args.race_id or next_race_id(db)))
            except PredictionError as exc:
                print(f"no prediction: {exc}")
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
