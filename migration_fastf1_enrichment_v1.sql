-- FastF1 enrichment layer v1.
-- Raw/derived data only; no production simulator logic.

ALTER TABLE laps
    ADD COLUMN IF NOT EXISTS lap_start_time_seconds NUMERIC(10,3),
    ADD COLUMN IF NOT EXISTS sector_1_session_time_seconds NUMERIC(10,3),
    ADD COLUMN IF NOT EXISTS sector_2_session_time_seconds NUMERIC(10,3),
    ADD COLUMN IF NOT EXISTS sector_3_session_time_seconds NUMERIC(10,3),
    ADD COLUMN IF NOT EXISTS speed_i1_kmh NUMERIC(7,3),
    ADD COLUMN IF NOT EXISTS speed_i2_kmh NUMERIC(7,3),
    ADD COLUMN IF NOT EXISTS speed_fl_kmh NUMERIC(7,3),
    ADD COLUMN IF NOT EXISTS speed_st_kmh NUMERIC(7,3),
    ADD COLUMN IF NOT EXISTS tyre_life_laps NUMERIC(6,1),
    ADD COLUMN IF NOT EXISTS fresh_tyre BOOLEAN,
    ADD COLUMN IF NOT EXISTS track_status_code VARCHAR(10),
    ADD COLUMN IF NOT EXISTS position_on_track INTEGER,
    ADD COLUMN IF NOT EXISTS is_accurate BOOLEAN,
    ADD COLUMN IF NOT EXISTS is_personal_best BOOLEAN,
    ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN,
    ADD COLUMN IF NOT EXISTS deleted_reason TEXT,
    ADD COLUMN IF NOT EXISTS pit_in_time_seconds NUMERIC(10,3),
    ADD COLUMN IF NOT EXISTS pit_out_time_seconds NUMERIC(10,3);

CREATE INDEX IF NOT EXISTS idx_laps_session_entry_lap
    ON laps(session_id, race_entry_id, lap_number);

CREATE TABLE IF NOT EXISTS session_weather_samples (
    id BIGSERIAL PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    sample_time_seconds NUMERIC(10,3) NOT NULL,
    air_temp_c NUMERIC(5,2),
    track_temp_c NUMERIC(5,2),
    humidity_pct NUMERIC(5,2),
    pressure_mbar NUMERIC(7,2),
    rainfall BOOLEAN,
    wind_direction_deg NUMERIC(6,2),
    wind_speed_mps NUMERIC(6,2),
    source VARCHAR(30) NOT NULL DEFAULT 'fastf1',
    UNIQUE (session_id, sample_time_seconds, source)
);

CREATE INDEX IF NOT EXISTS idx_session_weather_samples_session_time
    ON session_weather_samples(session_id, sample_time_seconds);

CREATE TABLE IF NOT EXISTS session_track_status_intervals (
    id BIGSERIAL PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    start_time_seconds NUMERIC(10,3) NOT NULL,
    end_time_seconds NUMERIC(10,3),
    status_code VARCHAR(10) NOT NULL,
    status_name VARCHAR(40),
    source VARCHAR(30) NOT NULL DEFAULT 'fastf1',
    UNIQUE (session_id, start_time_seconds, status_code, source)
);

CREATE INDEX IF NOT EXISTS idx_track_status_intervals_session_time
    ON session_track_status_intervals(session_id, start_time_seconds);

CREATE TABLE IF NOT EXISTS session_race_control_messages (
    id BIGSERIAL PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    event_time_seconds NUMERIC(10,3),
    lap_number INTEGER,
    category VARCHAR(40),
    message TEXT NOT NULL,
    flag VARCHAR(40),
    scope VARCHAR(40),
    sector VARCHAR(40),
    racing_number VARCHAR(20),
    event_fingerprint VARCHAR(64) NOT NULL UNIQUE,
    source VARCHAR(30) NOT NULL DEFAULT 'fastf1'
);

CREATE INDEX IF NOT EXISTS idx_race_control_messages_session_time
    ON session_race_control_messages(session_id, event_time_seconds);

CREATE TABLE IF NOT EXISTS lap_telemetry_summary (
    id BIGSERIAL PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    race_entry_id INTEGER NOT NULL REFERENCES race_entries(id),
    lap_number INTEGER NOT NULL,
    mean_speed_kmh NUMERIC(7,3),
    max_speed_kmh NUMERIC(7,3),
    mean_throttle_pct NUMERIC(7,3),
    full_throttle_pct NUMERIC(7,3),
    brake_active_pct NUMERIC(7,3),
    drs_active_pct NUMERIC(7,3),
    mean_rpm NUMERIC(10,3),
    mean_gear NUMERIC(7,3),
    distance_m NUMERIC(12,3),
    mean_distance_to_driver_ahead_m NUMERIC(10,3),
    close_traffic_150m_pct NUMERIC(7,3),
    driver_ahead_samples INTEGER,
    telemetry_samples INTEGER NOT NULL DEFAULT 0,
    telemetry_quality VARCHAR(30),
    source VARCHAR(30) NOT NULL DEFAULT 'fastf1',
    UNIQUE (session_id, race_entry_id, lap_number, source)
);

CREATE INDEX IF NOT EXISTS idx_lap_telemetry_summary_entry_lap
    ON lap_telemetry_summary(race_entry_id, lap_number);

CREATE INDEX IF NOT EXISTS idx_lap_telemetry_summary_session
    ON lap_telemetry_summary(session_id);
