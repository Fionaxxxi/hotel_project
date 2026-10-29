CREATE TABLE g4_matching_feedback (
 id uuid PRIMARY KEY,tenant_id uuid NOT NULL,source_id uuid NOT NULL,source_hash text NOT NULL,
 kind text NOT NULL,target_id uuid NOT NULL,target_version_id uuid NOT NULL,decision text NOT NULL,
 actor text NOT NULL,reason text NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,id),FOREIGN KEY(tenant_id,source_id) REFERENCES supplier_entity_version(tenant_id,id));
ALTER TABLE g4_matching_feedback ENABLE ROW LEVEL SECURITY;
ALTER TABLE g4_matching_feedback FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON g4_matching_feedback
 USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
 WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
GRANT SELECT,INSERT ON g4_matching_feedback TO hotel_app;
CREATE INDEX g4_feedback_pair ON g4_matching_feedback(tenant_id,kind,source_hash,target_id,target_version_id,created_at DESC);
UPDATE schema_revision SET version='015';
