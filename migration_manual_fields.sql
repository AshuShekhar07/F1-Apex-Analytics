ALTER TABLE tracks
    ADD COLUMN IF NOT EXISTS circuit_type VARCHAR(30),
    ADD COLUMN IF NOT EXISTS latitude NUMERIC(9,6),
    ADD COLUMN IF NOT EXISTS longitude NUMERIC(9,6),
    ADD COLUMN IF NOT EXISTS num_turns INTEGER,
    ADD COLUMN IF NOT EXISTS total_race_laps INTEGER,
    ADD COLUMN IF NOT EXISTS race_distance_km NUMERIC(6,3),
    ADD COLUMN IF NOT EXISTS avg_race_duration_minutes INTEGER,
    ADD COLUMN IF NOT EXISTS difficulty_rating INTEGER,
    ADD COLUMN IF NOT EXISTS elevation_change_m INTEGER;

ALTER TABLE drivers
    ADD COLUMN IF NOT EXISTS date_of_birth DATE,
    ADD COLUMN IF NOT EXISTS photo_url TEXT,
    ADD COLUMN IF NOT EXISTS country_code VARCHAR(3),
    ADD COLUMN IF NOT EXISTS total_world_championships INTEGER DEFAULT 0;

ALTER TABLE teams
    ADD COLUMN IF NOT EXISTS logo_url TEXT;

ALTER TABLE race_results
    ADD COLUMN IF NOT EXISTS gap_to_winner_seconds NUMERIC(8,3),
    ADD COLUMN IF NOT EXISTS gap_to_winner_display VARCHAR(20);

ALTER TABLE races
    ADD COLUMN IF NOT EXISTS safety_car_periods INTEGER,
    ADD COLUMN IF NOT EXISTS vsc_periods INTEGER,
    ADD COLUMN IF NOT EXISTS red_flags INTEGER;

CREATE TABLE IF NOT EXISTS session_weather (
    id                INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    session_id        INTEGER REFERENCES sessions(id) UNIQUE,
    air_temp_avg      NUMERIC(4,1),
    track_temp_avg    NUMERIC(4,1),
    humidity_avg      NUMERIC(4,1),
    rainfall          BOOLEAN,
    wind_speed_avg    NUMERIC(4,1)
);
