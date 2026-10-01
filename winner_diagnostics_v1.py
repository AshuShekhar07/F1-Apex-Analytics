"""Deep check of the race-winner model: where it goes wrong, and which fixes actually help.

Every race from 2019 is predicted only from the races before it (walk-forward), before and
after qualifying, exactly as production does (strength model + grid effect + win calibration).

PART 1  WHERE THE MISSES COME FROM
        each race the favourite did not win gets the first matching cause:
          favourite retired / wet race / winner came from P6 or further back /
          favourite's teammate won / first races under new rules / another team was faster
        plus the favourite's record by situation, how often the winner was in our top 3,
        and whether the podium chances are calibrated

PART 2  CANDIDATE FIXES, each judged against production on the same races
          half-life 4 / 6 / 12   how fast old races fade (production: 8)
          out-of-position grid   a driver starting 3+ places behind his qualifying (penalty,
                                 pit lane) is treated as starting between the two (w = 0.25 /
                                 0.5 of the way back) -- 2026 Monza: Antonelli won from the back
                                 with 2%
          practice pace          strength + beta x practice long-run pace (FP2, else FP1),
                                 beta chosen on earlier seasons
          same-weekend sprint    on sprint weekends the sprint has been run before the Grand
                                 Prix: include its order in the strengths after qualifying
        Run 1 (2026-10): out-of-position w=0.5 PASSED and is now production (the baseline here);
        half-life 4/6/12 and practice pace did not. --all reruns every candidate.
        Run 2: same-weekend sprint no clear difference (29 sprint weekends: log loss -0.016
        [-0.044, +0.009]) -> not used. Podium sharpening (winner_prediction_v1 --backtest)
        halved the podium calibration error but its log loss gain was not clear (-0.0026
        [-0.0054, +0.0004]) -> kept off by its gate.
        Each variant gets its own walk-forward win calibration, then winner log loss, win
        Brier and favourite-won are compared with production, CI resampling races.
        Promote only BETTER somewhere and WORSE nowhere.

    python winner_diagnostics_v1.py > winner_diagnostics.txt 2>&1
"""

from __future__ import annotations

import argparse
import math
import os
from collections import defaultdict
from dataclasses import dataclass, field
from statistics import mean

import numpy as np
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

import championship_model_v1 as cm
from winner_prediction_v1 import MIN_CALIBRATION_RACES, fit_power, sharpen

SIMS = 3000
STAGES = ("pre_qualifying", "post_qualifying")
HALF_LIVES = (4.0, 6.0, 12.0)
OUT_OF_POSITION = 3                    # grid - qualifying position
RECOVERY_WEIGHTS = (0.25, 0.5)
PRACTICE_BETAS = (0.0, 25.0, 50.0, 100.0, 200.0, 400.0)


@dataclass
class Case:
    w: cm.Weekend
    idx: int
    dnf: float
    winner: int
    quali: dict[int, int] = field(default_factory=dict)
    practice: dict[int, float] = field(default_factory=dict)
    wet: bool = False
    era_races_before: int = 0

    @property
    def grid(self) -> dict[int, int]:
        n = len(self.w.race)
        return {r.driver_id: (r.grid or n) for r in self.w.race}


def load_cases(db, weekends, start: int) -> list[Case]:
    from race_practice_pace_v1 import load_practice_gaps

    raced = [w for w in weekends if w.raced]
    quali: dict[int, dict[int, int]] = defaultdict(dict)
    for rid, d, p in db.execute(text("""
        SELECT s.race_id, re.driver_id, q.final_position FROM qualifying_results q
        JOIN sessions s ON s.id = q.session_id AND s.session_type = 'Q'
        JOIN race_entries re ON re.id = q.race_entry_id WHERE q.final_position IS NOT NULL""")):
        quali[int(rid)][int(d)] = int(p)
    wet = {int(r) for r in db.execute(text("""
        SELECT s.race_id FROM sessions s JOIN session_weather sw ON sw.session_id = s.id
        WHERE s.session_type = 'R' AND sw.rainfall""")).scalars()}
    cases = []
    for idx, w in enumerate(raced):
        if w.season < start or idx < cm.MIN_HISTORY:
            continue
        winner = next((r.driver_id for r in w.race if r.finish == 1), None)
        if winner is None:
            continue
        try:
            gaps, _ = load_practice_gaps(db, w.race_id)
        except Exception:                       # a practice session without usable laps
            gaps = {}
        cases.append(Case(w, idx, cm.dnf_rate(raced[:idx], w.era), winner, quali.get(w.race_id, {}), gaps,
                          w.race_id in wet, sum(x.era == w.era for x in raced[:idx])))
    return cases


def probabilities(s: np.ndarray, drivers: list[int], dnf: float, *, sims: int = SIMS) -> tuple[dict, dict]:
    pos = cm.simulate_order(s, dnf, np.random.default_rng(7), sims)
    finished = pos > 0
    return ({d: float(np.mean(pos[:, i] == 1)) for i, d in enumerate(drivers)},
            {d: float(np.mean(finished[:, i] & (pos[:, i] <= 3))) for i, d in enumerate(drivers)})


class Model:
    """Production strength model with switchable fixes; raw (uncalibrated) probabilities."""

    def __init__(self, raced, *, half_life=None, recovery=cm.RECOVERY_WEIGHT, practice=False, sprint=False):
        self.raced, self.half_life, self.recovery, self.practice = raced, half_life, recovery, practice
        self.sprint = sprint
        self.strengths_for = cm._cached_strengths(cm.RACE_WINTER_SD, half_life)
        self.grid_coef: dict[int, float] = {}
        self.beta: dict[int, float] = {}

    def coef(self, case: Case) -> float:
        season = case.w.season
        if season not in self.grid_coef:
            before = self.raced[:case.idx]
            pre = [x for x in before if x.season < season] or before
            self.grid_coef[season] = cm.fit_grid_coef(pre, self.strengths_for)
        return self.grid_coef[season]

    def base(self, case: Case, stage: str) -> tuple[list[int], np.ndarray]:
        if self.sprint and stage == "post_qualifying" and case.w.sprint:
            from dataclasses import replace
            # this weekend's sprint (already run before the Grand Prix) as the newest result
            st = cm.fit_strengths([*self.raced[:case.idx], replace(case.w, race=[])], era=case.w.era,
                                  winter_sd=cm.RACE_WINTER_SD, season=case.w.season, half_life=self.half_life)
        else:
            st = self.strengths_for(self.raced[:case.idx], case.w)
        drivers = [r.driver_id for r in case.w.race]
        s = np.array([st.of(r.driver_id, r.team_id) for r in case.w.race])
        if stage == "post_qualifying":
            grid = case.grid
            eff = []
            for d in drivers:
                g, q = grid[d], case.quali.get(d)
                if self.recovery is not None and q and g - q >= OUT_OF_POSITION:
                    g = q + self.recovery * (g - q)
                eff.append(g)
            s = s - self.coef(case) * np.log(np.array(eff, dtype=float))
        return drivers, s

    def practice_term(self, case: Case, drivers: list[int]) -> np.ndarray:
        gaps = case.practice
        if not gaps:
            return np.zeros(len(drivers))
        fill = float(np.median(list(gaps.values())))
        g = np.array([gaps.get(d, fill) for d in drivers])
        return -(g - g.mean())

    def raw(self, case: Case, stage: str, beta: float = 0.0) -> tuple[dict, dict]:
        drivers, s = self.base(case, stage)
        if beta:
            s = s + beta * self.practice_term(case, drivers)
        return probabilities(s, drivers, case.dnf)


def evaluate(model: Model, cases: list[Case], stage: str) -> list[dict]:
    """Per race: raw p_win / p_podium (with the walk-forward practice beta when enabled)."""
    out = []
    by_beta: dict[float, dict[int, dict]] = defaultdict(dict)
    for c in cases:
        if model.practice:
            for b in PRACTICE_BETAS:
                by_beta[b][c.w.race_id] = model.raw(c, stage, b)
        else:
            by_beta[0.0][c.w.race_id] = model.raw(c, stage)
    for c in cases:
        beta = 0.0
        if model.practice:
            earlier = [x for x in cases if x.w.season < c.w.season and x.practice]
            if len(earlier) >= MIN_CALIBRATION_RACES:
                beta = max(PRACTICE_BETAS, key=lambda b: sum(
                    math.log(max(by_beta[b][x.w.race_id][0].get(x.winner, 0.0), 1e-4)) for x in earlier))
        p_win, p_pod = by_beta[beta][c.w.race_id]
        out.append({"case": c, "p_win": p_win, "p_podium": p_pod, "beta": beta})
    return out


def calibrate(rows: list[dict]) -> None:
    """Walk-forward win sharpening, as production: k fitted on earlier seasons."""
    for r in rows:
        earlier = [(x["p_win"], x["case"].winner) for x in rows if x["case"].w.season < r["case"].w.season]
        k = fit_power(earlier) if len(earlier) >= MIN_CALIBRATION_RACES else 1.0
        r["p"] = sharpen(r["p_win"], k)


def metrics(r: dict) -> dict:
    p, c = r["p"], r["case"]
    ranked = sorted(p, key=lambda d: -p[d])
    return {"log_loss": -math.log(max(p.get(c.winner, 0.0), 1e-3)),
            "brier": mean((p[d] - (d == c.winner)) ** 2 for d in p),
            "favourite_won": float(ranked[0] == c.winner),
            "winner_top3": float(c.winner in ranked[:3])}


def compare(rows: list[dict], base: list[dict], label: str, only=None) -> list[str]:
    from race_form_signal_v1 import race_clustered

    out, verdicts = [], []
    pairs = [(a, b) for a, b in zip(rows, base) if only is None or only(a["case"])]
    if not pairs:
        return [f"    {label}: no races"]
    for metric, lower_better in (("log_loss", True), ("brier", True), ("favourite_won", False), ("winner_top3", False)):
        diffs = {a["case"].w.race_id: [metrics(a)[metric] - metrics(b)[metric]] for a, b in pairs}
        d, lo, hi = race_clustered(diffs)
        better = hi < 0 if lower_better else lo > 0
        worse = lo > 0 if lower_better else hi < 0
        verdicts.append("better" if better else "worse" if worse else "same")
        out.append(f"{metric} {mean(metrics(a)[metric] for a, _ in pairs):.4f} ({d:+.4f} [{lo:+.4f}, {hi:+.4f}])")
    verdict = ("PASS -- better somewhere, worse nowhere" if "better" in verdicts and "worse" not in verdicts
               else "FAIL -- worse somewhere" if "worse" in verdicts else "no clear difference")
    return [f"    {label} ({len(pairs)} races): {verdict}", "      " + "  ".join(out)]


def miss_cause(c: Case, favourite: int) -> str:
    fav = next(r for r in c.w.race if r.driver_id == favourite)
    win = next(r for r in c.w.race if r.driver_id == c.winner)
    if not fav.classified:
        return "favourite retired / did not finish"
    if c.wet:
        return "wet race"
    if (win.grid or 99) >= 6:
        return "winner came from P6 or further back"
    if fav.team_id == win.team_id:
        return "favourite's teammate won"
    if c.era_races_before < 3:
        return "first races under new rules"
    return "another team was faster on the day"


def part1(rows_by_stage: dict[str, list[dict]]) -> None:
    print("=== PART 1: WHERE THE MISSES COME FROM (production model, walk-forward) ===")
    for stage, rows in rows_by_stage.items():
        m = [metrics(r) for r in rows]
        print(f"\n  {stage.upper()}: {len(rows)} races, favourite won {mean(x['favourite_won'] for x in m):.1%}, "
              f"winner in our top 3 {mean(x['winner_top3'] for x in m):.1%}, winner log loss {mean(x['log_loss'] for x in m):.3f}")
        causes = defaultdict(list)
        for r in rows:
            fav = max(r["p"], key=r["p"].get)
            if fav != r["case"].winner:
                causes[miss_cause(r["case"], fav)].append(r)
        misses = sum(len(v) for v in causes.values())
        print(f"  {misses} misses by cause:")
        for cause, rs in sorted(causes.items(), key=lambda kv: -len(kv[1])):
            given = mean(r["p"].get(r["case"].winner, 0) for r in rs)
            print(f"    {cause:40} {len(rs):4} ({len(rs) / misses:5.1%})   chance we gave the winner {given:5.1%}")
        print("  favourite's record by situation:")
        groups = {
            "dry race": lambda c: not c.wet, "wet race": lambda c: c.wet,
            "sprint weekend": lambda c: c.w.sprint_weekend, "normal weekend": lambda c: not c.w.sprint_weekend,
            "first 3 races under new rules": lambda c: c.era_races_before < 3,
            "practice long runs available": lambda c: bool(c.practice), "no usable practice": lambda c: not c.practice,
            "out-of-position starter in the race": lambda c: any(
                g - c.quali.get(d, g) >= OUT_OF_POSITION for d, g in c.grid.items()),
        }
        for name, test in groups.items():
            sel = [metrics(r) for r in rows if test(r["case"])]
            if sel:
                print(f"    {name:38} {len(sel):4} races: favourite won {mean(x['favourite_won'] for x in sel):5.1%}, "
                      f"winner in top 3 {mean(x['winner_top3'] for x in sel):5.1%}")
        if stage == "post_qualifying":
            print("  podium chances (every driver): when we said X, they finished on the podium Y")
            pairs = [(r["p_podium"][d], next(x for x in r["case"].w.race if x.driver_id == d).finish in (1, 2, 3))
                     for r in rows for d in r["p_podium"]]
            for lo, hi in ((0, .05), (.05, .15), (.15, .3), (.3, .5), (.5, .7), (.7, 1.01)):
                sel = [(p, y) for p, y in pairs if lo <= p < hi]
                if sel:
                    print(f"    {lo:4.0%}-{min(hi, 1):4.0%}  n={len(sel):5}  said {mean(p for p, _ in sel):5.1%}  "
                          f"happened {mean(y for _, y in sel):5.1%}")


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start-year", type=int, default=2019)
    parser.add_argument("--all", action="store_true", help="rerun every candidate, not only the open ones")
    args = parser.parse_args()
    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as db:
        weekends = cm.load_weekends(db, start_year=2018)
        cases = load_cases(db, weekends, args.start_year)
    raced = [w for w in weekends if w.raced]
    print(f"{len(cases)} races from {args.start_year}, {sum(bool(c.practice) for c in cases)} with practice long runs, "
          f"{sum(c.wet for c in cases)} wet\n")

    production = Model(raced)
    base = {stage: evaluate(production, cases, stage) for stage in STAGES}
    for rows in base.values():
        calibrate(rows)
    part1(base)

    print("\n=== PART 2: CANDIDATE FIXES vs PRODUCTION (same races, walk-forward, own win calibration) ===")
    print("  metric mean (difference to production [90% CI]); log loss / Brier lower = better")
    variants = [("same-weekend sprint", Model(raced, sprint=True), ("post_qualifying",), "sprint")]
    if args.all:
        variants += [(f"half-life {hl:g} races", Model(raced, half_life=hl), STAGES, None) for hl in HALF_LIVES]
        variants += [("no out-of-position grid (previous production)", Model(raced, recovery=None),
                      ("post_qualifying",), "oop")]
        variants += [(f"out-of-position grid w={w}", Model(raced, recovery=w), ("post_qualifying",), "oop")
                     for w in RECOVERY_WEIGHTS if w != cm.RECOVERY_WEIGHT]
        variants += [("practice pace", Model(raced, practice=True), STAGES, "practice")]
    for label, model, stages, subgroup in variants:
        print(f"\n  {label}:")
        for stage in stages:
            rows = evaluate(model, cases, stage)
            calibrate(rows)
            for line in compare(rows, base[stage], stage):
                print(line)
            if subgroup == "oop":
                for line in compare(rows, base[stage], f"{stage}, races with an out-of-position starter",
                                    only=lambda c: any(g - c.quali.get(d, g) >= OUT_OF_POSITION
                                                       for d, g in c.grid.items())):
                    print(line)
            if subgroup == "sprint":
                for line in compare(rows, base[stage], f"{stage}, sprint weekends only", only=lambda c: bool(c.w.sprint)):
                    print(line)
            if subgroup == "practice":
                betas = sorted({(r["case"].w.season, r["beta"]) for r in rows})
                print(f"      practice beta chosen per season: {dict(betas)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
