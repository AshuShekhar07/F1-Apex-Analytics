-- Starting tyres declared before the start (start_tyres_v1): one row per car, from OpenF1's
-- first stint or entered by hand. Used by the pre-start winning strategy.
CREATE TABLE IF NOT EXISTS race_start_tyres (
    race_id INTEGER NOT NULL REFERENCES races(id),
    race_entry_id INTEGER NOT NULL REFERENCES race_entries(id),
    compound VARCHAR(20) NOT NULL,
    source VARCHAR(20) NOT NULL,
    fetched_at TIMESTAMP NOT NULL DEFAULT now(),
    PRIMARY KEY (race_id, race_entry_id)
);
