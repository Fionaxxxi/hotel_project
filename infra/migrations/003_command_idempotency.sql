CREATE TABLE command_idempotency (
 id uuid PRIMARY KEY,tenant_id uuid NOT NULL REFERENCES tenant(id),route text NOT NULL,key text NOT NULL,
 request_hash text NOT NULL,resource_id uuid NOT NULL,UNIQUE(tenant_id,id),UNIQUE(tenant_id,route,key));
ALTER TABLE command_idempotency ENABLE ROW LEVEL SECURITY;
ALTER TABLE command_idempotency FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_boundary ON command_idempotency TO hotel_app
 USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
 WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
GRANT SELECT,INSERT ON command_idempotency TO hotel_app;
UPDATE schema_revision SET version='003';
