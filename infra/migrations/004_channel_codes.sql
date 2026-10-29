CREATE TABLE channel_mapping (
 id uuid PRIMARY KEY,tenant_id uuid NOT NULL,channel_id uuid NOT NULL,
 property_id uuid,room_id uuid,plan_id uuid,external_code text NOT NULL,
 CHECK(num_nonnulls(property_id,room_id,plan_id)=1),UNIQUE(tenant_id,id),UNIQUE(tenant_id,channel_id,external_code),
 FOREIGN KEY(tenant_id,channel_id) REFERENCES channel_account(tenant_id,id),
 FOREIGN KEY(tenant_id,property_id) REFERENCES property(tenant_id,id),
 FOREIGN KEY(tenant_id,room_id) REFERENCES room_type(tenant_id,id),
 FOREIGN KEY(tenant_id,plan_id) REFERENCES rate_plan(tenant_id,id));
CREATE UNIQUE INDEX channel_property_unique ON channel_mapping(tenant_id,channel_id,property_id) WHERE property_id IS NOT NULL;
CREATE UNIQUE INDEX channel_room_unique ON channel_mapping(tenant_id,channel_id,room_id) WHERE room_id IS NOT NULL;
CREATE UNIQUE INDEX channel_plan_unique ON channel_mapping(tenant_id,channel_id,plan_id) WHERE plan_id IS NOT NULL;
ALTER TABLE channel_mapping ENABLE ROW LEVEL SECURITY;
ALTER TABLE channel_mapping FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_boundary ON channel_mapping TO hotel_app
 USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
 WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
GRANT SELECT,INSERT ON channel_mapping TO hotel_app;
UPDATE schema_revision SET version='004';
