import apex_report


def test_update_summary_reads_progress_and_the_quality_block(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "update_2026.log").write_text(
        "noise\n[15/16] done in 3s\n[16/16] done in 2s\n\n=== DATA QUALITY 2026 ===\n"
        "  R13 Monza                  raced     ok\n\n=== FORECASTS ===\n  R13 Monza: forecast stored\n"
        "\n=== SCOREBOARD vs x ===\nlots of scoreboard\n")
    lines = apex_report.update_summary(2026)
    assert lines[0] == "  loaders: step 16/16 done in 2s"
    assert any("R13 Monza" in l and "raced" in l for l in lines)
    assert any("forecast stored" in l for l in lines)
    assert not any("lots of scoreboard" in l for l in lines)


def test_update_summary_reports_a_stopped_run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "update_2026.log").write_text("[4/16] FAILED (exit 1) in 9s\nStopped. Fix the error above, "
                                              "then resume with --from-step 4.\n")
    lines = apex_report.update_summary(2026)
    assert "FAILED" in lines[0] and "--from-step 4" in lines[1]
    assert apex_report.update_summary(2025)[0].startswith("  (no update_2025.log")
