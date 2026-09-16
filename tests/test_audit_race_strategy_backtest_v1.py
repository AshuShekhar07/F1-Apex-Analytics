from audit_race_strategy_backtest_v1 import audit, load_rows


def test_audit_reports_calibration_and_repeated_compounds(tmp_path):
    path = tmp_path / "result.csv"
    path.write_text(
        "race_id,year,selected_strategy,model_expected_finish,model_p1_probability,actual_finish_position,baseline_name,baseline_expected_finish,baseline_distance_from_actual,selected_distance_from_actual,leakage_safe,actual_strategy,selected_sequence_match,selected_stop_l1_error,selected_lap_window_error\n"
        "1,2024,MEDIUM → MEDIUM [30],2.0,0.20,1,baseline,5.0,4.0,1.0,True,MEDIUM → HARD,False,3.0,3.0\n"
        "2,2025,MEDIUM → HARD [30],2.0,0.02,3,baseline,5.0,2.0,1.0,True,MEDIUM → HARD,True,0.0,0.0\n",
        encoding="utf-8",
    )
    report = audit(load_rows(path))
    assert report["rows"] == 2
    assert report["actual_strategy_available"] == 2
    assert report["repeated_compound_selected"] == 1
    assert report["sequence_match_rate"] == 0.5
    assert report["p1_mean"] == 0.11
    assert report["calibration_bins"]
