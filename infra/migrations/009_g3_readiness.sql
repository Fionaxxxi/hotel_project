-- 008 introduced the model; publish the readiness marker only after all G3 tables exist.
UPDATE schema_revision SET version='009';
