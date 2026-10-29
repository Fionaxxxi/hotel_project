ALTER TABLE g3_listing ADD COLUMN safety_due_at timestamptz NOT NULL DEFAULT clock_timestamp();
CREATE INDEX g5_safety_due ON g3_listing(tenant_id,safety_due_at) WHERE state<>'STOPPED';
REVOKE UPDATE ON g5_recovery_scope FROM hotel_app;
GRANT UPDATE(state,reason) ON g5_recovery_scope TO hotel_app;
CREATE TRIGGER g5_recovery_scope_immutable BEFORE UPDATE ON g5_recovery_scope
 FOR EACH ROW EXECUTE FUNCTION g3_immutable('state','reason');
UPDATE schema_revision SET version='019';
