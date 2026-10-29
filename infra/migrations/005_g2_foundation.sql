CREATE TABLE processing_run (
 id uuid PRIMARY KEY,tenant_id uuid NOT NULL,batch_id uuid NOT NULL,definition text NOT NULL,
 params_hash text NOT NULL,params jsonb NOT NULL,status text NOT NULL DEFAULT 'READY',version int NOT NULL DEFAULT 1,
 rows_committed bigint NOT NULL DEFAULT 0,created_by text NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,batch_id,params_hash),
 FOREIGN KEY(tenant_id,batch_id) REFERENCES import_batch(tenant_id,id));
CREATE TABLE processing_record (
 id uuid PRIMARY KEY,tenant_id uuid NOT NULL,run_id uuid NOT NULL,artifact_id uuid NOT NULL,
 kind text NOT NULL,position bigint NOT NULL,locator jsonb NOT NULL,natural_key text NOT NULL,
 raw jsonb NOT NULL,data jsonb NOT NULL,raw_hash text NOT NULL,is_valid bool NOT NULL,
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,run_id,artifact_id,kind,position),
 FOREIGN KEY(tenant_id,run_id) REFERENCES processing_run(tenant_id,id),
 FOREIGN KEY(tenant_id,artifact_id) REFERENCES artifact(tenant_id,id));
CREATE INDEX processing_natural ON processing_record(tenant_id,run_id,kind,natural_key);
CREATE TABLE parse_checkpoint (
 id uuid PRIMARY KEY,tenant_id uuid NOT NULL,run_id uuid NOT NULL,artifact_id uuid NOT NULL,kind text NOT NULL,
 position bigint NOT NULL,UNIQUE(tenant_id,id),UNIQUE(tenant_id,run_id,artifact_id,kind),
 FOREIGN KEY(tenant_id,run_id) REFERENCES processing_run(tenant_id,id),
 FOREIGN KEY(tenant_id,artifact_id) REFERENCES artifact(tenant_id,id));
CREATE TABLE dq_issue (
 id uuid PRIMARY KEY,tenant_id uuid NOT NULL,run_id uuid NOT NULL,record_id uuid,code text NOT NULL,
 partition_key text NOT NULL,severity text NOT NULL,location jsonb NOT NULL,detail text NOT NULL,
 UNIQUE(tenant_id,id),FOREIGN KEY(tenant_id,run_id) REFERENCES processing_run(tenant_id,id),
 FOREIGN KEY(tenant_id,record_id) REFERENCES processing_record(tenant_id,id));
CREATE INDEX dq_lookup ON dq_issue(tenant_id,run_id,partition_key);
CREATE TABLE source_generation (
 id uuid PRIMARY KEY,tenant_id uuid NOT NULL,supplier_id uuid NOT NULL,property_code text NOT NULL,
 parent_id uuid,run_id uuid,activation_id uuid,scope jsonb NOT NULL,view_hash text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),UNIQUE(tenant_id,id),
 FOREIGN KEY(tenant_id,supplier_id) REFERENCES supplier(tenant_id,id),
 FOREIGN KEY(tenant_id,parent_id) REFERENCES source_generation(tenant_id,id),
 FOREIGN KEY(tenant_id,run_id) REFERENCES processing_run(tenant_id,id),
 FOREIGN KEY(tenant_id,activation_id) REFERENCES source_activation(tenant_id,id));
CREATE TABLE generation_fact (
 id uuid PRIMARY KEY,tenant_id uuid NOT NULL,generation_id uuid NOT NULL,kind text NOT NULL,
 natural_key text NOT NULL,data jsonb NOT NULL,origin_record_id uuid,legacy_staging_id uuid,tombstone bool NOT NULL,
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,generation_id,kind,natural_key),
 CHECK(num_nonnulls(origin_record_id,legacy_staging_id)=1),
 FOREIGN KEY(tenant_id,generation_id) REFERENCES source_generation(tenant_id,id),
 FOREIGN KEY(tenant_id,origin_record_id) REFERENCES processing_record(tenant_id,id),
 FOREIGN KEY(tenant_id,legacy_staging_id) REFERENCES staging_record(tenant_id,id));
CREATE TABLE source_head (
 id uuid PRIMARY KEY,tenant_id uuid NOT NULL,supplier_id uuid NOT NULL,property_code text NOT NULL,
 generation_id uuid NOT NULL,version bigint NOT NULL DEFAULT 1,UNIQUE(tenant_id,id),
 UNIQUE(tenant_id,supplier_id,property_code),
 FOREIGN KEY(tenant_id,supplier_id) REFERENCES supplier(tenant_id,id),
 FOREIGN KEY(tenant_id,generation_id) REFERENCES source_generation(tenant_id,id));
CREATE TABLE stable_partition (
 id uuid PRIMARY KEY,tenant_id uuid NOT NULL,supplier_id uuid NOT NULL,property_code text NOT NULL,
 stream text NOT NULL,partition_key text NOT NULL,version bigint NOT NULL,source_sequence bigint NOT NULL,
 generation_id uuid NOT NULL,UNIQUE(tenant_id,id),UNIQUE(tenant_id,supplier_id,property_code,stream,partition_key),
 FOREIGN KEY(tenant_id,supplier_id) REFERENCES supplier(tenant_id,id),
 FOREIGN KEY(tenant_id,generation_id) REFERENCES source_generation(tenant_id,id));
CREATE TABLE activation_command (
 id uuid PRIMARY KEY,tenant_id uuid NOT NULL,run_id uuid NOT NULL,key text NOT NULL,request_hash text NOT NULL,
 generation_id uuid NOT NULL,UNIQUE(tenant_id,id),UNIQUE(tenant_id,run_id,key),
 FOREIGN KEY(tenant_id,run_id) REFERENCES processing_run(tenant_id,id),
 FOREIGN KEY(tenant_id,generation_id) REFERENCES source_generation(tenant_id,id));
CREATE TABLE outbox_event (
 id uuid PRIMARY KEY,tenant_id uuid NOT NULL,event_type text NOT NULL,aggregate_id uuid NOT NULL,
 aggregate_version bigint NOT NULL,payload jsonb NOT NULL,trace_id uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),delivered_at timestamptz,attempt int NOT NULL DEFAULT 0,
 next_run_at timestamptz NOT NULL DEFAULT now(),error text,UNIQUE(tenant_id,id));
CREATE TABLE consumer_inbox (
 id uuid PRIMARY KEY,tenant_id uuid NOT NULL,event_id uuid NOT NULL,consumer text NOT NULL,result text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),UNIQUE(tenant_id,id),UNIQUE(tenant_id,event_id,consumer),
 FOREIGN KEY(tenant_id,event_id) REFERENCES outbox_event(tenant_id,id));
CREATE TABLE workflow_run (
 id uuid PRIMARY KEY,tenant_id uuid NOT NULL,processing_id uuid NOT NULL,definition text NOT NULL,
 state text NOT NULL,version bigint NOT NULL DEFAULT 1,activation_id uuid,snapshot_id uuid,task_id uuid,
 paused bool NOT NULL DEFAULT false,created_by text NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,processing_id,definition),
 FOREIGN KEY(tenant_id,processing_id) REFERENCES processing_run(tenant_id,id),
 FOREIGN KEY(tenant_id,activation_id) REFERENCES source_activation(tenant_id,id),
 FOREIGN KEY(tenant_id,snapshot_id) REFERENCES offer_snapshot(tenant_id,id),
 FOREIGN KEY(tenant_id,task_id) REFERENCES publish_task(tenant_id,id));
CREATE TABLE workflow_step (
 id uuid PRIMARY KEY,tenant_id uuid NOT NULL,run_id uuid NOT NULL,step_key text NOT NULL,input_hash text NOT NULL,
 state text NOT NULL,output_ref uuid,attempt int NOT NULL DEFAULT 0,UNIQUE(tenant_id,id),
 UNIQUE(tenant_id,run_id,step_key,input_hash),FOREIGN KEY(tenant_id,run_id) REFERENCES workflow_run(tenant_id,id));
CREATE TABLE workflow_wait (
 id uuid PRIMARY KEY,tenant_id uuid NOT NULL,run_id uuid NOT NULL,gate text NOT NULL,resource_id uuid NOT NULL,
 expected_version bigint NOT NULL,state text NOT NULL DEFAULT 'WAITING',resolved_event_id uuid,
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,run_id,gate,resource_id,expected_version),
 FOREIGN KEY(tenant_id,run_id) REFERENCES workflow_run(tenant_id,id),
 FOREIGN KEY(tenant_id,resolved_event_id) REFERENCES outbox_event(tenant_id,id));
CREATE TABLE execution_job (
 id uuid PRIMARY KEY,tenant_id uuid NOT NULL,kind text NOT NULL,resource_id uuid NOT NULL,
 logical_key text NOT NULL,state text NOT NULL DEFAULT 'READY',owner text,lease_until timestamptz,
 fencing_token bigint NOT NULL DEFAULT 0,attempt int NOT NULL DEFAULT 0,max_attempts int NOT NULL DEFAULT 3,
 next_run_at timestamptz NOT NULL DEFAULT now(),error text,created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,kind,logical_key));
CREATE INDEX job_due ON execution_job(tenant_id,state,next_run_at);
CREATE TABLE job_attempt (
 id uuid PRIMARY KEY,tenant_id uuid NOT NULL,job_id uuid NOT NULL,token bigint NOT NULL,owner text NOT NULL,
 outcome text NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),UNIQUE(tenant_id,id),
 FOREIGN KEY(tenant_id,job_id) REFERENCES execution_job(tenant_id,id));
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['processing_run','processing_record','parse_checkpoint','dq_issue','source_generation',
 'generation_fact','source_head','stable_partition','activation_command','outbox_event','consumer_inbox',
 'workflow_run','workflow_step','workflow_wait','execution_job','job_attempt'] LOOP
 EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',t);
 EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',t);
 EXECUTE format('CREATE POLICY tenant_boundary ON %I TO hotel_app USING(tenant_id=nullif(current_setting(''app.tenant_id'',true),'''')::uuid AND EXISTS(SELECT 1 FROM tenant WHERE id=tenant_id)) WITH CHECK(tenant_id=nullif(current_setting(''app.tenant_id'',true),'''')::uuid AND EXISTS(SELECT 1 FROM tenant WHERE id=tenant_id))',t);
 EXECUTE format('GRANT SELECT,INSERT ON %I TO hotel_app',t);
 END LOOP; END $$;
GRANT UPDATE ON processing_run,processing_record,parse_checkpoint,source_head,stable_partition,outbox_event,workflow_run,workflow_step,workflow_wait,execution_job TO hotel_app;
UPDATE schema_revision SET version='005';
