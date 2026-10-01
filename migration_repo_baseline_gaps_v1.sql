-- Repo baseline gaps v1.
--
-- These tables/columns are used by the API and pipeline but were created by
-- hand in psql, so a fresh database built from schema.sql + migration_*.sql
-- could not run the app. Definitions are RECONSTRUCTED FROM THE CODE THAT
-- READS/WRITES THEM, not from pg_dump. Every statement is IF NOT EXISTS, so
-- applying this to the production database is a no-op for anything that
-- already exists. Run audit_schema_drift_v1.py to confirm the reconstructed
-- types match production and correct this file if they do not.
--
-- Apply order: schema.sql, migration_manual_fields.sql,
-- migration_weekend_format.sql, this file, then the remaining migration_*.sql.

ALTER TABLE races
    ADD COLUMN IF NOT EXISTS regulation_era VARCHAR(40),
    ADD COLUMN IF NOT EXISTS winner_time_seconds NUMERIC(10,3);

ALTER TABLE race_results
    ADD COLUMN IF NOT EXISTS starting_grid_position INTEGER;

ALTER TABLE laps
    ADD COLUMN IF NOT EXISTS position INTEGER;

ALTER TABLE tracks
    ADD COLUMN IF NOT EXISTS pit_lane_time_loss_seconds NUMERIC(5,2);

ALTER TABLE session_weather
    ADD COLUMN IF NOT EXISTS rain_onset_lap INTEGER;

-- Derived table rebuilt by extract_race_stints.py (compound-change stints;
-- same-compound pit visits are intentionally collapsed).
CREATE TABLE IF NOT EXISTS race_stints (
    id SERIAL PRIMARY KEY,
    race_id INTEGER REFERENCES races(id),
    race_entry_id INTEGER REFERENCES race_entries(id),
    finishing_position INTEGER,
    stint_number INTEGER NOT NULL,
    compound VARCHAR(20),
    start_lap INTEGER NOT NULL,
    end_lap INTEGER NOT NULL,
    stint_length INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_race_stints_race ON race_stints (race_id);

-- Written by simulate_season.py. Every simulation run is kept (one row per
-- entity per as_of_round) so probability-over-time history is preserved.
-- as_of_round = last COMPLETED round when the simulation ran; a 'next_race'
-- row with as_of_round = N is the prediction for round N + 1.
CREATE TABLE IF NOT EXISTS season_predictions (
    id SERIAL PRIMARY KEY,
    season_year INTEGER NOT NULL,
    prediction_type VARCHAR(20) NOT NULL,
    entity_id INTEGER NOT NULL,
    entity_name VARCHAR(100) NOT NULL,
    probability_pct NUMERIC(5,2) NOT NULL,
    as_of_round INTEGER NOT NULL,
    computed_at TIMESTAMP NOT NULL DEFAULT now(),
    UNIQUE (season_year, prediction_type, entity_id, as_of_round)
);

-- Pre-2018 team history for driver profiles (sourced manually).
CREATE TABLE IF NOT EXISTS driver_team_history_manual (
    id SERIAL PRIMARY KEY,
    driver_id INTEGER NOT NULL REFERENCES drivers(id),
    team_name VARCHAR(100) NOT NULL,
    team_logo_url VARCHAR(255),
    start_year INTEGER NOT NULL,
    end_year INTEGER NOT NULL
);

-- Pirelli nominated compounds per race (label SOFT/MEDIUM/HARD -> C-number).
-- Read by strategy_production_v6. Definition copied from production \d output.
-- C-numbers are NOT comparable across the 2023 renumbering; see README.
CREATE TABLE IF NOT EXISTS race_compound_nominations (
    id SERIAL PRIMARY KEY,
    race_id INTEGER REFERENCES races(id),
    label VARCHAR(10) NOT NULL,
    c_compound VARCHAR(5) NOT NULL,
    UNIQUE (race_id, label)
);

-- Output of fit_tire_degradation.py (rejected research approach; kept so the
-- schema is complete). Column types from production; constraints beyond the
-- primary key were not inspected.
CREATE TABLE IF NOT EXISTS tire_degradation_curves (
    id SERIAL PRIMARY KEY,
    track_id INTEGER REFERENCES tracks(id),
    compound VARCHAR(20),
    baseline_pace_seconds NUMERIC(6,3),
    degradation_rate_seconds_per_lap NUMERIC(6,4),
    sample_size INTEGER
);
