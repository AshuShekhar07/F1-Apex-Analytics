"""Compare two prediction-scoreboard runs race by race (paired, races resampled).

For changes that cannot run side by side in one scoreboard run -- a data rule such as which
races count as wet changes every model's inputs. Both runs score the same races (the
scoreboard scores wet and dry races alike), so each headline metric is compared on the
races both runs scored, with a 90% interval that resamples races.

    python prediction_scoreboard_v1.py --dump base.csv
    APEX21_WET_RULE=tyres python prediction_scoreboard_v1.py --dump tyres.csv
    python scoreboard_compare_v1.py base.csv tyres.csv
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from typing import Sequence

from prediction_scoreboard_v1 import HEADLINE, LOWER_IS_BETTER, DriverScore, load_dump, race_metrics
from race_form_signal_v1 import race_clustered


def compare(base: Sequence[DriverScore], new: Sequence[DriverScore], model: str = "blend") -> list[tuple]:
    """(metric, base value, new value, (diff, lo, hi), verdict) on the races both runs scored."""
    def by_race(scores):
        out = defaultdict(list)
        for s in scores:
            if s.model == model:
                out[s.race_id].append(s)
        return out
    a, b = by_race(base), by_race(new)
    shared = sorted(set(a) & set(b))
    rows = []
    for metric in HEADLINE:
        va, vb, diffs = {}, {}, {}
        for r in shared:
            x, y = race_metrics(a[r])[metric], race_metrics(b[r])[metric]
            if x is not None and y is not None:
                va[r], vb[r], diffs[r] = [x], [y], [y - x]
        if not diffs:
            continue
        d, lo, hi = race_clustered(diffs)
        lower = metric in LOWER_IS_BETTER
        better = hi < 0 if lower else lo > 0
        worse = lo > 0 if lower else hi < 0
        rows.append((metric, race_clustered(va)[0], race_clustered(vb)[0], (d, lo, hi),
                     "BETTER" if better else "WORSE" if worse else "no clear difference"))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("base")
    parser.add_argument("new")
    parser.add_argument("--model", default="blend")
    args = parser.parse_args()
    base, new = load_dump(args.base), load_dump(args.new)
    shared = {s.race_id for s in base if s.model == args.model} & {s.race_id for s in new if s.model == args.model}
    print(f"=== {args.new} vs {args.base}, model {args.model}, {len(shared)} races scored by both ===")
    print("metric               base      new       new - base [90% CI]")
    for metric, va, vb, (d, lo, hi), verdict in compare(base, new, args.model):
        print(f"{metric:20} {va:8.4f}  {vb:8.4f}  {d:+.4f} [{lo:+.4f}, {hi:+.4f}]  {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
