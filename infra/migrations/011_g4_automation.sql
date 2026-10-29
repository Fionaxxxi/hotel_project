CREATE TABLE g4_policy (
 id uuid PRIMARY KEY,tenant_id uuid NOT NULL,source_key text NOT NULL,body jsonb NOT NULL,
 body_hash text NOT NULL,baseline_ids jsonb NOT NULL,created_by text NOT NULL,
 state text NOT NULL DEFAULT 'DRAFT',reviewer text,reason text,epoch int NOT NULL DEFAULT 1,
 version int NOT NULL DEFAULT 1,expires_at timestamptz NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id));
CREATE UNIQUE INDEX g4_one_enabled_policy ON g4_policy(tenant_id,source_key) WHERE state='ENABLED';
CREATE TABLE g4_subscription (
 id uuid PRIMARY KEY,tenant_id uuid NOT NULL,source_key text NOT NULL,policy_id uuid NOT NULL,
 initial_activation_id uuid NOT NULL,created_by text NOT NULL,state text NOT NULL DEFAULT 'ACTIVE',
 version int NOT NULL DEFAULT 1,created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,source_key),
 FOREIGN KEY(tenant_id,policy_id) REFERENCES g4_policy(tenant_id,id),
 FOREIGN KEY(tenant_id,initial_activation_id) REFERENCES source_activation(tenant_id,id));
CREATE TABLE g4_run (
 id uuid PRIMARY KEY,tenant_id uuid NOT NULL,subscription_id uuid NOT NULL,processing_id uuid NOT NULL,
 definition text NOT NULL DEFAULT 'g4-flow-v1',state text NOT NULL DEFAULT 'PROCESSING',
 activation_id uuid,allocation_id uuid,snapshot_ids jsonb NOT NULL DEFAULT '[]',plan_id uuid,
 reason text,version int NOT NULL DEFAULT 1,created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,processing_id),
 FOREIGN KEY(tenant_id,subscription_id) REFERENCES g4_subscription(tenant_id,id),
 FOREIGN KEY(tenant_id,processing_id) REFERENCES processing_run(tenant_id,id),
 FOREIGN KEY(tenant_id,activation_id) REFERENCES source_activation(tenant_id,id),
 FOREIGN KEY(tenant_id,allocation_id) REFERENCES g3_allocation(tenant_id,id),
 FOREIGN KEY(tenant_id,plan_id) REFERENCES g3_plan(tenant_id,id));
CREATE TABLE g4_step (
 id uuid PRIMARY KEY,tenant_id uuid NOT NULL,run_id uuid NOT NULL,step_key text NOT NULL,
 input_hash text NOT NULL,state text NOT NULL,detail jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),UNIQUE(tenant_id,id),
 UNIQUE(tenant_id,run_id,step_key,input_hash,state),
 FOREIGN KEY(tenant_id,run_id) REFERENCES g4_run(tenant_id,id));
CREATE TABLE g4_mapping_reuse (
 id uuid PRIMARY KEY,tenant_id uuid NOT NULL,source_id uuid NOT NULL,previous_source_id uuid NOT NULL,
 fingerprint text NOT NULL,target_version_id uuid NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,source_id),
 FOREIGN KEY(tenant_id,source_id) REFERENCES supplier_entity_version(tenant_id,id),
 FOREIGN KEY(tenant_id,previous_source_id) REFERENCES supplier_entity_version(tenant_id,id));
CREATE TABLE g4_decision (
 id uuid PRIMARY KEY,tenant_id uuid NOT NULL,policy_id uuid NOT NULL,snapshot_id uuid NOT NULL,
 epoch int NOT NULL,payload_hash text NOT NULL,input_hash text NOT NULL,result text NOT NULL,
 reasons jsonb NOT NULL,actor text NOT NULL DEFAULT 'system.policy',expires_at timestamptz NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),UNIQUE(tenant_id,id),
 UNIQUE(tenant_id,snapshot_id,policy_id,epoch,input_hash),
 FOREIGN KEY(tenant_id,policy_id) REFERENCES g4_policy(tenant_id,id),
 FOREIGN KEY(tenant_id,snapshot_id) REFERENCES g3_snapshot(tenant_id,id));
CREATE TABLE g4_ai_task (
 id uuid PRIMARY KEY,tenant_id uuid NOT NULL,source_id uuid NOT NULL,input_hash text NOT NULL,
 input jsonb NOT NULL,state text NOT NULL DEFAULT 'READY',result jsonb,
 owner text,fence int NOT NULL DEFAULT 0,lease_until timestamptz,attempts int NOT NULL DEFAULT 0,
 created_at timestamptz NOT NULL DEFAULT now(),UNIQUE(tenant_id,id),UNIQUE(tenant_id,input_hash),
 FOREIGN KEY(tenant_id,source_id) REFERENCES supplier_entity_version(tenant_id,id));
CREATE TABLE g4_ai_attempt (
 id uuid PRIMARY KEY,tenant_id uuid NOT NULL,task_id uuid NOT NULL,fence int NOT NULL,
 status text NOT NULL,detail jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id),FOREIGN KEY(tenant_id,task_id) REFERENCES g4_ai_task(tenant_id,id));
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['g4_policy','g4_subscription','g4_run','g4_step','g4_mapping_reuse','g4_decision','g4_ai_task','g4_ai_attempt'] LOOP
 EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',t);
 EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',t);
 EXECUTE format('CREATE POLICY tenant_scope ON %I USING(tenant_id=nullif(current_setting(''app.tenant_id'',true),'''')::uuid) WITH CHECK(tenant_id=nullif(current_setting(''app.tenant_id'',true),'''')::uuid)',t);
 EXECUTE format('GRANT SELECT,INSERT ON %I TO hotel_app',t);
 END LOOP;END $$;
GRANT UPDATE ON g4_policy,g4_subscription,g4_run,g4_ai_task TO hotel_app;
CREATE TRIGGER g4_policy_immutable BEFORE UPDATE ON g4_policy FOR EACH ROW
 EXECUTE FUNCTION g3_immutable('state','reviewer','reason','epoch','version');
CREATE TRIGGER g4_subscription_immutable BEFORE UPDATE ON g4_subscription FOR EACH ROW
 EXECUTE FUNCTION g3_immutable('state','version');
CREATE TRIGGER g4_ai_immutable BEFORE UPDATE ON g4_ai_task FOR EACH ROW
 EXECUTE FUNCTION g3_immutable('state','result','owner','fence','lease_until','attempts');
INSERT INTO schema_revision(version) VALUES('011');
