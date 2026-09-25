-- One race entry per driver per race weekend.
--
-- Run fix_duplicate_race_entries_v1.py --apply FIRST; this constraint fails
-- (and changes nothing) while any duplicate (race_id, driver_id) remains.
-- Idempotent: skipped when the constraint already exists.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'race_entries_race_driver_key'
          AND conrelid = 'race_entries'::regclass
    ) THEN
        ALTER TABLE race_entries
            ADD CONSTRAINT race_entries_race_driver_key UNIQUE (race_id, driver_id);
    END IF;
END $$;
