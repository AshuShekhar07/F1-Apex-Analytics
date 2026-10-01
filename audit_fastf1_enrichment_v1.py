"""Coverage audit for the FastF1 enrichment layer v1.

Read-only. Reports how much of the requested enrichment is present in PostgreSQL.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text


def run_audit(engine, *, start_year: int, end_year: int) -> dict:
    with engine.connect() as conn:
        sessions = conn.execute(
            text(
                """
                SELECT s.id, s.session_type, r.season_year
                FROM sessions s
                JOIN races r ON r.id = s.race_id
                WHERE r.season_year BETWEEN :start_year AND :end_year
                  AND r.race_date IS NOT NULL
                """
            ),
            {"start_year": start_year, "end_year": end_year},
        ).mappings().all()

        session_counts = {}
        for row in sessions:
            key = str(row["session_type"])
            session_counts[key] = session_counts.get(key, 0) + 1

        lap_rows = conn.execute(
            text(
                """
                SELECT
                    COUNT(*) AS total,
                    COUNT(lap_start_time_seconds) AS lap_start,
                    COUNT(speed_i1_kmh) AS speed_i1,
                    COUNT(speed_i2_kmh) AS speed_i2,
                    COUNT(speed_fl_kmh) AS speed_fl,
                    COUNT(speed_st_kmh) AS speed_st,
                    COUNT(tyre_life_laps) AS tyre_life,
                    COUNT(fresh_tyre) AS fresh_tyre,
                    COUNT(track_status_code) AS track_status,
                    COUNT(position_on_track) AS position_on_track,
                    COUNT(pit_in_time_seconds) AS pit_in,
                    COUNT(pit_out_time_seconds) AS pit_out
                FROM laps l
                JOIN sessions s ON s.id = l.session_id
                JOIN races r ON r.id = s.race_id
                WHERE r.season_year BETWEEN :start_year AND :end_year
                """
            ),
            {"start_year": start_year, "end_year": end_year},
        ).mappings().one()

        lap_metadata_by_session = conn.execute(
            text(
                """
                SELECT
                    r.season_year,
                    s.session_type,
                    COUNT(*) AS total_laps,
                    COUNT(l.lap_start_time_seconds) AS lap_start,
                    COUNT(l.tyre_life_laps) AS tyre_life,
                    COUNT(l.fresh_tyre) AS fresh_tyre,
                    COUNT(l.speed_i1_kmh) AS speed_i1,
                    COUNT(l.track_status_code) AS track_status
                FROM laps l
                JOIN sessions s ON s.id = l.session_id
                JOIN races r ON r.id = s.race_id
                WHERE r.season_year BETWEEN :start_year AND :end_year
                GROUP BY r.season_year, s.session_type
                ORDER BY r.season_year, s.session_type
                """
            ),
            {"start_year": start_year, "end_year": end_year},
        ).mappings().all()

        tables = {}
        for table in (
            "session_weather_samples",
            "session_track_status_intervals",
            "session_race_control_messages",
            "lap_telemetry_summary",
            "track_corners",
        ):
            tables[table] = int(
                conn.execute(
                    text(f"SELECT COUNT(*) FROM {table}")
                ).scalar() or 0
            )

        telemetry_by_session = conn.execute(
            text(
                """
                SELECT s.session_type, COUNT(*) AS rows
                FROM lap_telemetry_summary t
                JOIN sessions s ON s.id = t.session_id
                JOIN races r ON r.id = s.race_id
                WHERE r.season_year BETWEEN :start_year AND :end_year
                GROUP BY s.session_type
                ORDER BY s.session_type
                """
            ),
            {"start_year": start_year, "end_year": end_year},
        ).mappings().all()

    result = {
        "start_year": start_year,
        "end_year": end_year,
        "sessions": len(sessions),
        "session_counts": session_counts,
        "lap_metadata": {key: int(value or 0) for key, value in lap_rows.items()},
        "lap_metadata_by_session": [
            {key: int(value) if key not in {"session_type"} else str(value) for key, value in row.items()}
            for row in lap_metadata_by_session
        ],
        "enrichment_table_rows": tables,
        "telemetry_rows_by_session_type": {
            str(row["session_type"]): int(row["rows"]) for row in telemetry_by_session
        },
    }

    print("=== FASTF1 ENRICHMENT COVERAGE V1 ===")
    print(f"sessions={result['sessions']}")
    print(f"session_counts={result['session_counts']}")
    print(f"lap_metadata={result['lap_metadata']}")
    print("lap_metadata_by_session:")
    for row in result["lap_metadata_by_session"]:
        total = row["total_laps"] or 0
        populated = row["lap_start"] or 0
        coverage = 100.0 * populated / total if total else 0.0
        print(
            f"  {row['season_year']} {row['session_type']}: "
            f"laps={total} lap_start={populated} coverage={coverage:.1f}% "
            f"tyre_life={row['tyre_life']} fresh={row['fresh_tyre']} "
            f"speed_i1={row['speed_i1']} track_status={row['track_status']}"
        )
    print(f"enrichment_table_rows={result['enrichment_table_rows']}")
    print(
        "telemetry_rows_by_session_type="
        f"{result['telemetry_rows_by_session_type']}"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit FastF1 enrichment coverage")
    parser.add_argument("--start-year", type=int, default=2018)
    parser.add_argument("--end-year", type=int, default=2026)
    parser.add_argument("--json", default="")
    args = parser.parse_args()

    load_dotenv()
    url = os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is not set")

    engine = create_engine(url)
    result = run_audit(
        engine,
        start_year=args.start_year,
        end_year=args.end_year,
    )

    if args.json:
        Path(args.json).write_text(
            json.dumps(result, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print(f"wrote={args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
