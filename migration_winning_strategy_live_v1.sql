-- Predicted winning strategies frozen BEFORE the race (strategy_live_record_v1): one row per
-- stage. pre_practice is rewritten only while this weekend's practice tyre wear is unknown,
-- post_practice until the race has results; neither is touched once the race has results.
CREATE TABLE IF NOT EXISTS winning_strategy_live (
    race_id INTEGER NOT NULL REFERENCES races(id),
    stage VARCHAR(20) NOT NULL CHECK (stage IN ('pre_practice', 'post_practice')),
    model_version VARCHAR(30) NOT NULL,
    prediction JSONB NOT NULL,
    confidence_level VARCHAR(10) NOT NULL,
    frozen_at TIMESTAMP NOT NULL DEFAULT now(),
    PRIMARY KEY (race_id, stage, model_version)
);
