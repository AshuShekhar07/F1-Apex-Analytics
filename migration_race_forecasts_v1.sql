-- Stored pre-race forecasts (race_forecast_v1.py writes, /races/{id}/forecast reads).
--
-- A forecast is computed once qualifying is known, from races strictly before
-- the race date. `inputs` keeps the exact simulator snapshot so /what-if can
-- replay scenarios quickly and every forecast is reproducible; `validation`
-- records the measured walk-forward accuracy of each method shown.

CREATE TABLE IF NOT EXISTS race_forecasts (
    id SERIAL PRIMARY KEY,
    race_id INTEGER NOT NULL REFERENCES races(id),
    model_version VARCHAR(30) NOT NULL,
    as_of_date DATE NOT NULL,
    grid_source VARCHAR(20) NOT NULL,
    computed_at TIMESTAMP NOT NULL DEFAULT now(),
    inputs JSONB NOT NULL,
    validation JSONB NOT NULL,
    UNIQUE (race_id, model_version)
);

CREATE TABLE IF NOT EXISTS race_forecast_drivers (
    forecast_id INTEGER NOT NULL REFERENCES race_forecasts(id) ON DELETE CASCADE,
    driver_id INTEGER NOT NULL REFERENCES drivers(id),
    grid INTEGER NOT NULL,
    predicted_position INTEGER NOT NULL,
    p_win NUMERIC(6,4) NOT NULL,
    p_podium NUMERIC(6,4) NOT NULL,
    strategy JSONB NOT NULL,
    PRIMARY KEY (forecast_id, driver_id)
);
