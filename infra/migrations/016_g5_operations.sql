CREATE TABLE g5_alert (
 id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenant(id),
 kind text NOT NULL, fingerprint text NOT NULL, state text NOT NULL DEFAULT 'OPEN'
 CHECK(state IN ('OPEN','ACKNOWLEDGED','RESOLVED')), threshold_seconds int NOT NULL,
 threshold_version text NOT NULL DEFAULT 'g5-v1', detail jsonb NOT NULL,
 first_seen timestamptz NOT NULL DEFAULT clock_timestamp(), last_seen timestamptz NOT NULL DEFAULT clock_timestamp(),
 resolved_at timestamptz, version int NOT NULL DEFAULT 1,
 UNIQUE(tenant_id,id));
CREATE UNIQUE INDEX g5_alert_active ON g5_alert(tenant_id,kind,fingerprint) WHERE state<>'RESOLVED';
CREATE TABLE g5_alert_event (
 id uuid PRIMARY KEY,tenant_id uuid NOT NULL,alert_id uuid NOT NULL,action text NOT NULL,
 actor text NOT NULL,reason text NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,id),FOREIGN KEY(tenant_id,alert_id) REFERENCES g5_alert(tenant_id,id));
CREATE TABLE g5_login_bucket (
 key text PRIMARY KEY, failures int NOT NULL DEFAULT 0,window_start timestamptz NOT NULL DEFAULT clock_timestamp());
GRANT SELECT,INSERT,UPDATE ON g5_login_bucket TO hotel_app;
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['g5_alert','g5_alert_event'] LOOP
 EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',t);
 EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',t);
 EXECUTE format('CREATE POLICY tenant_scope ON %I USING(tenant_id=nullif(current_setting(''app.tenant_id'',true),'''')::uuid) WITH CHECK(tenant_id=nullif(current_setting(''app.tenant_id'',true),'''')::uuid)',t);
 EXECUTE format('GRANT SELECT,INSERT ON %I TO hotel_app',t);
 END LOOP;END $$;
GRANT UPDATE ON g5_alert TO hotel_app;
CREATE TABLE g5_worker_heartbeat (
 tenant_id uuid NOT NULL REFERENCES tenant(id),worker_id uuid NOT NULL,lane text NOT NULL,
 last_seen timestamptz NOT NULL DEFAULT clock_timestamp(),PRIMARY KEY(tenant_id,worker_id));
ALTER TABLE g5_worker_heartbeat ENABLE ROW LEVEL SECURITY;
ALTER TABLE g5_worker_heartbeat FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON g5_worker_heartbeat
 USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
 WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
GRANT SELECT,INSERT,UPDATE ON g5_worker_heartbeat TO hotel_app;
CREATE INDEX g5_alert_recent ON g5_alert(tenant_id,last_seen DESC,id);
CREATE INDEX g5_audit_resource ON audit_event(tenant_id,resource_id,created_at,id);
ALTER TABLE execution_job ADD COLUMN trace_id uuid;
ALTER TABLE execution_job ADD COLUMN cause_event_id uuid;
ALTER TABLE execution_job ADD FOREIGN KEY(tenant_id,cause_event_id) REFERENCES outbox_event(tenant_id,id);
UPDATE schema_revision SET version='016';
