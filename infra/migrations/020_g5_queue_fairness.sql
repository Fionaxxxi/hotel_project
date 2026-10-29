CREATE INDEX g5_pending_recon ON execution_job(tenant_id,resource_id,created_at)
 WHERE kind='G3_RECON' AND state IN ('READY','RETRY_WAIT','RUNNING');
CREATE INDEX g5_deliver_jobs ON execution_job(tenant_id,state,next_run_at,resource_id)
 WHERE kind='G3_DELIVER';
UPDATE schema_revision SET version='020';
