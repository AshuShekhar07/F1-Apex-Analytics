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

BACKTEST_SIMS = 3000
# Real champions, used only to CHECK the points stored in the database (never as input).
KNOWN_CHAMPIONS = {
    2018: ("hamilton", "mercedes"), 2019: ("hamilton", "mercedes"), 2020: ("hamilton", "mercedes"),
    2021: ("verstappen", "mercedes"), 2022: ("verstappen", "red bull"), 2023: ("verstappen", "red bull"),
    2024: ("verstappen", "mclaren"), 2025: ("norris", "mclaren"),
}
STAGES = ((0.0, 0.25, "first quarter"), (0.25, 0.5, "second quarter"), (0.5, 0.75, "third quarter"),
          (0.75, 1.01, "last quarter"))


class StrengthCache:
    """Strengths fitted on the raced weekends before index i (prefix of the raced list), for
    a weekend of the given era and season. winter_sd: see fit_strengths (None = shared cars)."""

    def __init__(self, raced: list[Weekend], winter_sd: float | None = None):
        self.raced, self.winter_sd, self.cache = raced, winter_sd, {}

    def before(self, n: int, era: str | None, season: int | None = None):
        key = (n, era, season if self.winter_sd is not None else None)
        if key not in self.cache:
            self.cache[key] = fit_strengths(self.raced[:n], era=era, winter_sd=self.winter_sd, season=key[2])
        return self.cache[key]

    def for_prefix(self, before: list[Weekend], weekend: Weekend):
        return self.before(len(before), weekend.era, weekend.season)


CAR_MODELS = {"shared cars": None, "season cars, winter 0.3": 0.3, "season cars, winter 0.6": 0.6}
CURRENT = "strength + grid"                # the race model the season-car variants must beat


def strength_caches(raced: list[Weekend]) -> dict[str, StrengthCache]:
    return {name: StrengthCache(raced, w) for name, w in CAR_MODELS.items()}


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


def race_backtest(weekends: list[Weekend], caches: dict[str, StrengthCache], rates, start: int, end: int) -> dict:
    from race_form_signal_v1 import race_clustered

    raced = caches["shared cars"].raced
    by_race = defaultdict(dict)
    coef_by_season = defaultdict(dict)       # car model -> season -> grid effect
    for idx, w in enumerate(raced):
        if not (start <= w.season <= end) or idx < 10:
            continue
        dnf = dnf_rate(raced[:idx], w.era)
        entrants = [(r.driver_id, r.team_id, r.grid) for r in w.race]
        with_grid = {}
        for car_model, cache in caches.items():
            if w.season not in coef_by_season[car_model]:
                coef_by_season[car_model][w.season] = fit_grid_coef(raced[:idx], cache.for_prefix)
            with_grid[car_model] = race_probabilities(cache.before(idx, w.era, w.season), entrants,
                                                      grid_coef=coef_by_season[car_model][w.season], dnf=dnf,
                                                      sims=BACKTEST_SIMS)
        model = with_grid["shared cars"]
        form_only = race_probabilities(caches["shared cars"].before(idx, w.era), entrants, grid_coef=0.0, dnf=dnf,
                                       sims=BACKTEST_SIMS)
        slot = rates(w.race_date, w.era, len(entrants))
        winner = next((r.driver_id for r in w.race if r.finish == 1), None)
        podium = {r.driver_id for r in w.race if r.finish and r.finish <= 3}
        pole = min(w.race, key=lambda r: r.grid or 99).driver_id
        for name, p_win, p_pod in (
            *((f"{CURRENT}, {cm}", p.p_win, p.p_podium) for cm, p in with_grid.items() if cm != "shared cars"),
            (CURRENT, model.p_win, model.p_podium),
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
    out = {"races": len(by_race[CURRENT]), "grid_coef": dict(coef_by_season), "models": {}}
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
            if name.startswith(CURRENT + ", "):
                cur = by_race[CURRENT]
                diffs = {r: [v[metric] - cur[r][metric]] for r, v in races.items()
                         if v.get(metric) is not None and cur.get(r, {}).get(metric) is not None}
                if diffs:
                    row[metric + "_vs_current"] = race_clustered(diffs)
        out["models"][name] = row
    return out


DRIFTS = (0.0, 0.06, 0.15)                 # car development per weekend
SETTINGS = tuple((cm, d) for cm in CAR_MODELS for d in DRIFTS)
DEFAULT_SETTING = ("shared cars", 0.06)    # first season (no earlier season to choose on)


def _key(setting: tuple[str, float]) -> str:
    return f"{setting[0]}, drift {setting[1]}"


def _choose(rounds: list[dict], options) -> tuple[str, float]:
    """setting with the best total log score (both titles) over the given rounds."""
    return min(options, key=lambda st: sum(log_loss(r["probs"][_key(st)].get(r["champ"], 0.0)) for r in rounds))


def championship_backtest(weekends: list[Weekend], caches: dict[str, StrengthCache], start: int, end: int) -> dict:
    """Title chances before every round of every completed season for every (car model,
    drift) setting and the baselines. For season S each model uses the setting with the best
    log score on the seasons BEFORE S (walk-forward):
      'model, season cars + drift'   any setting
      'model, development drift'     shared cars, drift chosen (the current version)
      'model, static cars'           shared cars, no drift"""
    raced = caches["shared cars"].raced
    position = {w.race_id: i for i, w in enumerate(raced)}
    rounds = []          # dicts: season, round, share, kind, champ, candidates, probs {model: {entity: p}}
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
            dnf = dnf_rate(raced[:history_n], nxt.era)
            lineup = {r.driver_id: r.team_id for r in nxt.race}        # entry list of the next race
            d_pts, t_pts = standings(season_w[:k], season)
            by_setting = {(cm, d): project_championship(caches[cm].before(history_n, nxt.era, season), lineup,
                                                        season_w[k:], driver_points=d_pts, team_points=t_pts,
                                                        dnf=dnf, sims=BACKTEST_SIMS, drift=d)
                          for cm, d in SETTINGS}
            for kind, pts, champ, candidates in (
                ("drivers", d_pts, champ_d, sorted(set(lineup) | set(d_pts))),
                ("constructors", t_pts, champ_t, sorted(set(lineup.values()) | set(t_pts))),
            ):
                leader, share_p = baseline_probs({c: pts.get(c, 0.0) for c in candidates}, candidates)
                probs = {_key(st_): (p.p_driver_title if kind == "drivers" else p.p_team_title)
                         for st_, p in by_setting.items()}
                probs.update({"points leader wins": leader, "in proportion to points": share_p})
                rounds.append({"season": season, "round": k + 1, "share": k / n, "kind": kind, "champ": champ,
                               "candidates": candidates, "probs": probs})
    seasons = sorted({r["season"] for r in rounds})
    shared = [("shared cars", d) for d in DRIFTS]
    chosen, chosen_drift = {}, {}
    for season in seasons:
        earlier = [r for r in rounds if r["season"] < season]
        chosen[season] = _choose(earlier, SETTINGS) if earlier else DEFAULT_SETTING
        chosen_drift[season] = _choose(earlier, shared) if earlier else DEFAULT_SETTING
    for r in rounds:
        r["probs"]["model, season cars + drift"] = r["probs"][_key(chosen[r["season"]])]
        r["probs"]["model, development drift"] = r["probs"][_key(chosen_drift[r["season"]])]
        r["probs"]["model, static cars"] = r["probs"][_key(("shared cars", 0.0))]
    setting_now = _choose(rounds, SETTINGS) if rounds else DEFAULT_SETTING
    fixed = {_key(st_): mean(log_loss(r["probs"][_key(st_)].get(r["champ"], 0.0)) for r in rounds) for st_ in SETTINGS} \
        if rounds else {}
    return {"rounds": rounds, "checks": checks, "chosen": chosen, "chosen_drift": chosen_drift,
            "setting_now": setting_now, "fixed_log_loss": fixed}


MODELS = ("model, season cars + drift", "model, development drift", "model, static cars", "points leader wins",
          "in proportion to points")


def confident_wrong(rounds: list[dict], kind: str, model: str = MODELS[0], top: int = 10) -> list[tuple]:
    """(season, round, favourite, favourite's chance, champion, champion's chance) for the
    rounds where the model's favourite did NOT become champion, most confident first."""
    out = []
    for r in rounds:
        if r["kind"] != kind:
            continue
        probs = r["probs"][model]
        fav = max(probs, key=probs.get)
        if fav != r["champ"]:
            out.append((r["season"], r["round"], fav, probs[fav], r["champ"], probs.get(r["champ"], 0.0)))
    return sorted(out, key=lambda x: -x[3])[:top]


def summarise_titles(rounds: list[dict], kind: str) -> dict:
    """records / trajectories / calibration per model for one title."""
    records, trajectories, calibration = defaultdict(list), defaultdict(list), defaultdict(list)
    for r in rounds:
        if r["kind"] != kind:
            continue
        for name in MODELS:
            probs, champ = r["probs"][name], r["champ"]
            p = probs.get(champ, 0.0)
            fav = max(probs, key=probs.get) if probs else None
            brier = sum((probs.get(c, 0.0) - (c == champ)) ** 2 for c in set(probs) | {champ})
            records[name].append((r["season"], r["share"], p, float(fav == champ), brier))
            trajectories[(name, r["season"])].append(p)
            calibration[name] += [(probs.get(c, 0.0), c == champ) for c in r["candidates"]]
    return {"records": records, "trajectories": trajectories, "calibration": calibration}


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
    caches = strength_caches(raced)

    print("=== CHECK: champions from the stored points vs the real ones ===")
    champ = championship_backtest(weekends, caches, args.start_year, args.end_year)
    bad = 0
    for season, d, t in champ["checks"]:
        real = KNOWN_CHAMPIONS.get(season)
        dn, tn = driver_names.get(d, "?"), team_names.get(t, "?")
        ok = real is None or (real[0] in dn.lower() and real[1] in tn.lower())
        bad += not ok
        print(f"  {season}: drivers {dn}, constructors {tn}  {'ok' if ok else 'MISMATCH -- check the points data'}")

    print("\n=== RACE WINNER (each race, grid known; lower Brier / log loss = better) ===")
    race = race_backtest(weekends, caches, rates, max(args.start_year, 2019), args.end_year)
    print(f"  {race['races']} races; grid effect per season:")
    for car_model, coefs in race["grid_coef"].items():
        print(f"    {car_model:26} " + ", ".join(f"{s}: {c:.2f}" for s, c in sorted(coefs.items())))
    for name, row in race["models"].items():
        cells = []
        for metric in ("favourite_won", "brier_win", "brier_podium", "log_loss_win"):
            if metric in row:
                v = row[metric][0]
                cells.append(f"{metric} {v:.1%}" if metric == "favourite_won" else f"{metric} {v:.4f}")
        print(f"  {name:24} " + "  ".join(cells))
        for versus in ("production", "current"):
            for metric in ("brier_win", "brier_podium", "log_loss_win", "favourite_won"):
                key = f"{metric}_vs_{versus}"
                if key in row:
                    d, lo, hi = row[key]
                    lower_better = metric != "favourite_won"
                    verdict = ("BETTER" if (hi < 0 if lower_better else lo > 0) else
                               "WORSE" if (lo > 0 if lower_better else hi < 0) else "no clear difference")
                    print(f"      vs {versus:10} {metric:14} {d:+.4f} [{lo:+.4f}, {hi:+.4f}]  {verdict}")

    print("\n  (car model, drift) chosen walk-forward per season: "
          + ", ".join(f"{k}: {v[0]} / {v[1]}" for k, v in sorted(champ["chosen"].items()))
          + f"; for the projection: {champ['setting_now']}")
    print("  drift chosen walk-forward with shared cars (current version): "
          + ", ".join(f"{k}: {v[1]}" for k, v in sorted(champ["chosen_drift"].items())))
    print("  every setting fixed for all seasons (title log loss, both titles; for reference only):")
    for key, ll in sorted(champ["fixed_log_loss"].items(), key=lambda kv: kv[1]):
        print(f"    {key:40} {ll:.3f}")
    for kind in ("drivers", "constructors"):
        summary = summarise_titles(champ["rounds"], kind)
        print(f"\n=== {kind.upper()} CHAMPIONSHIP ({args.start_year}-{args.end_year}, every round) ===")
        print(f"  {'stage':16} {'model':26} {'chance given to champion':>25} {'favourite right':>16} {'log loss':>9} {'Brier':>7}")
        for lo_s, hi_s, label in STAGES:
            for name in MODELS:
                rows = [r for r in summary["records"][name] if lo_s <= r[1] < hi_s]
                if rows:
                    print(f"  {label:16} {name:26} {mean(r[2] for r in rows):>25.1%} {mean(r[3] for r in rows):>16.1%} "
                          f"{mean(log_loss(r[2]) for r in rows):>9.3f} {mean(r[4] for r in rows):>7.3f}")
        for name in MODELS:
            rows = summary["records"][name]
            moves = [abs(b - a) for (n, s), traj in summary["trajectories"].items() if n == name
                     for a, b in zip(traj, traj[1:])]
            print(f"  all rounds   {name:26} log loss {mean(log_loss(r[2]) for r in rows):.3f}  Brier "
                  f"{mean(r[4] for r in rows):.3f}  champion's chance moves {mean(moves):.1%}/round (largest {max(moves):.0%})")
        for name in MODELS[:3]:
            print(f"  calibration ({name}): when we said X, they became champion Y")
            for label, n, said, happened in calibration_rows(summary["calibration"][name]):
                print(f"    {label:>9}  n={n:5}  said {said:6.1%}  happened {happened:6.1%}")
        nm = driver_names if kind == "drivers" else team_names
        print(f"  most confident wrong calls ({MODELS[0]}):")
        for season, rnd, fav, p_fav, ch, p_ch in confident_wrong(champ["rounds"], kind):
            print(f"    {season} before round {rnd:2}: {nm.get(fav, fav):22} {p_fav:6.1%}   "
                  f"champion {nm.get(ch, ch):22} {p_ch:6.1%}")

    print(f"\n=== {args.season} PROJECTION (data up to today) ===")
    season_w = [w for w in weekends if w.season == args.season]
    done = [w for w in season_w if w.raced]
    remaining = [w for w in season_w if not w.raced]
    if done:
        lineup = {r.driver_id: r.team_id for r in done[-1].race}
        d_pts, t_pts = standings(done, args.season)
        current_drift = _choose(champ["rounds"], [("shared cars", d) for d in DRIFTS])[1] if champ["rounds"] else 0.06
        for label_v, (car_model, drift_now) in (("chosen setting", champ["setting_now"]),
                                                ("current version", ("shared cars", current_drift))):
            st = caches[car_model].before(len(raced), done[-1].era, args.season)
            proj = project_championship(st, lineup, remaining, driver_points=d_pts, team_points=t_pts,
                                        dnf=dnf_rate(raced, done[-1].era), drift=drift_now)
            print(f"  {label_v}: {car_model}, drift {drift_now} -- after round {done[-1].round}, "
                  f"{len(remaining)} weekends left")
            for label, probs, now, exp, nm in (("drivers", proj.p_driver_title, d_pts, proj.expected_driver_points, driver_names),
                                               ("constructors", proj.p_team_title, t_pts, proj.expected_team_points, team_names)):
                print(f"    {label}:")
                for k in sorted(probs, key=lambda k: -probs[k])[:6]:
                    print(f"      {nm.get(k, k):24} title {probs[k]:6.1%}   points now {now.get(k, 0):5.0f}   expected final {exp.get(k, 0):5.0f}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
