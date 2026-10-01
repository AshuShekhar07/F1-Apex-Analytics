-- Title chances after each round (championship_model_v1.project_season), one row per driver or
-- team. A round's projection uses only the races up to that round, so the rows of a season
-- are the record of what the model said as the season went on.
CREATE TABLE IF NOT EXISTS championship_projections (
    season_year INTEGER NOT NULL,
    after_round INTEGER NOT NULL,
    model_version VARCHAR(30) NOT NULL,
    kind VARCHAR(12) NOT NULL CHECK (kind IN ('drivers', 'constructors')),
    entity_id INTEGER NOT NULL,
    p_title NUMERIC(6,4) NOT NULL,
    points_now NUMERIC(7,1) NOT NULL,
    expected_points NUMERIC(7,1) NOT NULL,
    weekends_left INTEGER NOT NULL,
    computed_at TIMESTAMP NOT NULL DEFAULT now(),
    PRIMARY KEY (season_year, after_round, model_version, kind, entity_id)
);
