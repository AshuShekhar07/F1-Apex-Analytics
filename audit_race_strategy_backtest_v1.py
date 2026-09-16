"""Audit report for the real dry-race strategy backtest v1.

Consumes only the CSV emitted by race_strategy_real_backtest_v1.py. The audit
never changes model inputs or selections. It checks strategy matching, finish
error decomposition, and empirical calibration of the reported P1 probabilities.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AuditRow:
    race_id: int
    year: int
    selected_strategy: str
    actual_strategy: str | None
    selected_sequence_match: bool | None
    selected_stop_l1_error: float | None
    selected_lap_window_error: float | None
    model_expected_finish: float
    baseline_expected_finish: float
    actual_finish_position: int
    model_p1_probability: float


def _bool_or_none(value: str) -> bool | None:
    if value == "":
        return None
    return value.strip().lower() == "true"


def _float_or_none(value: str) -> float | None:
    return None if value == "" else float(value)


def load_rows(path: str | Path) -> list[AuditRow]:
    with Path(path).open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return [
        AuditRow(
            race_id=int(row["race_id"]),
            year=int(row["year"]),
            selected_strategy=row["selected_strategy"],
            actual_strategy=row.get("actual_strategy") or None,
            selected_sequence_match=_bool_or_none(row.get("selected_sequence_match", "")),
            selected_stop_l1_error=_float_or_none(row.get("selected_stop_l1_error", "")),
            selected_lap_window_error=_float_or_none(row.get("selected_lap_window_error", "")),
            model_expected_finish=float(row["model_expected_finish"]),
            baseline_expected_finish=float(row["baseline_expected_finish"]),
            actual_finish_position=int(row["actual_finish_position"]),
            model_p1_probability=float(row["model_p1_probability"]),
        )
        for row in rows
    ]


def _strategy_sequence(name: str) -> tuple[str, ...]:
    sequence = name.split(" [", 1)[0]
    return tuple(part.strip() for part in sequence.split("→"))


def _calibration_bins(rows: list[AuditRow]) -> list[tuple[str, int, float, float]]:
    bins = ((0.00, 0.05), (0.05, 0.10), (0.10, 0.20), (0.20, 0.40), (0.40, 1.01))
    out: list[tuple[str, int, float, float]] = []
    for lo, hi in bins:
        selected = [r for r in rows if lo <= r.model_p1_probability < hi]
        if not selected:
            continue
        mean_pred = sum(r.model_p1_probability for r in selected) / len(selected)
        observed = sum(r.actual_finish_position == 1 for r in selected) / len(selected)
        out.append((f"{lo:.2f}-{min(hi, 1.0):.2f}", len(selected), mean_pred, observed))
    return out


def audit(rows: list[AuditRow]) -> dict[str, object]:
    if not rows:
        raise ValueError("CSV contains no scored rows")

    sequence_rows = [r for r in rows if r.selected_sequence_match is not None]
    stop_rows = [r.selected_stop_l1_error for r in rows if r.selected_stop_l1_error is not None]
    window_rows = [r.selected_lap_window_error for r in rows if r.selected_lap_window_error is not None]
    model_errors = [abs(r.model_expected_finish - r.actual_finish_position) for r in rows]
    baseline_errors = [abs(r.baseline_expected_finish - r.actual_finish_position) for r in rows]

    repeated_compound = sum(len(set(_strategy_sequence(r.selected_strategy))) < len(_strategy_sequence(r.selected_strategy)) for r in rows)
    actual_known = sum(r.actual_strategy is not None for r in rows)

    return {
        "rows": len(rows),
        "actual_strategy_available": actual_known,
        "sequence_match_rate": (sum(bool(r.selected_sequence_match) for r in sequence_rows) / len(sequence_rows) if sequence_rows else None),
        "mean_stop_l1_error": sum(stop_rows) / len(stop_rows) if stop_rows else None,
        "mean_lap_window_error": sum(window_rows) / len(window_rows) if window_rows else None,
        "model_mae": sum(model_errors) / len(model_errors),
        "baseline_mae": sum(baseline_errors) / len(baseline_errors),
        "repeated_compound_selected": repeated_compound,
        "repeated_compound_rate": repeated_compound / len(rows),
        "p1_mean": sum(r.model_p1_probability for r in rows) / len(rows),
        "p1_brier_score": sum((r.model_p1_probability - float(r.actual_finish_position == 1)) ** 2 for r in rows) / len(rows),
        "calibration_bins": _calibration_bins(rows),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit a race-strategy backtest CSV")
    parser.add_argument("csv", nargs="?", default="strategy_backtest_v1.csv")
    args = parser.parse_args()

    report = audit(load_rows(args.csv))
    print("=== STRATEGY BACKTEST AUDIT ===")
    for key in (
        "rows", "actual_strategy_available", "sequence_match_rate",
        "mean_stop_l1_error", "mean_lap_window_error", "model_mae",
        "baseline_mae", "repeated_compound_selected", "repeated_compound_rate",
        "p1_mean", "p1_brier_score",
    ):
        print(f"{key}={report[key]}")

    print("\nP1 calibration bins:")
    print("bin n mean_pred observed_win_rate")
    for label, n, mean_pred, observed in report["calibration_bins"]:  # type: ignore[misc]
        print(f"{label} {n} {mean_pred:.3f} {observed:.3f}")


if __name__ == "__main__":
    main()
