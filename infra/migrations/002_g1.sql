CREATE TABLE import_batch (
 id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenant(id), supplier_id uuid NOT NULL,
 package_hash text NOT NULL, manifest jsonb NOT NULL, status text NOT NULL DEFAULT 'RECEIVED',
 version integer NOT NULL DEFAULT 1, issues jsonb NOT NULL DEFAULT '[]', created_by text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id), UNIQUE(tenant_id,supplier_id,package_hash),
 FOREIGN KEY(tenant_id,supplier_id) REFERENCES supplier(tenant_id,id));
CREATE TABLE artifact (
 id uuid PRIMARY KEY, tenant_id uuid NOT NULL, batch_id uuid NOT NULL, name text NOT NULL,
 storage_key text NOT NULL UNIQUE, sha256 text NOT NULL, size integer NOT NULL CHECK(size>=0),
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,batch_id,name),
 FOREIGN KEY(tenant_id,batch_id) REFERENCES import_batch(tenant_id,id));
CREATE TABLE staging_record (
 id uuid PRIMARY KEY, tenant_id uuid NOT NULL, batch_id uuid NOT NULL, artifact_id uuid NOT NULL,
 kind text NOT NULL, record_number integer NOT NULL CHECK(record_number>=2), data jsonb NOT NULL,
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,batch_id,kind,record_number),
 FOREIGN KEY(tenant_id,batch_id) REFERENCES import_batch(tenant_id,id),
 FOREIGN KEY(tenant_id,artifact_id) REFERENCES artifact(tenant_id,id));
CREATE TABLE source_activation (
 id uuid PRIMARY KEY, tenant_id uuid NOT NULL, batch_id uuid NOT NULL, supplier_id uuid NOT NULL,
 property_code text NOT NULL, scope jsonb NOT NULL, source_sequence bigint NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), activated_by text NOT NULL,
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,batch_id),
 FOREIGN KEY(tenant_id,batch_id) REFERENCES import_batch(tenant_id,id),
 FOREIGN KEY(tenant_id,supplier_id) REFERENCES supplier(tenant_id,id));
CREATE TABLE source_partition (
 id uuid PRIMARY KEY, tenant_id uuid NOT NULL, supplier_id uuid NOT NULL, property_code text NOT NULL,
 scope_hash text NOT NULL, activation_id uuid NOT NULL, version integer NOT NULL DEFAULT 1,
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,supplier_id,property_code,scope_hash),
 FOREIGN KEY(tenant_id,supplier_id) REFERENCES supplier(tenant_id,id),
 FOREIGN KEY(tenant_id,activation_id) REFERENCES source_activation(tenant_id,id));
CREATE TABLE supplier_entity_version (
 id uuid PRIMARY KEY, tenant_id uuid NOT NULL, activation_id uuid NOT NULL, staging_id uuid NOT NULL,
 kind text NOT NULL CHECK(kind IN ('property','room','rate')), natural_key text NOT NULL,
 parent_key text, data jsonb NOT NULL, facts_hash text NOT NULL,
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,activation_id,kind,natural_key),
 FOREIGN KEY(tenant_id,activation_id) REFERENCES source_activation(tenant_id,id),
 FOREIGN KEY(tenant_id,staging_id) REFERENCES staging_record(tenant_id,id));
CREATE TABLE inventory_pool (
 id uuid PRIMARY KEY, tenant_id uuid NOT NULL, activation_id uuid NOT NULL,
 property_code text NOT NULL, room_code text NOT NULL, pool_code text NOT NULL,
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,activation_id,property_code,room_code,pool_code),
 FOREIGN KEY(tenant_id,activation_id) REFERENCES source_activation(tenant_id,id));
CREATE TABLE inventory_cell (
 id uuid PRIMARY KEY, tenant_id uuid NOT NULL, pool_id uuid NOT NULL, staging_id uuid NOT NULL,
 stay_date date NOT NULL, quantity integer NOT NULL CHECK(quantity>=0), stop_sell boolean NOT NULL,
 observed_at timestamptz NOT NULL, UNIQUE(tenant_id,id), UNIQUE(tenant_id,pool_id,stay_date),
 FOREIGN KEY(tenant_id,pool_id) REFERENCES inventory_pool(tenant_id,id),
 FOREIGN KEY(tenant_id,staging_id) REFERENCES staging_record(tenant_id,id));
CREATE TABLE plan_pool_link (
 id uuid PRIMARY KEY, tenant_id uuid NOT NULL, source_plan_id uuid NOT NULL, pool_id uuid NOT NULL,
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,source_plan_id),
 FOREIGN KEY(tenant_id,source_plan_id) REFERENCES supplier_entity_version(tenant_id,id),
 FOREIGN KEY(tenant_id,pool_id) REFERENCES inventory_pool(tenant_id,id));
CREATE TABLE rate_cell (
 id uuid PRIMARY KEY, tenant_id uuid NOT NULL, source_plan_id uuid NOT NULL, staging_id uuid NOT NULL,
 stay_date date NOT NULL, adults integer NOT NULL CHECK(adults=2), currency text NOT NULL CHECK(currency='USD'),
 net_amount numeric(18,4) NOT NULL CHECK(net_amount>0),
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,source_plan_id,stay_date,adults,currency),
 FOREIGN KEY(tenant_id,source_plan_id) REFERENCES supplier_entity_version(tenant_id,id),
 FOREIGN KEY(tenant_id,staging_id) REFERENCES staging_record(tenant_id,id));

DO $$ DECLARE k text; target text; BEGIN
 FOR k,target IN SELECT * FROM (VALUES('property','property'),('room','room_type'),('rate','rate_plan')) AS v(k,t) LOOP
 EXECUTE format('CREATE TABLE %I_mapping (
 id uuid PRIMARY KEY, tenant_id uuid NOT NULL, source_id uuid NOT NULL, target_id uuid NOT NULL,
 target_version_id uuid NOT NULL, status text NOT NULL, version integer NOT NULL DEFAULT 1,
 actor text NOT NULL, reason text NOT NULL, UNIQUE(tenant_id,id), UNIQUE(tenant_id,source_id),
 FOREIGN KEY(tenant_id,source_id) REFERENCES supplier_entity_version(tenant_id,id),
 FOREIGN KEY(tenant_id,target_id) REFERENCES %I(tenant_id,id))',k,target);
 END LOOP; END $$;
ALTER TABLE property_mapping ADD FOREIGN KEY(tenant_id,target_id,target_version_id) REFERENCES property_version(tenant_id,property_id,id);
ALTER TABLE room_mapping ADD FOREIGN KEY(tenant_id,target_id,target_version_id) REFERENCES room_version(tenant_id,room_id,id);
ALTER TABLE rate_mapping ADD FOREIGN KEY(tenant_id,target_id,target_version_id) REFERENCES policy_version(tenant_id,plan_id,id);
CREATE TABLE mapping_decision (
 id uuid PRIMARY KEY, tenant_id uuid NOT NULL, source_id uuid NOT NULL, kind text NOT NULL,
 decision text NOT NULL, target_id uuid NOT NULL, target_version_id uuid NOT NULL,
 actor text NOT NULL, reason text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id), FOREIGN KEY(tenant_id,source_id) REFERENCES supplier_entity_version(tenant_id,id));
CREATE TABLE channel_account (
 id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenant(id), code text NOT NULL,
 capability_version text NOT NULL DEFAULT 'sim-a-v1', UNIQUE(tenant_id,id), UNIQUE(tenant_id,code));
CREATE TABLE offer_snapshot (
 id uuid PRIMARY KEY, tenant_id uuid NOT NULL, activation_id uuid NOT NULL, channel_id uuid NOT NULL,
 listing_key text NOT NULL, revision bigint NOT NULL, input_versions jsonb NOT NULL,
 payload jsonb NOT NULL, payload_hash text NOT NULL, status text NOT NULL DEFAULT 'COMPILED',
 version integer NOT NULL DEFAULT 1, submitted_by text, expires_at timestamptz NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id), UNIQUE(tenant_id,channel_id,listing_key,revision),
 FOREIGN KEY(tenant_id,activation_id) REFERENCES source_activation(tenant_id,id),
 FOREIGN KEY(tenant_id,channel_id) REFERENCES channel_account(tenant_id,id));
CREATE TABLE approval (
 id uuid PRIMARY KEY, tenant_id uuid NOT NULL, snapshot_id uuid NOT NULL, payload_hash text NOT NULL,
 actor text NOT NULL, decision text NOT NULL, reason text NOT NULL, expires_at timestamptz NOT NULL,
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,snapshot_id),
 FOREIGN KEY(tenant_id,snapshot_id) REFERENCES offer_snapshot(tenant_id,id));
CREATE TABLE publish_task (
 id uuid PRIMARY KEY, tenant_id uuid NOT NULL, snapshot_id uuid NOT NULL, idempotency_key text NOT NULL UNIQUE,
 status text NOT NULL DEFAULT 'READY', error_code text, attempt integer NOT NULL DEFAULT 0,
 created_by text NOT NULL, remote_operation_id uuid, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,snapshot_id),
 FOREIGN KEY(tenant_id,snapshot_id) REFERENCES offer_snapshot(tenant_id,id));
CREATE TABLE task_attempt (
 id uuid PRIMARY KEY, tenant_id uuid NOT NULL, task_id uuid NOT NULL, attempt integer NOT NULL,
 request_hash text NOT NULL, outcome text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id), FOREIGN KEY(tenant_id,task_id) REFERENCES publish_task(tenant_id,id));
CREATE TABLE channel_receipt (
 id uuid PRIMARY KEY, tenant_id uuid NOT NULL, task_id uuid NOT NULL, data jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id),
 FOREIGN KEY(tenant_id,task_id) REFERENCES publish_task(tenant_id,id));

CREATE FUNCTION g1_snapshot_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF (NEW.payload,NEW.payload_hash,NEW.input_versions,NEW.activation_id,NEW.channel_id,NEW.listing_key,NEW.revision)
 IS DISTINCT FROM (OLD.payload,OLD.payload_hash,OLD.input_versions,OLD.activation_id,OLD.channel_id,OLD.listing_key,OLD.revision)
 THEN RAISE EXCEPTION 'immutable snapshot'; END IF; RETURN NEW; END $$;
CREATE TRIGGER immutable_snapshot BEFORE UPDATE ON offer_snapshot FOR EACH ROW EXECUTE FUNCTION g1_snapshot_immutable();
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['import_batch','artifact','staging_record','source_activation','source_partition',
 'supplier_entity_version','inventory_pool','inventory_cell','plan_pool_link','rate_cell',
 'property_mapping','room_mapping','rate_mapping','mapping_decision','channel_account','offer_snapshot',
 'approval','publish_task','task_attempt','channel_receipt'] LOOP
 EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',t);
 EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',t);
 EXECUTE format('CREATE POLICY tenant_boundary ON %I TO hotel_app USING(tenant_id=nullif(current_setting(''app.tenant_id'',true),'''')::uuid AND EXISTS(SELECT 1 FROM tenant WHERE id=tenant_id)) WITH CHECK(tenant_id=nullif(current_setting(''app.tenant_id'',true),'''')::uuid AND EXISTS(SELECT 1 FROM tenant WHERE id=tenant_id))',t);
 EXECUTE format('GRANT SELECT,INSERT ON %I TO hotel_app',t);
 END LOOP; END $$;
GRANT UPDATE ON import_batch,source_partition,property_mapping,room_mapping,rate_mapping,offer_snapshot,publish_task TO hotel_app;
UPDATE schema_revision SET version='002';
