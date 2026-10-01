-- Race Strategy Simulator v1: raw FastF1 pit-stop reconstruction.
-- This table stores only what FastF1 can support directly: total elapsed
-- pit-lane time from PitInTime to the subsequent PitOutTime. It deliberately
-- leaves crew stationary/service time separate because that quantity is not
-- identified by these FastF1 fields alone.

CREATE TABLE IF NOT EXISTS race_strategy_pit_stops (
    id SERIAL PRIMARY KEY,
    race_id INTEGER NOT NULL REFERENCES races(id),
    race_entry_id INTEGER NOT NULL REFERENCES race_entries(id),
    pit_lap INTEGER NOT NULL,
    pit_in_time_seconds NUMERIC(10,3) NOT NULL,
    pit_out_time_seconds NUMERIC(10,3) NOT NULL,
    total_pit_lane_seconds NUMERIC(10,3) NOT NULL,
    source VARCHAR(30) NOT NULL DEFAULT 'fastf1',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (race_id, race_entry_id, pit_lap, source)
);

CREATE INDEX IF NOT EXISTS idx_race_strategy_pit_stops_race
    ON race_strategy_pit_stops (race_id);

CREATE INDEX IF NOT EXISTS idx_race_strategy_pit_stops_entry
    ON race_strategy_pit_stops (race_entry_id);
