-- Race-winner predictions frozen BEFORE the race (live_record_v1): one row per driver for
-- each stage. A stage is rewritten only while it is open (pre_qualifying before qualifying,
-- post_qualifying before the race) and never after the race has results.
CREATE TABLE IF NOT EXISTS winner_prediction_live (
    race_id INTEGER NOT NULL REFERENCES races(id),
    stage VARCHAR(20) NOT NULL CHECK (stage IN ('pre_qualifying', 'post_qualifying')),
    model_version VARCHAR(30) NOT NULL,
    driver_id INTEGER NOT NULL REFERENCES drivers(id),
    rank INTEGER NOT NULL,
    grid INTEGER,
    p_win NUMERIC(6,4) NOT NULL,
    p_podium NUMERIC(6,4) NOT NULL,
    confidence_level VARCHAR(10) NOT NULL,
    confidence_basis TEXT,
    frozen_at TIMESTAMP NOT NULL DEFAULT now(),
    PRIMARY KEY (race_id, stage, model_version, driver_id)
);
