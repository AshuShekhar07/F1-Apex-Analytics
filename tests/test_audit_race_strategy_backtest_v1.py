import csv
from pathlib import Path

from audit_race_strategy_backtest_v1 import audit_rows


def _write(path: Path, rows):
    fields = [
        "race_id",
        "year",
        "selected_strategy",
        "model_expected_finish",
        "model_p1_probability",
        "actual_finish_position",
        "baseline_name",
        "baseline_expected_finish",
        "baseline_distance_from_actual",
        "selected_distance_from_actual",
        "leakage_safe",
        "actual_strategy",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_audit_reports_strategy_and_probability_metrics(tmp_path):
    path = tmp_path / "results.csv"
    _write(
        path,
        [
            {
                "race_id": 1,
                "year": 2024,
                "selected_strategy": "MEDIUM → HARD [25]",
                "model_expected_finish": 2.0,
                "model_p1_probability": 0.60,
                "actual_finish_position": 1,
                "baseline_name": "MEDIUM → HARD [50%]",
                "baseline_expected_finish": 3.0,
                "baseline_distance_from_actual": 2.0,
                "selected_distance_from_actual": 1.0,
                "leakage_safe": True,
                "actual_strategy": "MEDIUM → HARD",
            },
            {
                "race_id": 2,
                "year": 2024,
                "selected_strategy": "MEDIUM → MEDIUM [30]",
                "model_expected_finish": 2.5,
                "model_p1_probability": 0.20,
                "actual_finish_position": 3,
                "baseline_name": "MEDIUM → HARD [50%]",
                "baseline_expected_finish": 2.0,
                "baseline_distance_from_actual": 1.0,
                "selected_distance_from_actual": 0.5,
                "leakage_safe": True,
                "actual_strategy": "MEDIUM → MEDIUM",
            },
        ],
    )
    report = audit_rows(path)
    assert report["rows"] == 2
    assert report["sequence_matches"] == 2
    assert report["sequence_match_rate"] == 1.0
    assert report["repeated_compound_selected"] == 1
    assert report["p1_brier"] == 0.20
    assert report["probability_bins"]["0.5-1.0"]["n"] == 1


def test_audit_accepts_safe_boolean_strings(tmp_path):
    path = tmp_path / "results.csv"
    _write(
        path,
        [{
            "race_id": 1,
            "year": 2024,
            "selected_strategy": "MEDIUM → HARD [25]",
            "model_expected_finish": 1.0,
            "model_p1_probability": 0.8,
            "actual_finish_position": 1,
            "baseline_name": "MEDIUM → HARD [50%]",
            "baseline_expected_finish": 2.0,
            "baseline_distance_from_actual": 1.0,
            "selected_distance_from_actual": 0.0,
            "leakage_safe": "True",
            "actual_strategy": "MEDIUM → HARD",
        }],
    )
    assert audit_rows(path)["unsafe_rows"] == 0
