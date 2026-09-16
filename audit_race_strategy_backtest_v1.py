"""Audit report for the research race-strategy backtest CSV."""
from __future__ import annotations

import csv
import math
import sys
from pathlib import Path


def _bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _f(value: str) -> float:
    return float(value)


def _sequence(strategy: str) -> tuple[str, ...]:
    text = strategy.split("[")[0].strip()
    return tuple(part.strip() for part in text.split("→") if part.strip())


def audit_rows(path: str | Path) -> dict:
    path = Path(path)
    with path.open("r", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    if not rows:
        return {"rows": 0, "unsafe_rows": 0, "sequence_matches": 0, "sequence_match_rate": 0.0, "repeated_compound_selected": 0, "p1_brier": float("nan"), "probability_bins": {}}

    unsafe = sum(not _bool(r.get("leakage_safe", "false")) for r in rows)
    matches = 0
    repeated = 0
    brier_terms: list[float] = []
    bins = {
        "0.0-0.2": [],
        "0.2-0.5": [],
        "0.5-1.0": [],
    }

    for row in rows:
        selected = _sequence(row.get("selected_strategy", ""))
        actual = _sequence(row.get("actual_strategy", ""))
        if actual and selected == actual:
            matches += 1
        if len(selected) >= 2 and len(set(selected)) < len(selected):
            repeated += 1
        p = min(1.0, max(0.0, _f(row["model_p1_probability"])))
        y = 1.0 if int(row["actual_finish_position"]) == 1 else 0.0
        brier_terms.append((p - y) ** 2)
        if p < 0.2:
            bins["0.0-0.2"].append((p, y))
        elif p < 0.5:
            bins["0.2-0.5"].append((p, y))
        else:
            bins["0.5-1.0"].append((p, y))

    probability_bins = {}
    for name, values in bins.items():
        probability_bins[name] = {
            "n": len(values),
            "mean_predicted_p1": sum(v[0] for v in values) / len(values) if values else float("nan"),
            "observed_win_rate": sum(v[1] for v in values) / len(values) if values else float("nan"),
        }

    return {
        "rows": len(rows),
        "unsafe_rows": unsafe,
        "sequence_matches": matches,
        "sequence_match_rate": matches / len(rows),
        "repeated_compound_selected": repeated,
        "p1_brier": sum(brier_terms) / len(brier_terms),
        "probability_bins": probability_bins,
    }


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python audit_race_strategy_backtest_v1.py strategy_backtest_v1.csv")
    report = audit_rows(sys.argv[1])
    print("=== BACKTEST AUDIT ===")
    print(f"rows={report['rows']}")
    print(f"unsafe_rows={report['unsafe_rows']}")
    print(f"sequence_matches={report['sequence_matches']}")
    print(f"sequence_match_rate={report['sequence_match_rate']:.3f}")
    print(f"repeated_compound_selected={report['repeated_compound_selected']}")
    brier = report["p1_brier"]
    print(f"p1_brier={brier:.4f}" if math.isfinite(brier) else "p1_brier=nan")
    print("probability_bins:")
    for name, values in report["probability_bins"].items():
        if values["n"]:
            print(f"  {name}: n={values['n']} predicted={values['mean_predicted_p1']:.3f} observed={values['observed_win_rate']:.3f}")
        else:
            print(f"  {name}: n=0")


if __name__ == "__main__":
    main()
