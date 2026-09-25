CREATE TABLE tracks (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    country VARCHAR(100) NOT NULL,
    length_km NUMERIC(5,3),
    lap_record VARCHAR(20)
);

CREATE TABLE teams (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    color_hex VARCHAR(7)
);

CREATE TABLE drivers (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    nationality VARCHAR(100),
    permanent_number INTEGER
);

CREATE TABLE races (
    id SERIAL PRIMARY KEY,
    track_id INTEGER REFERENCES tracks(id),
    season_year INTEGER NOT NULL,
    round_number INTEGER NOT NULL,
    race_date DATE,
    UNIQUE (season_year, round_number)
);

CREATE TABLE race_entries (
    id SERIAL PRIMARY KEY,
    race_id INTEGER REFERENCES races(id),
    driver_id INTEGER REFERENCES drivers(id),
    team_id INTEGER REFERENCES teams(id),
    role VARCHAR(20) DEFAULT 'race_driver',
    car_number INTEGER
);

CREATE TABLE sessions (
    id SERIAL PRIMARY KEY,
    race_id INTEGER REFERENCES races(id),
    session_type VARCHAR(20) NOT NULL,
    start_time TIMESTAMP
);

CREATE TABLE laps (
    id SERIAL PRIMARY KEY,
    session_id INTEGER REFERENCES sessions(id),
    race_entry_id INTEGER REFERENCES race_entries(id),
    lap_number INTEGER NOT NULL,
    lap_time NUMERIC(8,3),
    sector_1_time NUMERIC(8,3),
    sector_2_time NUMERIC(8,3),
    sector_3_time NUMERIC(8,3),
    tire_compound VARCHAR(20),
    is_valid BOOLEAN DEFAULT TRUE
);

CREATE TABLE qualifying_results (
    id SERIAL PRIMARY KEY,
    session_id INTEGER REFERENCES sessions(id),
    race_entry_id INTEGER REFERENCES race_entries(id),
    q1_time NUMERIC(6,3),
    q2_time NUMERIC(6,3),
    q3_time NUMERIC(6,3),
    final_position INTEGER
);

CREATE TABLE race_results (
    id SERIAL PRIMARY KEY,
    session_id INTEGER REFERENCES sessions(id),
    race_entry_id INTEGER REFERENCES race_entries(id),
    finishing_position INTEGER,
    points NUMERIC(4,1),
    status VARCHAR(20)
);
