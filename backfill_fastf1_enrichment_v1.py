"""FastF1 enrichment backfill v1.

Adds high-value historical data that the race-strategy system can consume later:
1) rich FastF1 lap metadata (tyre life/freshness, speed traps, track status,
   pit timestamps, qualifying/race accuracy flags)
2) time-varying session weather samples
3) exact race track-status intervals and race-control messages
4) optional per-lap telemetry summaries for selected session types

This script is data ingestion only. It does not train or modify the simulator.
Telemetry is deliberately opt-in because it is substantially more expensive
than timing/weather backfills.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import time
from pathlib import Path
from typing import Any

import fastf1
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text


API_CALL_COUNT = 0
THROTTLE_EVERY = 350
THROTTLE_SECONDS = 15 * 60
MAX_RETRIES = 3
RETRY_SLEEP_SECONDS = 60 * 60


def api_call(fn, *args, **kwargs):
    """Call FastF1 with conservative throttling for large historical backfills."""
    global API_CALL_COUNT
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            result = fn(*args, **kwargs)
            API_CALL_COUNT += 1
            if API_CALL_COUNT % THROTTLE_EVERY == 0:
                print(
                    f"  [throttle] {API_CALL_COUNT} FastF1 calls; "
                    f"cooling down {THROTTLE_SECONDS // 60} min",
                    flush=True,
                )
                time.sleep(THROTTLE_SECONDS)
            return result
        except Exception as exc:
            message = str(exc).lower()
            rate_limited = (
                "rate" in message and "limit" in message
            ) or "500 calls/h" in message or "too many requests" in message
            if not rate_limited or attempt >= MAX_RETRIES:
                raise
            print(
                f"  [rate limit] attempt {attempt}/{MAX_RETRIES}; "
                f"sleeping {RETRY_SLEEP_SECONDS // 60} min",
                flush=True,
            )
            time.sleep(RETRY_SLEEP_SECONDS)
    raise RuntimeError("FastF1 API retry loop exhausted")


SESSION_LOAD_NAMES = {
    "FP1": "Practice 1",
    "FP2": "Practice 2",
    "FP3": "Practice 3",
    "Q": "Qualifying",
    "SQ": "Sprint Qualifying",
    "S": "Sprint",
    "R": "Race",
}

TRACK_STATUS_NAMES = {
    "1": "ALL_CLEAR",
    "2": "YELLOW",
    "4": "SAFETY_CAR",
    "5": "RED_FLAG",
    "6": "VSC",
    "7": "VSC_ENDING",
    "8": "CHEQUERED",
}


def to_seconds(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        if hasattr(value, "total_seconds"):
            value = value.total_seconds()
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result


def safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result


def safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def safe_bool(value: Any) -> bool | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, bool):
        return value
    text_value = str(value).strip().lower()
    if text_value in {"true", "1", "yes"}:
        return True
    if text_value in {"false", "0", "no"}:
        return False
    return None


def db_session_rows(conn, start_year: int, end_year: int, session_types: tuple[str, ...]):
    rows = conn.execute(
        text(
            """
            SELECT
                s.id AS session_id,
                r.id AS race_id,
                r.season_year,
                r.round_number,
                r.race_date,
                s.session_type
            FROM sessions s
            JOIN races r ON r.id = s.race_id
            WHERE r.season_year BETWEEN :start_year AND :end_year
              AND r.race_date <= CURRENT_DATE
              AND s.session_type = ANY(:session_types)
            ORDER BY r.race_date, r.id, s.id
            """
        ),
        {"start_year": start_year, "end_year": end_year, "session_types": list(session_types)},
    ).mappings().all()
    return [dict(row) for row in rows]


def entry_map(conn, race_id: int) -> dict[int, int]:
    rows = conn.execute(
        text(
            """
            SELECT id, car_number
            FROM race_entries
            WHERE race_id = :race_id
              AND car_number IS NOT NULL
            """
        ),
        {"race_id": race_id},
    ).mappings().all()
    mapping: dict[int, int] = {}
    for row in rows:
        car_number = safe_int(row["car_number"])
        if car_number is not None:
            mapping[car_number] = int(row["id"])
    return mapping


def backfill_lap_metadata(engine, *, start_year: int, end_year: int, session_types: tuple[str, ...] = ('FP2', 'Q', 'R')) -> dict[str, int]:
    updated = 0
    sessions_done = 0
    failures = 0

    with engine.begin() as conn:
        sessions = db_session_rows(
            conn,
            start_year,
            end_year,
            session_types,
        )

    print(f"[lap-metadata] sessions={len(sessions)}", flush=True)

    for item in sessions:
        year = int(item["season_year"])
        rnd = int(item["round_number"])
        db_type = str(item["session_type"])
        session_id = int(item["session_id"])
        race_id = int(item["race_id"])

        try:
            session = api_call(fastf1.get_session, year, rnd, SESSION_LOAD_NAMES[db_type])
            # Lap.get_telemetry() requires the session's car/position
            # telemetry to have been loaded first.
            api_call(
                session.load,
                laps=True,
                telemetry=True,
                weather=False,
                messages=False,
            )
            laps = session.laps
            if laps is None or laps.empty:
                continue

            with engine.connect() as conn:
                entries = entry_map(conn, race_id)

            # Build a tiny update buffer first, then write one transaction.
            updates: list[dict[str, Any]] = []
            for _, row in laps.iterrows():
                car_number = safe_int(row.get("DriverNumber"))
                entry_id = entries.get(car_number or -1)
                lap_number = safe_int(row.get("LapNumber"))
                if entry_id is None or lap_number is None:
                    continue

                updates.append(
                    {
                        "session_id": session_id,
                        "entry_id": entry_id,
                        "lap_number": lap_number,
                        "lap_start_time": to_seconds(row.get("LapStartTime")),
                        "s1_session": to_seconds(row.get("Sector1SessionTime")),
                        "s2_session": to_seconds(row.get("Sector2SessionTime")),
                        "s3_session": to_seconds(row.get("Sector3SessionTime")),
                        "speed_i1": safe_float(row.get("SpeedI1")),
                        "speed_i2": safe_float(row.get("SpeedI2")),
                        "speed_fl": safe_float(row.get("SpeedFL")),
                        "speed_st": safe_float(row.get("SpeedST")),
                        "tyre_life": safe_float(row.get("TyreLife")),
                        "fresh_tyre": safe_bool(row.get("FreshTyre")),
                        "track_status": (
                            None
                            if row.get("TrackStatus") is None
                            else str(row.get("TrackStatus"))
                        ),
                        "position": safe_int(row.get("Position")),
                        "is_accurate": safe_bool(row.get("IsAccurate")),
                        "is_personal_best": safe_bool(row.get("IsPersonalBest")),
                        "is_deleted": safe_bool(row.get("Deleted")),
                        "deleted_reason": (
                            None
                            if row.get("DeletedReason") is None
                            else str(row.get("DeletedReason"))
                        ),
                        "pit_in": to_seconds(row.get("PitInTime")),
                        "pit_out": to_seconds(row.get("PitOutTime")),
                    }
                )

            with engine.begin() as conn:
                for row in updates:
                    result = conn.execute(
                        text(
                            """
                            UPDATE laps
                            SET
                                lap_start_time_seconds = :lap_start_time,
                                sector_1_session_time_seconds = :s1_session,
                                sector_2_session_time_seconds = :s2_session,
                                sector_3_session_time_seconds = :s3_session,
                                speed_i1_kmh = :speed_i1,
                                speed_i2_kmh = :speed_i2,
                                speed_fl_kmh = :speed_fl,
                                speed_st_kmh = :speed_st,
                                tyre_life_laps = :tyre_life,
                                fresh_tyre = :fresh_tyre,
                                track_status_code = :track_status,
                                position_on_track = :position,
                                is_accurate = :is_accurate,
                                is_personal_best = :is_personal_best,
                                is_deleted = :is_deleted,
                                deleted_reason = :deleted_reason,
                                pit_in_time_seconds = :pit_in,
                                pit_out_time_seconds = :pit_out
                            WHERE session_id = :session_id
                              AND race_entry_id = :entry_id
                              AND lap_number = :lap_number
                            """
                        ),
                        row,
                    )
                    updated += int(result.rowcount or 0)

            sessions_done += 1
            print(
                f"  [{sessions_done}/{len(sessions)}] {year} R{rnd} {db_type}: "
                f"FastF1 laps={len(updates)} updated={updated}",
                flush=True,
            )
        except Exception as exc:
            failures += 1
            print(f"  {year} R{rnd} {db_type}: FAILED {exc}", flush=True)

        time.sleep(0.2)

    return {"sessions_done": sessions_done, "rows_updated": updated, "failures": failures}


def backfill_weather_samples(engine, *, start_year: int, end_year: int, session_types: tuple[str, ...] = ('FP2', 'Q', 'R')) -> dict[str, int]:
    sessions_done = 0
    samples_written = 0
    failures = 0

    with engine.begin() as conn:
        sessions = db_session_rows(
            conn,
            start_year,
            end_year,
            session_types,
        )

    print(f"[weather-samples] sessions={len(sessions)}", flush=True)

    for item in sessions:
        year = int(item["season_year"])
        rnd = int(item["round_number"])
        db_type = str(item["session_type"])
        session_id = int(item["session_id"])

        try:
            with engine.begin() as conn:
                exists = conn.execute(
                    text(
                        """
                        SELECT 1
                        FROM session_weather_samples
                        WHERE session_id = :session_id
                        LIMIT 1
                        """
                    ),
                    {"session_id": session_id},
                ).first()
            if exists:
                continue

            session = api_call(fastf1.get_session, year, rnd, SESSION_LOAD_NAMES[db_type])
            api_call(
                session.load,
                laps=False,
                telemetry=False,
                weather=True,
                messages=False,
            )
            weather = session.weather_data
            if weather is None or weather.empty:
                continue

            rows: list[dict[str, Any]] = []
            for _, row in weather.iterrows():
                timestamp = to_seconds(row.get("Time"))
                if timestamp is None:
                    continue
                rows.append(
                    {
                        "session_id": session_id,
                        "t": round(timestamp, 3),
                        "air": safe_float(row.get("AirTemp")),
                        "track": safe_float(row.get("TrackTemp")),
                        "humidity": safe_float(row.get("Humidity")),
                        "pressure": safe_float(row.get("Pressure")),
                        "rain": safe_bool(row.get("Rainfall")),
                        "wind_dir": safe_float(row.get("WindDirection")),
                        "wind_speed": safe_float(row.get("WindSpeed")),
                    }
                )

            with engine.begin() as conn:
                for row in rows:
                    result = conn.execute(
                        text(
                            """
                            INSERT INTO session_weather_samples (
                                session_id, sample_time_seconds, air_temp_c,
                                track_temp_c, humidity_pct, pressure_mbar,
                                rainfall, wind_direction_deg, wind_speed_mps
                            )
                            VALUES (
                                :session_id, :t, :air, :track, :humidity, :pressure,
                                :rain, :wind_dir, :wind_speed
                            )
                            ON CONFLICT (session_id, sample_time_seconds, source)
                            DO UPDATE SET
                                air_temp_c = EXCLUDED.air_temp_c,
                                track_temp_c = EXCLUDED.track_temp_c,
                                humidity_pct = EXCLUDED.humidity_pct,
                                pressure_mbar = EXCLUDED.pressure_mbar,
                                rainfall = EXCLUDED.rainfall,
                                wind_direction_deg = EXCLUDED.wind_direction_deg,
                                wind_speed_mps = EXCLUDED.wind_speed_mps
                            """
                        ),
                        row,
                    )
                    samples_written += int(result.rowcount or 0)

            sessions_done += 1
            print(
                f"  [{sessions_done}/{len(sessions)}] {year} R{rnd} {db_type}: "
                f"samples={len(rows)}",
                flush=True,
            )
        except Exception as exc:
            failures += 1
            print(f"  {year} R{rnd} {db_type}: FAILED {exc}", flush=True)

        time.sleep(0.2)

    return {"sessions_done": sessions_done, "samples_written": samples_written, "failures": failures}


def status_intervals(track_status: pd.DataFrame) -> list[dict[str, Any]]:
    """Compress consecutive FastF1 status samples into status intervals."""
    if track_status is None or track_status.empty:
        return []

    samples: list[tuple[float, str]] = []
    for _, row in track_status.iterrows():
        timestamp = to_seconds(row.get("Time"))
        status = row.get("Status")
        if timestamp is None or status is None:
            continue
        samples.append((float(timestamp), str(status)))

    if not samples:
        return []

    samples.sort(key=lambda item: item[0])

    # FastF1 can contain repeated samples of the same status. It can also
    # contain multiple status updates at the same timestamp; preserve the
    # final state at that timestamp rather than creating zero-length intervals.
    by_time: dict[float, str] = {}
    for timestamp, status_code in samples:
        by_time[timestamp] = status_code
    ordered = sorted(by_time.items())

    intervals: list[dict[str, Any]] = []
    start_time, current_status = ordered[0]

    for timestamp, status_code in ordered[1:]:
        if status_code == current_status:
            continue

        intervals.append(
            {
                "start": round(start_time, 3),
                "end": round(timestamp, 3),
                "status_code": current_status,
                "status_name": TRACK_STATUS_NAMES.get(current_status, "UNKNOWN"),
            }
        )
        start_time = timestamp
        current_status = status_code

    intervals.append(
        {
            "start": round(start_time, 3),
            "end": None,
            "status_code": current_status,
            "status_name": TRACK_STATUS_NAMES.get(current_status, "UNKNOWN"),
        }
    )
    return intervals


def _message_value(row: Any, *names: str) -> Any:
    for name in names:
        try:
            value = row.get(name)
        except AttributeError:
            value = None
        if value is not None:
            return value
    return None


def message_fingerprint(session_id: int, row: Any) -> str:
    raw = "|".join(
        [
            str(session_id),
            str(_message_value(row, "Time")),
            str(_message_value(row, "Lap")),
            str(_message_value(row, "Category")),
            str(_message_value(row, "Message")),
            str(_message_value(row, "Flag")),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def backfill_circuit_corners(engine, *, start_year: int, end_year: int) -> dict[str, int]:
    """Backfill FastF1 circuit corner geometry once per race weekend."""
    races_done = 0
    corners_written = 0
    failures = 0

    with engine.begin() as conn:
        races = conn.execute(
            text(
                """
                SELECT r.id AS race_id, r.track_id, r.season_year, r.round_number
                FROM races r
                WHERE r.season_year BETWEEN :start_year AND :end_year
                  AND r.race_date <= CURRENT_DATE
                ORDER BY r.race_date, r.id
                """
            ),
            {"start_year": start_year, "end_year": end_year},
        ).mappings().all()

    print(f"[circuit-corners] races={len(races)}", flush=True)

    for item in races:
        year = int(item["season_year"])
        rnd = int(item["round_number"])
        race_id = int(item["race_id"])
        track_id = int(item["track_id"])

        try:
            with engine.begin() as conn:
                exists = conn.execute(
                    text(
                        """
                        SELECT 1
                        FROM track_corners
                        WHERE track_id = :track_id
                          AND season_year = :year
                        LIMIT 1
                        """
                    ),
                    {"track_id": track_id, "year": year},
                ).first()
            if exists:
                continue

            session = api_call(fastf1.get_session, year, rnd, "Q")
            # Session.get_circuit_info() computes marker distances from a
            # reference lap's position telemetry, so both laps and telemetry
            # must be loaded here.
            api_call(session.load, laps=True, telemetry=True, weather=False, messages=False)
            info = session.get_circuit_info()
            corners = getattr(info, "corners", None)
            rotation = safe_float(getattr(info, "rotation", None))
            if corners is None or corners.empty:
                # Some weekends may not expose Q circuit info; race is an
                # independent fallback.
                session = api_call(fastf1.get_session, year, rnd, "R")
                api_call(session.load, laps=True, telemetry=True, weather=False, messages=False)
                info = session.get_circuit_info()
                corners = getattr(info, "corners", None)
                rotation = safe_float(getattr(info, "rotation", None))

            if corners is None or corners.empty:
                continue

            rows: list[dict[str, Any]] = []
            for _, corner in corners.iterrows():
                number = safe_int(corner.get("Number"))
                if number is None:
                    continue
                rows.append(
                    {
                        "track_id": track_id,
                        "year": year,
                        "number": number,
                        "letter": (
                            None
                            if corner.get("Letter") is None
                            else str(corner.get("Letter"))
                        ),
                        "x": safe_float(corner.get("X")),
                        "y": safe_float(corner.get("Y")),
                        "angle": safe_float(corner.get("Angle")),
                        # FastF1 circuit info uses meter-scale track distance.
                        "distance": safe_float(corner.get("Distance")),
                        "rotation": rotation,
                    }
                )

            with engine.begin() as conn:
                for row in rows:
                    result = conn.execute(
                        text(
                            """
                            INSERT INTO track_corners (
                                track_id, season_year, corner_number, corner_letter,
                                x_coord, y_coord, angle_deg, distance_m,
                                circuit_rotation_deg
                            )
                            VALUES (
                                :track_id, :year, :number, :letter,
                                :x, :y, :angle, :distance, :rotation
                            )
                            ON CONFLICT (
                                track_id, season_year, corner_number, corner_letter, source
                            )
                            DO UPDATE SET
                                x_coord = EXCLUDED.x_coord,
                                y_coord = EXCLUDED.y_coord,
                                angle_deg = EXCLUDED.angle_deg,
                                distance_m = EXCLUDED.distance_m,
                                circuit_rotation_deg = EXCLUDED.circuit_rotation_deg
                            """
                        ),
                        row,
                    )
                    corners_written += int(result.rowcount or 0)

                conn.execute(
                    text(
                        """
                        UPDATE tracks
                        SET num_turns = (
                            SELECT COUNT(*)
                            FROM track_corners tc
                            WHERE tc.track_id = :track_id
                              AND tc.season_year = :year
                        )
                        WHERE id = :track_id
                          AND (num_turns IS NULL OR num_turns = 0)
                        """
                    ),
                    {"track_id": track_id, "year": year},
                )

            races_done += 1
            print(
                f"  [{races_done}/{len(races)}] {year} R{rnd}: corners={len(rows)}",
                flush=True,
            )
        except Exception as exc:
            failures += 1
            print(f"  {year} R{rnd}: FAILED {exc}", flush=True)

        time.sleep(0.2)

    return {
        "races_done": races_done,
        "corners_written": corners_written,
        "failures": failures,
    }


def backfill_race_events(engine, *, start_year: int, end_year: int) -> dict[str, int]:
    intervals_written = 0
    messages_written = 0
    failures = 0
    races_done = 0

    with engine.begin() as conn:
        sessions = db_session_rows(conn, start_year, end_year, ("R",))

    print(f"[race-events] race sessions={len(sessions)}", flush=True)

    for item in sessions:
        year = int(item["season_year"])
        rnd = int(item["round_number"])
        session_id = int(item["session_id"])

        try:
            session = api_call(fastf1.get_session, year, rnd, "Race")
            # track_status is loaded as part of laps=True; race-control
            # messages are loaded independently via messages=True.
            api_call(
                session.load,
                laps=True,
                telemetry=False,
                weather=False,
                messages=True,
            )

            intervals = status_intervals(getattr(session, "track_status", None))
            messages = getattr(session, "race_control_messages", None)

            with engine.begin() as conn:
                for interval in intervals:
                    result = conn.execute(
                        text(
                            """
                            INSERT INTO session_track_status_intervals (
                                session_id, start_time_seconds, end_time_seconds,
                                status_code, status_name
                            )
                            VALUES (
                                :session_id, :start, :end, :status_code, :status_name
                            )
                            ON CONFLICT (
                                session_id, start_time_seconds, status_code, source
                            )
                            DO UPDATE SET
                                end_time_seconds = EXCLUDED.end_time_seconds,
                                status_name = EXCLUDED.status_name
                            """
                        ),
                        {"session_id": session_id, **interval},
                    )
                    intervals_written += int(result.rowcount or 0)

                if messages is not None and not messages.empty:
                    for _, row in messages.iterrows():
                        event_time = to_seconds(_message_value(row, "Time"))
                        lap_number = safe_int(_message_value(row, "Lap", "LapNumber"))
                        category = _message_value(row, "Category")
                        message = _message_value(row, "Message")
                        if message is None:
                            continue
                        values = {
                            "session_id": session_id,
                            "event_time": event_time,
                            "lap_number": lap_number,
                            "category": None if category is None else str(category),
                            "message": str(message),
                            "flag": _message_value(row, "Flag"),
                            "scope": _message_value(row, "Scope"),
                            "sector": _message_value(row, "Sector"),
                            "racing_number": _message_value(
                                row, "RacingNumber", "DriverNumber"
                            ),
                            "fingerprint": message_fingerprint(session_id, row),
                        }
                        result = conn.execute(
                            text(
                                """
                                INSERT INTO session_race_control_messages (
                                    session_id, event_time_seconds, lap_number,
                                    category, message, flag, scope, sector,
                                    racing_number, event_fingerprint
                                )
                                VALUES (
                                    :session_id, :event_time, :lap_number, :category,
                                    :message, :flag, :scope, :sector,
                                    :racing_number, :fingerprint
                                )
                                ON CONFLICT (event_fingerprint) DO NOTHING
                                """
                            ),
                            values,
                        )
                        messages_written += int(result.rowcount or 0)

            races_done += 1
            print(
                f"  [{races_done}/{len(sessions)}] {year} R{rnd}: "
                f"status_intervals={len(intervals)} "
                f"rcm={0 if messages is None else len(messages)}",
                flush=True,
            )
        except Exception as exc:
            failures += 1
            print(f"  {year} R{rnd}: FAILED {exc}", flush=True)

        time.sleep(0.2)

    return {
        "races_done": races_done,
        "status_intervals_written": intervals_written,
        "messages_written": messages_written,
        "failures": failures,
    }


def telemetry_summary(telemetry: pd.DataFrame) -> dict[str, Any] | None:
    if telemetry is None or telemetry.empty:
        return None

    def pct(condition_series: pd.Series | None) -> float | None:
        if condition_series is None:
            return None
        valid = condition_series.dropna()
        if len(valid) == 0:
            return None
        return 100.0 * float(valid.astype(bool).mean())

    result: dict[str, Any] = {
        "mean_speed_kmh": None,
        "max_speed_kmh": None,
        "mean_throttle_pct": None,
        "full_throttle_pct": None,
        "brake_active_pct": None,
        "drs_active_pct": None,
        "mean_rpm": None,
        "mean_gear": None,
        "distance_m": None,
        "mean_distance_to_driver_ahead_m": None,
        "close_traffic_150m_pct": None,
        "driver_ahead_samples": 0,
        "telemetry_samples": int(len(telemetry)),
        "telemetry_quality": "partial",
    }

    for column, key in (
        ("Speed", "mean_speed_kmh"),
        ("RPM", "mean_rpm"),
        ("nGear", "mean_gear"),
    ):
        if column in telemetry:
            values = pd.to_numeric(telemetry[column], errors="coerce").dropna()
            if len(values):
                result[key] = float(values.mean())

    if "Speed" in telemetry:
        values = pd.to_numeric(telemetry["Speed"], errors="coerce").dropna()
        if len(values):
            result["max_speed_kmh"] = float(values.max())

    if "Throttle" in telemetry:
        throttle = pd.to_numeric(telemetry["Throttle"], errors="coerce")
        valid = throttle.dropna()
        if len(valid):
            result["mean_throttle_pct"] = float(valid.mean())
            result["full_throttle_pct"] = float((valid >= 95.0).mean() * 100.0)

    if "Brake" in telemetry:
        result["brake_active_pct"] = pct(telemetry["Brake"])

    if "DRS" in telemetry:
        drs = pd.to_numeric(telemetry["DRS"], errors="coerce")
        valid = drs.dropna()
        if len(valid):
            result["drs_active_pct"] = float((valid > 0).mean() * 100.0)

    if "Distance" in telemetry:
        distance = pd.to_numeric(telemetry["Distance"], errors="coerce").dropna()
        if len(distance):
            result["distance_m"] = float(distance.max() - distance.min())

    if "DistanceToDriverAhead" in telemetry:
        ahead = pd.to_numeric(
            telemetry["DistanceToDriverAhead"], errors="coerce"
        ).dropna()
        if len(ahead):
            result["mean_distance_to_driver_ahead_m"] = float(ahead.mean())
            result["close_traffic_150m_pct"] = float((ahead <= 150.0).mean() * 100.0)
            result["driver_ahead_samples"] = int(len(ahead))

    required = {"Speed", "Throttle", "Brake"}
    result["telemetry_quality"] = (
        "full" if required.issubset(telemetry.columns) else "partial"
    )
    return result


def backfill_telemetry(engine, *, start_year: int, end_year: int, session_types: tuple[str, ...]) -> dict[str, int]:
    written = 0
    laps_attempted = 0
    failures = 0
    sessions_done = 0

    with engine.begin() as conn:
        sessions = db_session_rows(conn, start_year, end_year, session_types)

    print(f"[telemetry] sessions={len(sessions)} types={session_types}", flush=True)

    for item in sessions:
        year = int(item["season_year"])
        rnd = int(item["round_number"])
        db_type = str(item["session_type"])
        session_id = int(item["session_id"])
        race_id = int(item["race_id"])

        try:
            with engine.connect() as conn:
                coverage = conn.execute(
                    text(
                        """
                        SELECT
                            COUNT(*) FILTER (WHERE lap_time_seconds IS NOT NULL)
                                AS timed_laps,
                            (
                                SELECT COUNT(*)
                                FROM lap_telemetry_summary lts
                                WHERE lts.session_id = :session_id
                            ) AS telemetry_rows
                        FROM laps
                        WHERE session_id = :session_id
                        """
                    ),
                    {"session_id": session_id},
                ).mappings().one()

            timed_laps = int(coverage["timed_laps"] or 0)
            telemetry_rows = int(coverage["telemetry_rows"] or 0)
            if timed_laps > 0 and telemetry_rows >= timed_laps:
                print(
                    f"  [skip] {year} R{rnd} {db_type}: "
                    f"telemetry summary already populated "
                    f"({telemetry_rows}/{timed_laps} timed laps)",
                    flush=True,
                )
                sessions_done += 1
                continue

            # Telemetry backfill is independent of lap-metadata completeness.
            # A session can have complete lap metadata and still have no
            # telemetry summaries, so gate only on the target table above.
            session = api_call(
                fastf1.get_session, year, rnd, SESSION_LOAD_NAMES[db_type]
            )
            api_call(
                session.load,
                laps=True,
                telemetry=False,
                weather=False,
                messages=False,
            )
            laps = session.laps
            if laps is None or laps.empty:
                continue

            with engine.connect() as conn:
                entries = entry_map(conn, race_id)

            pending_rows: list[dict[str, Any]] = []
            for idx, lap_row in laps.iterrows():
                lap_number = safe_int(lap_row.get("LapNumber"))
                car_number = safe_int(lap_row.get("DriverNumber"))
                entry_id = entries.get(car_number or -1)
                lap_time = to_seconds(lap_row.get("LapTime"))
                if lap_number is None or entry_id is None or lap_time is None:
                    continue

                laps_attempted += 1
                try:
                    lap_slice = session.laps.loc[[idx]]
                    telemetry = lap_slice.get_telemetry()
                    # DriverAhead/DistanceToDriverAhead are not present in
                    # raw merged telemetry; calculate them per lap before
                    # summarising traffic exposure.
                    telemetry = telemetry.add_driver_ahead()
                    summary = telemetry_summary(telemetry)
                    if summary is None:
                        continue

                    pending_rows.append(
                        {
                            "session_id": session_id,
                            "entry_id": entry_id,
                            "lap_number": lap_number,
                            **summary,
                        }
                    )
                except Exception as exc:
                    failures += 1
                    if failures <= 10:
                        print(
                            f"    telemetry lap {lap_number} failed: {exc}",
                            flush=True,
                        )

            if pending_rows:
                with engine.begin() as conn:
                    conn.execute(
                        text(
                            """
                            INSERT INTO lap_telemetry_summary (
                                session_id, race_entry_id, lap_number,
                                mean_speed_kmh, max_speed_kmh,
                                mean_throttle_pct, full_throttle_pct,
                                brake_active_pct, drs_active_pct,
                                mean_rpm, mean_gear, distance_m,
                                mean_distance_to_driver_ahead_m,
                                close_traffic_150m_pct, driver_ahead_samples,
                                telemetry_samples, telemetry_quality
                            )
                            VALUES (
                                :session_id, :entry_id, :lap_number,
                                :mean_speed_kmh, :max_speed_kmh,
                                :mean_throttle_pct, :full_throttle_pct,
                                :brake_active_pct, :drs_active_pct,
                                :mean_rpm, :mean_gear, :distance_m,
                                :mean_distance_to_driver_ahead_m,
                                :close_traffic_150m_pct, :driver_ahead_samples,
                                :telemetry_samples, :telemetry_quality
                            )
                            ON CONFLICT (
                                session_id, race_entry_id, lap_number, source
                            )
                            DO UPDATE SET
                                mean_speed_kmh = EXCLUDED.mean_speed_kmh,
                                max_speed_kmh = EXCLUDED.max_speed_kmh,
                                mean_throttle_pct = EXCLUDED.mean_throttle_pct,
                                full_throttle_pct = EXCLUDED.full_throttle_pct,
                                brake_active_pct = EXCLUDED.brake_active_pct,
                                drs_active_pct = EXCLUDED.drs_active_pct,
                                mean_rpm = EXCLUDED.mean_rpm,
                                mean_gear = EXCLUDED.mean_gear,
                                distance_m = EXCLUDED.distance_m,
                                mean_distance_to_driver_ahead_m =
                                    EXCLUDED.mean_distance_to_driver_ahead_m,
                                close_traffic_150m_pct =
                                    EXCLUDED.close_traffic_150m_pct,
                                driver_ahead_samples = EXCLUDED.driver_ahead_samples,
                                telemetry_samples = EXCLUDED.telemetry_samples,
                                telemetry_quality = EXCLUDED.telemetry_quality
                            """
                        ),
                        pending_rows,
                    )
                written += len(pending_rows)

            sessions_done += 1
            print(
                f"  [{sessions_done}/{len(sessions)}] {year} R{rnd} {db_type}: "
                f"telemetry_rows={written}",
                flush=True,
            )
        except Exception as exc:
            failures += 1
            print(f"  {year} R{rnd} {db_type}: FAILED {exc}", flush=True)

    return {
        "sessions_done": sessions_done,
        "laps_attempted": laps_attempted,
        "telemetry_rows_written": written,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="FastF1 enrichment backfill v1")
    parser.add_argument("--start-year", type=int, default=2018)
    parser.add_argument("--end-year", type=int, default=2026)
    parser.add_argument(
        "--mode",
        choices=("lap-metadata", "weather", "events", "circuit-corners", "telemetry", "all"),
        default="all",
    )
    parser.add_argument(
        "--telemetry-sessions",
        default="Q,FP2,R",
        help="Comma-separated DB session codes for telemetry mode",
    )
    parser.add_argument(
        "--lap-metadata-sessions",
        default="Q,FP2,R",
        help="Comma-separated DB session codes for lap-metadata mode",
    )
    parser.add_argument(
        "--weather-sessions",
        default="Q,FP2,R",
        help="Comma-separated DB session codes for weather mode",
    )
    parser.add_argument("--cache-dir", default="")
    args = parser.parse_args()

    load_dotenv()
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is not set")

    if args.cache_dir:
        fastf1.Cache.enable_cache(args.cache_dir)
    else:
        default_cache = Path.cwd() / "cache"
        default_cache.mkdir(parents=True, exist_ok=True)
        fastf1.Cache.enable_cache(str(default_cache))

    engine = create_engine(database_url)

    if args.mode in {"lap-metadata", "all"}:
        lap_metadata_sessions = tuple(
            token.strip().upper()
            for token in args.lap_metadata_sessions.split(",")
            if token.strip()
        )
        invalid = sorted(set(lap_metadata_sessions) - set(SESSION_LOAD_NAMES))
        if invalid:
            raise SystemExit(f"Unknown lap-metadata session type(s): {invalid}")
        print("\n=== LAP METADATA BACKFILL ===")
        print(
            backfill_lap_metadata(
                engine,
                start_year=args.start_year,
                end_year=args.end_year,
                session_types=lap_metadata_sessions,
            )
        )

    if args.mode in {"weather", "all"}:
        weather_sessions = tuple(
            token.strip().upper()
            for token in args.weather_sessions.split(",")
            if token.strip()
        )
        invalid = sorted(set(weather_sessions) - set(SESSION_LOAD_NAMES))
        if invalid:
            raise SystemExit(f"Unknown weather session type(s): {invalid}")
        print("\n=== WEATHER SAMPLE BACKFILL ===")
        print(
            backfill_weather_samples(
                engine,
                start_year=args.start_year,
                end_year=args.end_year,
                session_types=weather_sessions,
            )
        )

    if args.mode in {"events", "all"}:
        print("\n=== RACE EVENT BACKFILL ===")
        print(backfill_race_events(engine, start_year=args.start_year, end_year=args.end_year))

    if args.mode in {"circuit-corners", "all"}:
        print("\n=== CIRCUIT CORNER BACKFILL ===")
        print(
            backfill_circuit_corners(
                engine,
                start_year=args.start_year,
                end_year=args.end_year,
            )
        )

    if args.mode == "telemetry":
        session_types = tuple(
            token.strip().upper()
            for token in args.telemetry_sessions.split(",")
            if token.strip()
        )
        invalid = sorted(set(session_types) - set(SESSION_LOAD_NAMES))
        if invalid:
            raise SystemExit(f"Unknown session type(s): {invalid}")
        print("\n=== TELEMETRY SUMMARY BACKFILL ===")
        print(
            backfill_telemetry(
                engine,
                start_year=args.start_year,
                end_year=args.end_year,
                session_types=session_types,
            )
        )

    print("\nDone. Production simulator integration remains unchanged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
