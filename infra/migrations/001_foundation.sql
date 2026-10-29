CREATE TABLE schema_revision(version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE tenant(id uuid PRIMARY KEY, name text NOT NULL, status text NOT NULL CHECK(status IN ('active','disabled')));
CREATE TABLE app_user(id uuid PRIMARY KEY, subject text NOT NULL UNIQUE);
CREATE TABLE membership(
 tenant_id uuid NOT NULL REFERENCES tenant(id), user_id uuid NOT NULL REFERENCES app_user(id),
 role text NOT NULL CHECK(role IN ('tenant_admin','operator','reviewer','auditor')),
 status text NOT NULL CHECK(status IN ('active','disabled')), PRIMARY KEY(tenant_id,user_id));
CREATE TABLE supplier(
 id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenant(id), code text NOT NULL,
 name text NOT NULL, contract_version text NOT NULL, status text NOT NULL DEFAULT 'active',
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,code));
CREATE TABLE property(
 id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenant(id), current_version_id uuid,
 UNIQUE(tenant_id,id));
CREATE TABLE property_version(
 id uuid PRIMARY KEY, tenant_id uuid NOT NULL, property_id uuid NOT NULL, version integer NOT NULL CHECK(version>0),
 name text NOT NULL, country_code text NOT NULL, city text NOT NULL, address text NOT NULL,
 timezone text NOT NULL, lat numeric(9,6), lon numeric(9,6),
 CHECK((lat IS NULL)=(lon IS NULL)), CHECK(lat BETWEEN -90 AND 90), CHECK(lon BETWEEN -180 AND 180),
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,property_id,id), UNIQUE(tenant_id,property_id,version),
 FOREIGN KEY(tenant_id,property_id) REFERENCES property(tenant_id,id));
ALTER TABLE property ADD CONSTRAINT property_current_version_fk
 FOREIGN KEY(tenant_id,id,current_version_id) REFERENCES property_version(tenant_id,property_id,id)
 DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE room_type(
 id uuid PRIMARY KEY, tenant_id uuid NOT NULL, property_id uuid NOT NULL, current_version_id uuid,
 UNIQUE(tenant_id,id), FOREIGN KEY(tenant_id,property_id) REFERENCES property(tenant_id,id));
CREATE TABLE room_version(
 id uuid PRIMARY KEY, tenant_id uuid NOT NULL, room_id uuid NOT NULL, version integer NOT NULL CHECK(version>0),
 name text NOT NULL, bed_type text NOT NULL CHECK(bed_type IN ('KING','TWIN','UNKNOWN')),
 max_adults integer NOT NULL CHECK(max_adults>0),
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,room_id,id), UNIQUE(tenant_id,room_id,version),
 FOREIGN KEY(tenant_id,room_id) REFERENCES room_type(tenant_id,id));
ALTER TABLE room_type ADD CONSTRAINT room_current_version_fk
 FOREIGN KEY(tenant_id,id,current_version_id) REFERENCES room_version(tenant_id,room_id,id)
 DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE rate_plan(
 id uuid PRIMARY KEY, tenant_id uuid NOT NULL, room_id uuid NOT NULL, current_version_id uuid,
 UNIQUE(tenant_id,id), FOREIGN KEY(tenant_id,room_id) REFERENCES room_type(tenant_id,id));
CREATE TABLE policy_version(
 id uuid PRIMARY KEY, tenant_id uuid NOT NULL, plan_id uuid NOT NULL, version integer NOT NULL CHECK(version>0),
 schema_version text NOT NULL, meal text NOT NULL, cancellation jsonb NOT NULL,
 payment text NOT NULL, tax_basis text NOT NULL, raw_terms text NOT NULL,
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,plan_id,id), UNIQUE(tenant_id,plan_id,version),
 FOREIGN KEY(tenant_id,plan_id) REFERENCES rate_plan(tenant_id,id));
ALTER TABLE rate_plan ADD CONSTRAINT plan_current_version_fk
 FOREIGN KEY(tenant_id,id,current_version_id) REFERENCES policy_version(tenant_id,plan_id,id)
 DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE audit_event(
 id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenant(id), actor text NOT NULL,
 action text NOT NULL, resource_id uuid, trace_id uuid NOT NULL, reason text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id));

-- No SECURITY DEFINER helpers: identity policies expose only the authenticated subject.
ALTER TABLE app_user ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_user FORCE ROW LEVEL SECURITY;
CREATE POLICY user_self ON app_user FOR SELECT TO hotel_app
 USING(subject=current_setting('app.subject',true));
ALTER TABLE membership ENABLE ROW LEVEL SECURITY;
ALTER TABLE membership FORCE ROW LEVEL SECURITY;
CREATE POLICY membership_self ON membership FOR SELECT TO hotel_app
 USING(user_id IN (SELECT id FROM app_user WHERE subject=current_setting('app.subject',true)));
ALTER TABLE tenant ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_current ON tenant TO hotel_app
 USING(id=nullif(current_setting('app.tenant_id',true),'')::uuid AND status='active');

DO $$
DECLARE tbl text;
BEGIN
 FOREACH tbl IN ARRAY ARRAY['supplier','property','property_version','room_type','room_version','rate_plan','policy_version','audit_event'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',tbl);
  EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',tbl);
  EXECUTE format('CREATE POLICY tenant_boundary ON %I TO hotel_app USING (tenant_id=nullif(current_setting(''app.tenant_id'',true),'''')::uuid AND EXISTS (SELECT 1 FROM tenant WHERE id=tenant_id)) WITH CHECK (tenant_id=nullif(current_setting(''app.tenant_id'',true),'''')::uuid AND EXISTS (SELECT 1 FROM tenant WHERE id=tenant_id))',tbl);
 END LOOP;
END $$;
GRANT SELECT ON schema_revision,tenant,app_user,membership TO hotel_app;
GRANT SELECT,INSERT,UPDATE,DELETE ON supplier,property,room_type,rate_plan TO hotel_app;
GRANT SELECT,INSERT ON property_version,room_version,policy_version,audit_event TO hotel_app;
INSERT INTO schema_revision(version) VALUES('001');
