CREATE TABLE g5_recovery_scope (
 tenant_id uuid NOT NULL REFERENCES tenant(id),source_key text NOT NULL,backup_id text NOT NULL,
 state text NOT NULL DEFAULT 'HELD' CHECK(state IN ('HELD','RELEASED')),
 reason text NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(tenant_id,source_key));
ALTER TABLE g5_recovery_scope ENABLE ROW LEVEL SECURITY;
ALTER TABLE g5_recovery_scope FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON g5_recovery_scope
 USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
 WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
GRANT SELECT,UPDATE ON g5_recovery_scope TO hotel_app;
UPDATE schema_revision SET version='018';
