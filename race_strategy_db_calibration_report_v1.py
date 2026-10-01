"""Read-only inspection report for race-strategy calibration inputs.

This script is intentionally an inspection/calibration report, not a production
strategy predictor. It reads the existing database through the application's
SQLAlchemy session, reports coverage and warnings, and calibrates only the
observation classes that are currently available from the adapter.

It does not write to the database and does not fabricate unavailable pit-stop or
absolute-pace inputs.

Usage:
    python race_strategy_db_calibration_report_v1.py
    python race_strategy_db_calibration_report_v1.py --list-eras
    python race_strategy_db_calibration_report_v1.py --era <stored-era> --start-year 2017 --end-year 2026
"""

from __future__ import annotations

import argparse
import math
from typing import Any

from race_strategy_calibration_v1 import calibrate_event_hazards, calibrate_tyre_degradation
from race_strategy_data_adapter_v1 import load_calibration_dataset, load_regulation_eras


def _format_float(value: float) -> str:
    if not math.isfinite(value):
        return "n/a"
    return f"{value:.6f}"


def build_report(dataset: Any) -> str:
    """Render a deterministic human-readable calibration coverage report."""
    lines = [
        "Race Strategy Calibration v1 — DB Inspection",
        "=" * 48,
        "",
        "Observation coverage",
        f"  tyre observations : {len(dataset.tyre_observations)}",
        f"  event observations: {len(dataset.event_observations)}",
        f"  pit observations  : {len(dataset.pit_observations)}",
        f"  pace observations : {len(dataset.pace_observations)}",
        "",
    ]

    if dataset.tyre_observations:
        tyre_inputs, tyre_warnings = calibrate_tyre_degradation(dataset.tyre_observations)
        lines.append("Tyre degradation estimates")
        if tyre_inputs:
            for compound in sorted(tyre_inputs):
                dist = tyre_inputs[compound]
                lines.append(
                    f"  {compound:<14} mean={_format_float(dist.mean)}s/lap  "
                    f"std={_format_float(dist.std)}  n="
                    f"{sum(1 for row in dataset.tyre_observations if row.compound.upper() == compound)}"
                )
        else:
            lines.append("  unavailable: no usable within-stint tyre degradation estimates")
        if tyre_warnings:
            lines.append("  warnings:")
            lines.extend(f"    - {warning}" for warning in tyre_warnings)
        lines.append("")
    else:
        lines.extend(["Tyre degradation estimates", "  unavailable: no usable tyre observations", ""])

    if dataset.event_observations:
        hazards, event_warnings = calibrate_event_hazards(dataset.event_observations)
        lines.append("Race-event hazards (posterior means)")
        for key in sorted(hazards):
            lines.append(f"  {key:<32} {_format_float(hazards[key])}/lap")
        if event_warnings:
            lines.append("  warnings:")
            lines.extend(f"    - {warning}" for warning in event_warnings)
        lines.append("")
    else:
        lines.extend(["Race-event hazards", "  unavailable: no event observations", ""])

    lines.append("Explicitly unavailable inputs")
    if dataset.pit_observations:
        lines.append(f"  pit observations present: {len(dataset.pit_observations)}")
    else:
        lines.append("  pit service/pit-lane observations: unavailable")
    if dataset.pace_observations:
        lines.append(f"  pace observations present: {len(dataset.pace_observations)}")
    else:
        lines.append("  absolute pace observations: unavailable")

    if dataset.warnings:
        lines.extend(["", "Adapter warnings"])
        lines.extend(f"  - {warning}" for warning in dataset.warnings)

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect real DB coverage for race-strategy calibration v1")
    parser.add_argument("--era", default=None, help="Optional regulation_era filter")
    parser.add_argument("--list-eras", action="store_true", help="List distinct stored regulation_era labels and exit")
    parser.add_argument("--start-year", type=int, default=2017)
    parser.add_argument("--end-year", type=int, default=2026)
    parser.add_argument("--min-stint-laps", type=int, default=5)
    args = parser.parse_args()

    if args.start_year > args.end_year:
        raise SystemExit("--start-year cannot be greater than --end-year")
    if args.min_stint_laps < 2:
        raise SystemExit("--min-stint-laps must be at least 2")
    if args.list_eras and args.era is not None:
        raise SystemExit("--list-eras cannot be combined with --era")

    from app.database import SessionLocal

    db = SessionLocal()
    try:
        if args.list_eras:
            eras = load_regulation_eras(db, start_year=args.start_year, end_year=args.end_year)
            print("Stored regulation_era values")
            print("=" * 29)
            if eras:
                for era in eras:
                    print(f"  {era}")
            else:
                print("  (none)")
            return

        dataset = load_calibration_dataset(
            db,
            era=args.era,
            start_year=args.start_year,
            end_year=args.end_year,
            min_stint_laps=args.min_stint_laps,
        )
        print(build_report(dataset))
    finally:
        db.close()


if __name__ == "__main__":
    main()
