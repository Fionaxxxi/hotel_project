CREATE INDEX g5_job_kind_due ON execution_job(tenant_id,kind,state,next_run_at,created_at);
CREATE INDEX g5_job_resource_due ON execution_job(tenant_id,kind,resource_id,state,next_run_at);
UPDATE schema_revision SET version='021';
