CREATE TABLE generation_activation (
 tenant_id uuid NOT NULL,generation_id uuid NOT NULL,activation_id uuid NOT NULL,
 PRIMARY KEY(tenant_id,generation_id),UNIQUE(tenant_id,activation_id),
 FOREIGN KEY(tenant_id,generation_id) REFERENCES source_generation(tenant_id,id),
 FOREIGN KEY(tenant_id,activation_id) REFERENCES source_activation(tenant_id,id));
ALTER TABLE generation_activation ENABLE ROW LEVEL SECURITY;
ALTER TABLE generation_activation FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_boundary ON generation_activation TO hotel_app
 USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
 WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
GRANT SELECT,INSERT ON generation_activation TO hotel_app;
UPDATE schema_revision SET version='006';
