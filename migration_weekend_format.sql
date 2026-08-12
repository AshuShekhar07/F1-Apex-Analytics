ALTER TABLE races
    ADD COLUMN IF NOT EXISTS weekend_format VARCHAR(20) NOT NULL DEFAULT 'conventional';

CREATE TABLE IF NOT EXISTS expected_session_types (
    weekend_format VARCHAR(20) NOT NULL,
    session_type   VARCHAR(20) NOT NULL,
    display_order  INTEGER NOT NULL,
    PRIMARY KEY (weekend_format, session_type)
);

INSERT INTO expected_session_types (weekend_format, session_type, display_order) VALUES
    ('conventional',    'FP1', 1),
    ('conventional',    'FP2', 2),
    ('conventional',    'FP3', 3),
    ('conventional',    'Q',   4),
    ('conventional',    'R',   5),
    ('sprint_legacy',   'FP1', 1),
    ('sprint_legacy',   'FP2', 2),
    ('sprint_legacy',   'Q',   3),
    ('sprint_legacy',   'S',   4),
    ('sprint_legacy',   'R',   5),
    ('sprint_shootout', 'FP1', 1),
    ('sprint_shootout', 'SQ',  2),
    ('sprint_shootout', 'S',   3),
    ('sprint_shootout', 'Q',   4),
    ('sprint_shootout', 'R',   5)
ON CONFLICT (weekend_format, session_type) DO NOTHING;

CREATE OR REPLACE VIEW race_session_status AS
SELECT
    r.id                AS race_id,
    r.season_year,
    r.round_number,
    t.name              AS track_name,
    r.weekend_format,
    est.session_type,
    est.display_order,
    s.id                AS session_id,
    CASE
        WHEN s.id IS NULL THEN 'missing'
        WHEN lap_counts.lap_count IS NULL OR lap_counts.lap_count = 0 THEN 'empty'
        ELSE 'present'
    END AS status,
    COALESCE(lap_counts.lap_count, 0) AS lap_count
FROM races r
JOIN tracks t
    ON t.id = r.track_id
JOIN expected_session_types est
    ON est.weekend_format = r.weekend_format
LEFT JOIN sessions s
    ON s.race_id = r.id
    AND s.session_type = est.session_type
LEFT JOIN (
    SELECT session_id, COUNT(*) AS lap_count
    FROM laps
    GROUP BY session_id
) lap_counts
    ON lap_counts.session_id = s.id
ORDER BY r.season_year, r.round_number, est.display_order;
