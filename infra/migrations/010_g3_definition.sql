ALTER TABLE g3_plan ADD COLUMN definition text NOT NULL DEFAULT 'g3-flow-v1';
UPDATE schema_revision SET version='010';
