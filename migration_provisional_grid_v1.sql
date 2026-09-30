-- Provisional starting grid published before the race (penalties applied), from OpenF1.
-- race_results.starting_grid_position only arrives with the race results; forecasts made on
-- race day use this table when it is filled (fetch_starting_grid_v1.py).
CREATE TABLE IF NOT EXISTS race_provisional_grid (
    race_id INTEGER NOT NULL REFERENCES races(id),
    race_entry_id INTEGER NOT NULL REFERENCES race_entries(id),
    grid_position INTEGER NOT NULL,
    source VARCHAR(30) NOT NULL DEFAULT 'openf1',
    fetched_at TIMESTAMP NOT NULL DEFAULT now(),
    PRIMARY KEY (race_id, race_entry_id)
);
