ALTER TABLE g5_worker_heartbeat ADD COLUMN slot text NOT NULL DEFAULT '';
UPDATE schema_revision SET version='017';
