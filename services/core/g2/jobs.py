"""Database time leases; every state-changing commit is fenced."""

from uuid import uuid4

from services.core.g1.common import REQUEST_TRACE, fail, j
from services.core.g5.tracing import CAUSE_EVENT


def enqueue(c, tenant, kind, resource, logical_key):
    row = c.execute(
        "INSERT INTO execution_job(id,tenant_id,kind,resource_id,logical_key,trace_id,cause_event_id) VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(tenant_id,kind,logical_key) DO NOTHING RETURNING *",
        (uuid4(), tenant, kind, resource, logical_key, REQUEST_TRACE.get(), CAUSE_EVENT.get()),
    ).fetchone()
    return (
        row
        or c.execute(
            "SELECT * FROM execution_job WHERE kind=%s AND logical_key=%s", (kind, logical_key)
        ).fetchone()
    )


def claim(c, owner, lease_seconds=30, kinds=None, resource=None, resource_ids=None):
    clauses = [
        "tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid",
        "((state IN ('READY','RETRY_WAIT') AND next_run_at<=clock_timestamp() AND attempt<max_attempts) OR (state='RUNNING' AND lease_until<clock_timestamp()))",
    ]
    params = []
    if kinds is not None:
        clauses.append("kind=ANY(%s::text[])")
        params.append(kinds)
    if resource is not None:
        clauses.append("resource_id=%s::uuid")
        params.append(str(resource))
    if resource_ids is not None:
        clauses.append("resource_id=ANY(%s::uuid[])")
        params.append([str(x) for x in resource_ids])
    job = c.execute(
        "SELECT * FROM execution_job WHERE "
        + " AND ".join(clauses)
        + " ORDER BY next_run_at,created_at FOR UPDATE SKIP LOCKED LIMIT 1",
        params,
    ).fetchone()
    if not job:
        return None
    previous = job["state"]
    row = c.execute(
        "UPDATE execution_job SET state='RUNNING',owner=%s,lease_until=clock_timestamp()+(%s*interval '1 second'),fencing_token=fencing_token+1,attempt=attempt+1 WHERE id=%s RETURNING *",
        (owner, lease_seconds, job["id"]),
    ).fetchone()
    row["recovering"] = previous == "RUNNING"
    return row


def guard(c, job):
    valid = c.execute(
        "SELECT id FROM execution_job WHERE id=%s AND owner=%s AND fencing_token=%s AND state='RUNNING' AND lease_until>clock_timestamp() FOR UPDATE",
        (job["id"], job["owner"], job["fencing_token"]),
    ).fetchone()
    if not valid:
        fail("LEASE_LOST")


def heartbeat(c, job, seconds=30):
    row = c.execute(
        "UPDATE execution_job SET lease_until=clock_timestamp()+(%s*interval '1 second') WHERE id=%s AND owner=%s AND fencing_token=%s AND state='RUNNING' AND lease_until>clock_timestamp() RETURNING id",
        (seconds, job["id"], job["owner"], job["fencing_token"]),
    ).fetchone()
    if not row:
        fail("LEASE_LOST")


def defer(c, job, reason):
    guard(c, job)
    c.execute(
        "UPDATE execution_job SET state='RETRY_WAIT',attempt=greatest(0,attempt-1),lease_until=NULL,error=%s,next_run_at=clock_timestamp()+interval '1 second' WHERE id=%s",
        (reason, job["id"]),
    )


def finish(c, job, state="SUCCEEDED", error=None):
    guard(c, job)
    if state == "RETRY_WAIT" and job["attempt"] >= job["max_attempts"]:
        state = "MANUAL"
    c.execute(
        "UPDATE execution_job SET state=%s,error=%s,lease_until=NULL,next_run_at=clock_timestamp()+interval '1 second' WHERE id=%s",
        (state, error, job["id"]),
    )
    c.execute(
        "INSERT INTO job_attempt(id,tenant_id,job_id,token,owner,outcome) VALUES(%s,%s,%s,%s,%s,%s)",
        (uuid4(), job["tenant_id"], job["id"], job["fencing_token"], job["owner"], state),
    )


def emit(c, tenant, event_type, aggregate, version, payload):
    eid = uuid4()
    c.execute(
        "INSERT INTO outbox_event(id,tenant_id,event_type,aggregate_id,aggregate_version,payload,trace_id) VALUES(%s,%s,%s,%s,%s,%s,%s)",
        (eid, tenant, event_type, aggregate, version, j(payload), REQUEST_TRACE.get() or uuid4()),
    )
    return eid
