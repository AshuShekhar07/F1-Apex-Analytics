-- One row per scheduled pipeline run (run_pipeline.py): /health reports the latest, and a
-- failed or stale run is visible without reading server logs.
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id SERIAL PRIMARY KEY,
    job VARCHAR(40) NOT NULL,
    started_at TIMESTAMP NOT NULL DEFAULT now(),
    finished_at TIMESTAMP,
    status VARCHAR(20) NOT NULL DEFAULT 'running',   -- running | ok | failed | skipped
    detail TEXT
);
CREATE INDEX IF NOT EXISTS pipeline_runs_started_idx ON pipeline_runs (started_at DESC);
