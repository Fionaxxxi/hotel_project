CREATE TABLE g3_account (
 id uuid PRIMARY KEY,tenant_id uuid NOT NULL,channel_id uuid NOT NULL,code text NOT NULL,
 capability text NOT NULL,state text NOT NULL DEFAULT 'ACTIVE',version int NOT NULL DEFAULT 1,
 next_write_at timestamptz NOT NULL DEFAULT now(),next_read_at timestamptz NOT NULL DEFAULT now(),
 cooldown_until timestamptz NOT NULL DEFAULT now(),UNIQUE(tenant_id,id),UNIQUE(tenant_id,code),
 FOREIGN KEY(tenant_id,channel_id) REFERENCES channel_account(tenant_id,id));
CREATE TABLE g3_allocation (
 id uuid PRIMARY KEY,tenant_id uuid NOT NULL,source_key text NOT NULL,activation_id uuid NOT NULL,
 version int NOT NULL,buffer int NOT NULL CHECK(buffer>=0),amounts jsonb NOT NULL,capacity jsonb NOT NULL,
 created_by text NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,source_key,version),
 FOREIGN KEY(tenant_id,activation_id) REFERENCES source_activation(tenant_id,id));
CREATE TABLE g3_source_control (
 id uuid PRIMARY KEY,tenant_id uuid NOT NULL,source_key text NOT NULL,activation_id uuid NOT NULL,
 legacy_state text NOT NULL DEFAULT 'UNKNOWN',proof jsonb NOT NULL DEFAULT '{}',
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,source_key),
 FOREIGN KEY(tenant_id,activation_id) REFERENCES source_activation(tenant_id,id));
CREATE TABLE g3_allocation_head (
 id uuid PRIMARY KEY,tenant_id uuid NOT NULL,source_key text NOT NULL,allocation_id uuid NOT NULL,
 version int NOT NULL,UNIQUE(tenant_id,id),UNIQUE(tenant_id,source_key),
 FOREIGN KEY(tenant_id,allocation_id) REFERENCES g3_allocation(tenant_id,id));
CREATE TABLE g3_snapshot (
 id uuid PRIMARY KEY,tenant_id uuid NOT NULL,account_id uuid NOT NULL,allocation_id uuid NOT NULL,
 base_snapshot_id uuid NOT NULL,source_key text NOT NULL,listing_key text NOT NULL,
 payload jsonb NOT NULL,payload_hash text NOT NULL,inputs jsonb NOT NULL,
 state text NOT NULL DEFAULT 'SUBMITTED',version int NOT NULL DEFAULT 1,created_by text NOT NULL,
 reviewer text,reason text,allow_repair boolean NOT NULL DEFAULT false,
 expires_at timestamptz NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id),FOREIGN KEY(tenant_id,account_id) REFERENCES g3_account(tenant_id,id),
 FOREIGN KEY(tenant_id,allocation_id) REFERENCES g3_allocation(tenant_id,id),
 FOREIGN KEY(tenant_id,base_snapshot_id) REFERENCES offer_snapshot(tenant_id,id));
CREATE TABLE g3_listing (
 id uuid PRIMARY KEY,tenant_id uuid NOT NULL,account_id uuid NOT NULL,source_key text NOT NULL,
 listing_key text NOT NULL,desired_revision bigint NOT NULL DEFAULT 0,delivery_revision bigint NOT NULL DEFAULT 0,
 stop_epoch bigint NOT NULL DEFAULT 0,version int NOT NULL DEFAULT 1,state text NOT NULL DEFAULT 'UNPUBLISHED',
 intent_id uuid,applied_revision bigint,observed_revision bigint,next_due_at timestamptz NOT NULL DEFAULT now(),
 paused boolean NOT NULL DEFAULT false,UNIQUE(tenant_id,id),UNIQUE(tenant_id,account_id,listing_key),
 FOREIGN KEY(tenant_id,account_id) REFERENCES g3_account(tenant_id,id));
CREATE TABLE g3_reservation (
 id uuid PRIMARY KEY,tenant_id uuid NOT NULL,source_key text NOT NULL,account_id uuid NOT NULL,
 pool_day text NOT NULL,exposure int NOT NULL CHECK(exposure>=0),
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,source_key,account_id,pool_day),
 FOREIGN KEY(tenant_id,account_id) REFERENCES g3_account(tenant_id,id));
CREATE TABLE g3_plan (
 id uuid PRIMARY KEY,tenant_id uuid NOT NULL,command_key text NOT NULL,request_hash text NOT NULL,
 state text NOT NULL DEFAULT 'RUNNING',created_by text NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,command_key));
CREATE TABLE g3_intent (
 id uuid PRIMARY KEY,tenant_id uuid NOT NULL,listing_id uuid NOT NULL,snapshot_id uuid NOT NULL,
 plan_id uuid,action text NOT NULL CHECK(action IN ('PUBLISH','REPAIR','STOP')),
 desired_revision bigint NOT NULL,delivery_revision bigint NOT NULL,stop_epoch bigint NOT NULL,
 payload jsonb NOT NULL,payload_hash text NOT NULL,manifest jsonb NOT NULL,manifest_hash text NOT NULL,
 state text NOT NULL DEFAULT 'READY',created_by text NOT NULL,reason text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),UNIQUE(tenant_id,id),
 UNIQUE(tenant_id,listing_id,delivery_revision),
 FOREIGN KEY(tenant_id,listing_id) REFERENCES g3_listing(tenant_id,id),
 FOREIGN KEY(tenant_id,snapshot_id) REFERENCES g3_snapshot(tenant_id,id),
 FOREIGN KEY(tenant_id,plan_id) REFERENCES g3_plan(tenant_id,id));
ALTER TABLE g3_listing ADD FOREIGN KEY(tenant_id,intent_id) REFERENCES g3_intent(tenant_id,id);
CREATE TABLE g3_task (
 id uuid PRIMARY KEY,tenant_id uuid NOT NULL,intent_id uuid NOT NULL,chunk_index int NOT NULL,
 action text NOT NULL,request_key text NOT NULL,body jsonb NOT NULL,request_hash text NOT NULL,
 state text NOT NULL DEFAULT 'READY',send_count int NOT NULL DEFAULT 0,query_count int NOT NULL DEFAULT 0,
 budget_cycle int NOT NULL DEFAULT 1,error text,remote_operation text,
 deadline timestamptz NOT NULL DEFAULT now()+interval '15 minutes',version int NOT NULL DEFAULT 1,
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,intent_id,chunk_index),UNIQUE(request_key),
 FOREIGN KEY(tenant_id,intent_id) REFERENCES g3_intent(tenant_id,id));
CREATE TABLE g3_receipt (
 id uuid PRIMARY KEY,tenant_id uuid NOT NULL,task_id uuid NOT NULL,phase text NOT NULL,
 status int NOT NULL,data jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id),FOREIGN KEY(tenant_id,task_id) REFERENCES g3_task(tenant_id,id));
CREATE TABLE g3_cell_result (
 id uuid PRIMARY KEY,tenant_id uuid NOT NULL,task_id uuid NOT NULL,cell_key text NOT NULL,
 state text NOT NULL,code text,UNIQUE(tenant_id,id),UNIQUE(tenant_id,task_id,cell_key),
 FOREIGN KEY(tenant_id,task_id) REFERENCES g3_task(tenant_id,id));
CREATE TABLE g3_observation (
 id uuid PRIMARY KEY,tenant_id uuid NOT NULL,listing_id uuid NOT NULL,desired_revision bigint NOT NULL,
 classification text NOT NULL,complete boolean NOT NULL,data jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),UNIQUE(tenant_id,id),
 FOREIGN KEY(tenant_id,listing_id) REFERENCES g3_listing(tenant_id,id));
CREATE TABLE g3_diff (
 id uuid PRIMARY KEY,tenant_id uuid NOT NULL,listing_id uuid NOT NULL,observation_id uuid NOT NULL,
 desired_revision bigint NOT NULL,classification text NOT NULL,cells jsonb NOT NULL,
 state text NOT NULL DEFAULT 'OPEN',repair_intent_id uuid,created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id),FOREIGN KEY(tenant_id,listing_id) REFERENCES g3_listing(tenant_id,id),
 FOREIGN KEY(tenant_id,observation_id) REFERENCES g3_observation(tenant_id,id),
 FOREIGN KEY(tenant_id,repair_intent_id) REFERENCES g3_intent(tenant_id,id));
CREATE TABLE g3_stop (
 id uuid PRIMARY KEY,tenant_id uuid NOT NULL,listing_id uuid NOT NULL,command_key text NOT NULL,
 request_hash text NOT NULL,intent_id uuid NOT NULL,reason text NOT NULL,created_by text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),UNIQUE(tenant_id,id),UNIQUE(tenant_id,listing_id,command_key),
 FOREIGN KEY(tenant_id,listing_id) REFERENCES g3_listing(tenant_id,id),
 FOREIGN KEY(tenant_id,intent_id) REFERENCES g3_intent(tenant_id,id));
CREATE TABLE g3_dead_letter (
 id uuid PRIMARY KEY,tenant_id uuid NOT NULL,task_id uuid NOT NULL,cycle int NOT NULL,
 code text NOT NULL,state text NOT NULL DEFAULT 'OPEN',created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,task_id,cycle),
 FOREIGN KEY(tenant_id,task_id) REFERENCES g3_task(tenant_id,id));
CREATE INDEX g3_due ON g3_listing(tenant_id,next_due_at);
CREATE FUNCTION g3_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF (to_jsonb(NEW)-TG_ARGV::text[]) IS DISTINCT FROM (to_jsonb(OLD)-TG_ARGV::text[])
 THEN RAISE EXCEPTION 'immutable G3 business fields';END IF;RETURN NEW;END $$;
CREATE TRIGGER g3_snapshot_immutable BEFORE UPDATE ON g3_snapshot FOR EACH ROW
 EXECUTE FUNCTION g3_immutable('state','version','reviewer','reason','allow_repair');
CREATE TRIGGER g3_intent_immutable BEFORE UPDATE ON g3_intent FOR EACH ROW EXECUTE FUNCTION g3_immutable('state');
CREATE TRIGGER g3_task_immutable BEFORE UPDATE ON g3_task FOR EACH ROW
 EXECUTE FUNCTION g3_immutable('state','send_count','query_count','budget_cycle','error','remote_operation','deadline','version');
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['g3_source_control','g3_account','g3_allocation','g3_allocation_head','g3_snapshot','g3_listing',
 'g3_reservation','g3_plan','g3_intent','g3_task','g3_receipt','g3_cell_result','g3_observation','g3_diff','g3_stop','g3_dead_letter'] LOOP
 EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',t);
 EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',t);
 EXECUTE format('CREATE POLICY tenant_scope ON %I USING(tenant_id=nullif(current_setting(''app.tenant_id'',true),'''')::uuid) WITH CHECK(tenant_id=nullif(current_setting(''app.tenant_id'',true),'''')::uuid)',t);
 EXECUTE format('GRANT SELECT,INSERT ON %I TO hotel_app',t);
 END LOOP;END $$;
GRANT UPDATE ON g3_source_control,g3_account,g3_allocation_head,g3_snapshot,g3_listing,g3_reservation,g3_plan,g3_intent,g3_task,g3_diff,g3_dead_letter TO hotel_app;
