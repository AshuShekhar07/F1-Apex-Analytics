"""Walk-forward gate for tyre model v3 and its strategy optimiser.

For every dry target race, the tyre model is fitted on races strictly before it.

Gate 1 (tyre model): on the target race's clean laps, after giving every car its
own pace level, does the model's compound offset + wear shape predict lap times
better than a FLAT model (no tyre effect)? Also reported: offsets only (no wear).
PASS when the race-clustered 90% CI of (model RMSE - flat RMSE) is entirely < 0.

Gate 2 (optimiser): the optimiser's best strategy (one per race) is scored against
every classified car's REAL stints, next to the precedent hierarchy (method C of
race_strategy_prediction_backtest_v1). PASS when its stop-count accuracy is not
clearly worse: CI lower bound of (optimiser - hierarchy) above -0.05.

The optimiser is only offered in forecasts when BOTH gates pass; the switch is
race_forecast_v1.OPTIMISER_ENABLED and is flipped by hand, never automatically.

    python race_tyre_model_backtest_v3.py --start-year 2024 --end-year 2025
"""

from __future__ import annotations

import argparse
import os
from collections import defaultdict
from dataclasses import dataclass
from math import sqrt
from typing import Any, Sequence

from dotenv import load_dotenv
from sqlalchemy import create_engine

from race_strategy_backtest_v2 import race_clustered_ci
from race_strategy_prediction_backtest_v1 import Score, score
from race_strategy_timing_v1 import load_car_strategies, predict_strategy
from race_tyre_model_v3 import fit_for_race, load_tyre_observations, optimise, residuals

OPTIMISER_TOLERANCE = -0.05


@dataclass(frozen=True)
class RaceFit:
    race_id: int
    source: str
    rmse_flat: float
    rmse_offsets: float
    rmse_model: float


def _rmse(values: Sequence[float]) -> float:
    return sqrt(sum(v * v for v in values) / len(values))


def run(db: Any, *, start_year: int, end_year: int) -> tuple[list[RaceFit], list[Score], list[str]]:
    from race_strategy_pit_loss_v2 import estimate_pit_loss, load_observations as load_pit_losses

    obs = load_tyre_observations(db, start_year=2018, end_year=end_year)
    cars = load_car_strategies(db, start_year=2018, end_year=end_year)
    pit_obs = load_pit_losses(db, start_year=2018, end_year=end_year)
    notes: list[str] = []
    targets = defaultdict(list)
    for o in obs:
        if start_year <= o.race_date.year <= end_year:
            targets[(o.race_date, o.race_id)].append(o)
    fits: list[RaceFit] = []
    scores: list[Score] = []
    for (race_date, race_id), laps in sorted(targets.items()):
        era, track = laps[0].regulation_era, laps[0].track_id
        model = fit_for_race(obs, track_id=track, regulation_era=era, before_date=race_date)
        if model is None:
            notes.append(f"race {race_id}: not enough earlier {era} laps to fit a tyre model")
            continue
        usable = [o for o in laps if o.compound in model.params]
        fits.append(RaceFit(
            race_id, model.source,
            _rmse(residuals(lambda o: 0.0, usable)),
            _rmse(residuals(lambda o: model.params[o.compound][0], usable)),
            _rmse(residuals(lambda o: model.shape(o.compound, o.tyre_age), usable)),
        ))
        race_cars = [c for c in cars if c.race_id == race_id and not c.wet]
        if not race_cars:
            continue
        loss = estimate_pit_loss(pit_obs, track_id=track, regulation_era=era, condition="green",
                                 before_date=race_date)
        if loss is None:
            notes.append(f"race {race_id}: no green pit-loss evidence, optimiser not scored")
            continue
        total_laps = race_cars[0].race_laps
        ranked = optimise(model, total_laps=total_laps, pit_loss=loss.median_seconds)
        best = ranked[0][0] if ranked else None
        history = [c for c in cars if c.race_date < race_date]
        for car in race_cars:
            scores.append(score(race_id, "optimiser", best, car))
            hierarchy = predict_strategy(history, track_id=track, regulation_era=era, grid=car.grid,
                                         total_laps=total_laps).strategy
            scores.append(score(race_id, "hierarchy", hierarchy, car))
        print(f"race {race_id} ({race_date}): {model.source}, optimiser -> {best.name if best else None}", flush=True)
    return fits, scores, notes


class _Value:
    def __init__(self, race_id: int, value: float):
        self.race_id, self.value = race_id, value


def paired(scores: Sequence[Score], metric: str, a: str = "optimiser", b: str = "hierarchy"):
    xs = [s for s in scores if s.method == a]
    ys = [s for s in scores if s.method == b]
    diffs = [_Value(x.race_id, getattr(x, metric) - getattr(y, metric)) for x, y in zip(xs, ys)
             if getattr(x, metric) is not None and getattr(y, metric) is not None]
    return race_clustered_ci(diffs, lambda r: r.value)


def gates(fits: Sequence[RaceFit], scores: Sequence[Score]) -> dict:
    model_vs_flat = race_clustered_ci([_Value(f.race_id, f.rmse_model - f.rmse_flat) for f in fits], lambda r: r.value)
    stop_diff = paired(scores, "stop_count_hit") if scores else (float("nan"),) * 3
    gate1 = model_vs_flat[2] < 0
    gate2 = stop_diff[1] > OPTIMISER_TOLERANCE
    return {"model_vs_flat": model_vs_flat, "stop_count_diff": stop_diff,
            "gate1": gate1, "gate2": gate2, "enable_optimiser": gate1 and gate2}


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start-year", type=int, default=2024)
    parser.add_argument("--end-year", type=int, default=2025)
    args = parser.parse_args()
    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as db:
        fits, scores, notes = run(db, start_year=args.start_year, end_year=args.end_year)
    for note in notes:
        print("note:", note)
    if not fits:
        print("No races scored.")
        return 1
    print(f"\n=== TYRE MODEL v3 GATE (dry races {args.start_year}-{args.end_year}, {len(fits)} races) ===")
    for name, key in (("flat (no tyre effect)", "rmse_flat"), ("compound offsets only", "rmse_offsets"),
                      ("offsets + wear (v3)", "rmse_model")):
        v = race_clustered_ci(fits, lambda f, k=key: getattr(f, k))
        print(f"lap-time RMSE  {name:24} {v[0]:.4f} s  [{v[1]:.4f}, {v[2]:.4f}]")
    g = gates(fits, scores)
    d = g["model_vs_flat"]
    print(f"v3 - flat      {d[0]:+.4f} s  [{d[1]:+.4f}, {d[2]:+.4f}]  gate 1: {'PASS' if g['gate1'] else 'FAIL'}")
    if scores:
        for metric in ("sequence_hit", "stop_count_hit", "first_stop_error", "first_stop_within_3"):
            o = race_clustered_ci([s for s in scores if s.method == "optimiser"], lambda s, m=metric: getattr(s, m))
            h = race_clustered_ci([s for s in scores if s.method == "hierarchy"], lambda s, m=metric: getattr(s, m))
            print(f"{metric:20} optimiser {o[0]:6.3f}   hierarchy {h[0]:6.3f}")
        s = g["stop_count_diff"]
        print(f"stop-count optimiser - hierarchy {s[0]:+.3f} [{s[1]:+.3f}, {s[2]:+.3f}]  "
              f"gate 2: {'PASS' if g['gate2'] else 'FAIL'}")
    print(f"\nVERDICT: {'ENABLE optimiser (set OPTIMISER_ENABLED = True)' if g['enable_optimiser'] else 'keep optimiser OFF'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
