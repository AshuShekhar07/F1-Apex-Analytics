-- The forecast as it stood BEFORE the race: written while the race has no results and never
-- touched afterwards. race_forecasts is recomputed whenever the model improves, so only this
-- table is a true live record (prediction_scoreboard_v1 scores it as "live").
CREATE TABLE IF NOT EXISTS race_forecast_live (
    race_id INTEGER NOT NULL REFERENCES races(id),
    model_version VARCHAR(30) NOT NULL,
    driver_id INTEGER NOT NULL REFERENCES drivers(id),
    grid INTEGER NOT NULL,
    predicted_position INTEGER NOT NULL,
    p_win NUMERIC(6,4) NOT NULL,
    p_podium NUMERIC(6,4) NOT NULL,
    grid_source VARCHAR(20) NOT NULL,
    computed_at TIMESTAMP NOT NULL DEFAULT now(),
    PRIMARY KEY (race_id, model_version, driver_id)
);
