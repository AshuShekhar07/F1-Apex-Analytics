"""Walk-forward test of championship_model_v1: race winners and both championships.

Every past weekend is predicted with only the weekends before it:

RACE WINNER (each race, grid known): strength model + grid effect vs the current production
  win/podium chances (historical rate of the grid slot) and "pole wins". Brier and log loss
  (lower = better), winner called by the favourite, CI resampling races.

CHAMPIONSHIPS (each completed season, before round 1 and after every round): title chances
  for every driver and team vs two baselines -- "the points leader wins" and "chance in
  proportion to points". Reported by stage of the season: chance given to the eventual
  champion, log loss, favourite right, and stability (average change of the champion's
  chance from one round to the next -- the consistency asked for). Champions computed
  from the database are checked against the real ones first.

    python championship_backtest_v1.py                       # 2018-2025 + the 2026 projection
"""

from __future__ import annotations

import argparse
import math
import os
from collections import defaultdict
from statistics import mean

import numpy as np
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from championship_model_v1 import (
    Weekend,
    dnf_rate,
    fit_grid_coef,
    fit_strengths,
    load_weekends,
    project_championship,
    race_probabilities,
    standings,
)

BACKTEST_SIMS = 4000
# Real champions, used only to CHECK the points stored in the database (never as input).
KNOWN_CHAMPIONS = {
    2018: ("hamilton", "mercedes"), 2019: ("hamilton", "mercedes"), 2020: ("hamilton", "mercedes"),
    2021: ("verstappen", "mercedes"), 2022: ("verstappen", "red bull"), 2023: ("verstappen", "red bull"),
    2024: ("verstappen", "mclaren"), 2025: ("norris", "mclaren"),
}
STAGES = ((0.0, 0.25, "first quarter"), (0.25, 0.5, "second quarter"), (0.5, 0.75, "third quarter"),
          (0.75, 1.01, "last quarter"))


class StrengthCache:
    """Strengths fitted on the raced weekends before index i (prefix of the raced list)."""

    def __init__(self, raced: list[Weekend]):
        self.raced, self.cache = raced, {}

    def before(self, n: int, era: str | None):
        key = (n, era)
        if key not in self.cache:
            self.cache[key] = fit_strengths(self.raced[:n], era=era)
        return self.cache[key]

    def for_prefix(self, before: list[Weekend], era: str | None):
        return self.before(len(before), era)


def champion(points: dict[int, float]) -> int | None:
    return max(points, key=points.get) if points else None


def baseline_probs(points: dict[int, float], candidates: list[int]) -> tuple[dict, dict]:
    """(leader-takes-all, proportional-to-points) title chances."""
    if not points or max(points.values(), default=0) <= 0:
        uniform = {c: 1 / len(candidates) for c in candidates}
        return uniform, uniform
    top = max(points.values())
    leaders = [c for c, p in points.items() if p == top]
    total = sum(max(p, 0) for p in points.values())
    return ({c: (1 / len(leaders) if c in leaders else 0.0) for c in points},
            {c: max(p, 0) / total for c, p in points.items()})


def log_loss(p: float) -> float:
    return -math.log(max(p, 1e-3))


def race_backtest(weekends: list[Weekend], cache: StrengthCache, rates, start: int, end: int) -> dict:
    from race_form_signal_v1 import race_clustered

    raced = cache.raced
    by_race = defaultdict(dict)
    coef_by_season = {}
    for idx, w in enumerate(raced):
        if not (start <= w.season <= end) or idx < 10:
            continue
        if w.season not in coef_by_season:
            coef_by_season[w.season] = fit_grid_coef(raced[:idx], cache.for_prefix)
        st = cache.before(idx, w.era)
        dnf = dnf_rate(raced[:idx], w.era)
        entrants = [(r.driver_id, r.team_id, r.grid) for r in w.race]
        model = race_probabilities(st, entrants, grid_coef=coef_by_season[w.season], dnf=dnf, sims=BACKTEST_SIMS)
        form_only = race_probabilities(st, entrants, grid_coef=0.0, dnf=dnf, sims=BACKTEST_SIMS)
        slot = rates(w.race_date, w.era, len(entrants))
        winner = next((r.driver_id for r in w.race if r.finish == 1), None)
        podium = {r.driver_id for r in w.race if r.finish and r.finish <= 3}
        pole = min(w.race, key=lambda r: r.grid or 99).driver_id
        for name, p_win, p_pod in (
            ("strength + grid", model.p_win, model.p_podium),
            ("strength only", form_only.p_win, form_only.p_podium),
            ("grid slot (production)", {r.driver_id: slot.get(r.grid or 99, (0, 0))[0] for r in w.race},
             {r.driver_id: slot.get(r.grid or 99, (0, 0))[1] for r in w.race}),
        ):
            fav = max(p_win, key=p_win.get)
            by_race[name][w.race_id] = {
                "brier_win": mean((p_win.get(d, 0) - (d == winner)) ** 2 for d, _, _ in entrants),
                "brier_podium": mean((p_pod.get(d, 0) - (d in podium)) ** 2 for d, _, _ in entrants),
                "log_loss_win": log_loss(p_win.get(winner, 0)) if winner else None,
                "favourite_won": float(fav == winner),
            }
        by_race["pole sitter"][w.race_id] = {"favourite_won": float(pole == winner)}
    out = {"races": len(by_race["strength + grid"]), "grid_coef": coef_by_season, "models": {}}
    base = by_race["grid slot (production)"]
    for name, races in by_race.items():
        row = {}
        for metric in ("brier_win", "brier_podium", "log_loss_win", "favourite_won"):
            vals = {r: [v[metric]] for r, v in races.items() if v.get(metric) is not None}
            if not vals:
                continue
            row[metric] = race_clustered(vals)
            if name != "grid slot (production)":
                diffs = {r: [v[metric] - base[r][metric]] for r, v in races.items()
                         if v.get(metric) is not None and base.get(r, {}).get(metric) is not None}
                if diffs:
                    row[metric + "_vs_production"] = race_clustered(diffs)
        out["models"][name] = row
    return out


def championship_backtest(weekends: list[Weekend], cache: StrengthCache, start: int, end: int) -> dict:
    raced = cache.raced
    position = {w.race_id: i for i, w in enumerate(raced)}
    records = defaultdict(list)                     # (kind, model) -> [(season, stage share, p_champ, fav_right, brier)]
    trajectories = defaultdict(list)                # (kind, model, season) -> [p_champ by round]
    calibration = defaultdict(list)                 # kind -> [(model chance, became champion)]
    checks = []
    for season in range(start, end + 1):
        season_w = [w for w in weekends if w.season == season]
        if not season_w or not all(w.raced for w in season_w):
            continue
        final_d, final_t = standings(season_w, season)
        champ_d, champ_t = champion(final_d), champion(final_t)
        checks.append((season, champ_d, champ_t))
        n = len(season_w)
        for k in range(n):
            nxt = season_w[k]
            history_n = position[nxt.race_id]
            st = cache.before(history_n, nxt.era)
            dnf = dnf_rate(raced[:history_n], nxt.era)
            lineup = {r.driver_id: r.team_id for r in nxt.race}        # entry list of the next race
            d_pts, t_pts = standings(season_w[:k], season)
            proj = project_championship(st, lineup, season_w[k:], driver_points=d_pts, team_points=t_pts,
                                        dnf=dnf, sims=BACKTEST_SIMS)
            share = k / n
            for kind, model_p, pts, champ, candidates in (
                ("drivers", proj.p_driver_title, d_pts, champ_d, sorted(set(lineup) | set(d_pts))),
                ("constructors", proj.p_team_title, t_pts, champ_t, sorted(set(lineup.values()) | set(t_pts))),
            ):
                leader, share_p = baseline_probs({c: pts.get(c, 0.0) for c in candidates}, candidates)
                for name, probs in (("model", model_p), ("points leader wins", leader), ("in proportion to points", share_p)):
                    p = probs.get(champ, 0.0)
                    fav = max(probs, key=probs.get) if probs else None
                    brier = sum((probs.get(c, 0.0) - (c == champ)) ** 2 for c in set(probs) | {champ})
                    records[(kind, name)].append((season, share, p, float(fav == champ), brier))
                    trajectories[(kind, name, season)].append(p)
                calibration[kind] += [(model_p.get(c, 0.0), c == champ) for c in candidates]
    return {"records": records, "trajectories": trajectories, "checks": checks, "calibration": calibration}


CALIBRATION_BINS = (0.0, 0.02, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0001)


def calibration_rows(pairs: list[tuple[float, bool]]) -> list[tuple[str, int, float, float]]:
    out = []
    for lo, hi in zip(CALIBRATION_BINS, CALIBRATION_BINS[1:]):
        rows = [(p, y) for p, y in pairs if lo <= p < hi]
        if rows:
            out.append((f"{lo:.0%}-{min(hi, 1):.0%}", len(rows), mean(p for p, _ in rows), mean(y for _, y in rows)))
    return out


def names(db) -> tuple[dict[int, str], dict[int, str]]:
    return ({int(i): n for i, n in db.execute(text("SELECT id, name FROM drivers"))},
            {int(i): n for i, n in db.execute(text("SELECT id, name FROM teams"))})


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start-year", type=int, default=2018)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument("--season", type=int, default=2026, help="season to project now")
    args = parser.parse_args()

    from prediction_scoreboard_v1 import load_finishes, slot_rate_function
    from race_form_signal_v1 import race_clustered

    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as db:
        weekends = load_weekends(db, start_year=2018)
        rates = slot_rate_function(load_finishes(db))
        driver_names, team_names = names(db)
    raced = [w for w in weekends if w.raced]
    cache = StrengthCache(raced)

    print("=== CHECK: champions from the stored points vs the real ones ===")
    champ = championship_backtest(weekends, cache, args.start_year, args.end_year)
    bad = 0
    for season, d, t in champ["checks"]:
        real = KNOWN_CHAMPIONS.get(season)
        dn, tn = driver_names.get(d, "?"), team_names.get(t, "?")
        ok = real is None or (real[0] in dn.lower() and real[1] in tn.lower())
        bad += not ok
        print(f"  {season}: drivers {dn}, constructors {tn}  {'ok' if ok else 'MISMATCH -- check the points data'}")

    print("\n=== RACE WINNER (each race, grid known; lower Brier / log loss = better) ===")
    race = race_backtest(weekends, cache, rates, max(args.start_year, 2019), args.end_year)
    print(f"  {race['races']} races; grid effect per season: "
          + ", ".join(f"{s}: {c:.2f}" for s, c in sorted(race["grid_coef"].items())))
    for name, row in race["models"].items():
        cells = []
        for metric in ("favourite_won", "brier_win", "brier_podium", "log_loss_win"):
            if metric in row:
                v = row[metric][0]
                cells.append(f"{metric} {v:.1%}" if metric == "favourite_won" else f"{metric} {v:.4f}")
        print(f"  {name:24} " + "  ".join(cells))
        for metric in ("brier_win", "brier_podium", "log_loss_win", "favourite_won"):
            key = metric + "_vs_production"
            if key in row:
                d, lo, hi = row[key]
                lower_better = metric != "favourite_won"
                verdict = ("BETTER" if (hi < 0 if lower_better else lo > 0) else
                           "WORSE" if (lo > 0 if lower_better else hi < 0) else "no clear difference")
                print(f"      vs production {metric:14} {d:+.4f} [{lo:+.4f}, {hi:+.4f}]  {verdict}")

    for kind in ("drivers", "constructors"):
        print(f"\n=== {kind.upper()} CHAMPIONSHIP ({args.start_year}-{args.end_year}, every round) ===")
        print(f"  {'stage':16} {'model':26} {'chance given to champion':>25} {'favourite right':>16} {'log loss':>9} {'Brier':>7}")
        for lo_s, hi_s, label in STAGES:
            for name in ("model", "points leader wins", "in proportion to points"):
                rows = [r for r in champ["records"][(kind, name)] if lo_s <= r[1] < hi_s]
                if rows:
                    print(f"  {label:16} {name:26} {mean(r[2] for r in rows):>25.1%} {mean(r[3] for r in rows):>16.1%} "
                          f"{mean(log_loss(r[2]) for r in rows):>9.3f} {mean(r[4] for r in rows):>7.3f}")
        for name in ("model", "points leader wins", "in proportion to points"):
            moves = [abs(b - a) for (k, n, s), traj in champ["trajectories"].items() if k == kind and n == name
                     for a, b in zip(traj, traj[1:])]
            if moves:
                print(f"  consistency, {name:24} champion's chance moves {mean(moves):.1%} per round on average "
                      f"(largest {max(moves):.0%})")
        # calibration of the model's title chances (every candidate, every round)
        print("  calibration (model): when we said X, they became champion Y")
        for label, n, said, happened in calibration_rows(champ["calibration"][kind]):
            print(f"    {label:>9}  n={n:5}  said {said:6.1%}  happened {happened:6.1%}")

    print(f"\n=== {args.season} PROJECTION (data up to today) ===")
    season_w = [w for w in weekends if w.season == args.season]
    done = [w for w in season_w if w.raced]
    remaining = [w for w in season_w if not w.raced]
    if done:
        st = cache.before(len(raced), done[-1].era)
        lineup = {r.driver_id: r.team_id for r in done[-1].race}
        d_pts, t_pts = standings(done, args.season)
        proj = project_championship(st, lineup, remaining, driver_points=d_pts, team_points=t_pts,
                                    dnf=dnf_rate(raced, done[-1].era))
        print(f"  after round {done[-1].round}, {len(remaining)} weekends left")
        for label, probs, now, exp, nm in (("drivers", proj.p_driver_title, d_pts, proj.expected_driver_points, driver_names),
                                           ("constructors", proj.p_team_title, t_pts, proj.expected_team_points, team_names)):
            print(f"  {label}:")
            for k in sorted(probs, key=lambda k: -probs[k])[:6]:
                print(f"    {nm.get(k, k):24} title {probs[k]:6.1%}   points now {now.get(k, 0):5.0f}   expected final {exp.get(k, 0):5.0f}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
