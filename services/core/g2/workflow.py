"""Versioned, deterministic orchestration. Decisions are facts, not 'skip' commands."""

from uuid import uuid4

from fastapi import HTTPException

from services.core.g1 import distribution, offers
from services.core.g1.common import audit, digest, get
from services.core.g2 import jobs


def ensure(c, tenant, processing):
    run = c.execute(
        "SELECT * FROM workflow_run WHERE processing_id=%s AND definition=%s",
        (processing["id"], "g2-flow-v1"),
    ).fetchone()
    if run:
        return run
    wid = uuid4()
    c.execute(
        "INSERT INTO workflow_run(id,tenant_id,processing_id,definition,state,created_by) VALUES(%s,%s,%s,'g2-flow-v1','PROCESSING',%s)",
        (wid, tenant, processing["id"], processing["created_by"]),
    )
    return get(c, "workflow_run", wid)


def step(c, run, key, input_hash, output):
    c.execute(
        "INSERT INTO workflow_step(id,tenant_id,run_id,step_key,input_hash,state,output_ref,attempt) VALUES(%s,%s,%s,%s,%s,'SUCCEEDED',%s,1) ON CONFLICT(tenant_id,run_id,step_key,input_hash) DO NOTHING",
        (uuid4(), run["tenant_id"], run["id"], key, input_hash, output),
    )


def wait(c, run, gate, resource, expected):
    c.execute(
        "INSERT INTO workflow_wait(id,tenant_id,run_id,gate,resource_id,expected_version) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
        (uuid4(), run["tenant_id"], run["id"], gate, resource, expected),
    )
    c.execute("UPDATE workflow_run SET state=%s,version=version+1 WHERE id=%s", (gate, run["id"]))


def resolve(c, run, gate, event_id=None):
    c.execute(
        "UPDATE workflow_wait SET state='RESOLVED',resolved_event_id=%s WHERE run_id=%s AND gate=%s AND state='WAITING'",
        (event_id, run["id"], gate),
    )


def advance(c, wid, event_id=None):
    run = get(c, "workflow_run", wid, True)
    if run["paused"] or run["state"] == "CANCELLED":
        return run
    processing = get(c, "processing_run", run["processing_id"])
    batch = get(c, "import_batch", processing["batch_id"])
    source_key = str(batch["supplier_id"]) + "/" + batch["manifest"]["scope"]["property_codes"][0]
    if c.execute("SELECT 1 FROM g4_subscription WHERE source_key=%s", (source_key,)).fetchone():
        c.execute(
            "UPDATE workflow_run SET state='G4_MANAGED',paused=true,version=version+1 WHERE id=%s",
            (wid,),
        )
        return get(c, "workflow_run", wid)
    if processing["status"] == "MANUAL":
        wait(c, run, "BLOCKED", processing["id"], processing["version"])
        return get(c, "workflow_run", wid)
    if processing["status"] in {"READY_TO_ACTIVATE", "QUALITY_REVIEW", "ACTIVE"}:
        step(c, run, "PARSE", processing["params_hash"], processing["id"])
    actor = {"tenant_id": run["tenant_id"], "subject": run["created_by"], "role": "operator"}
    if processing["status"] != "ACTIVE":
        gate = (
            "WAIT_QUALITY"
            if processing["status"] == "QUALITY_REVIEW"
            else "WAIT_SCOPE"
            if processing["status"] == "READY_TO_ACTIVATE"
            else "PROCESSING"
        )
        wait(c, run, gate, processing["id"], processing["version"])
        return get(c, "workflow_run", wid)
    generation = c.execute(
        "SELECT g.id,l.activation_id FROM source_head h JOIN source_generation g ON(h.tenant_id,h.generation_id)=(g.tenant_id,g.id) JOIN generation_activation l ON(g.tenant_id,g.id)=(l.tenant_id,l.generation_id) WHERE g.run_id=%s",
        (processing["id"],),
    ).fetchone()
    if not generation:
        c.execute("UPDATE workflow_run SET state='STALE',version=version+1 WHERE id=%s", (wid,))
        return get(c, "workflow_run", wid)
    aid = generation["activation_id"]
    activation = get(c, "source_activation", aid)
    if c.execute(
        "SELECT 1 FROM g3_source_control WHERE source_key=%s",
        (str(activation["supplier_id"]) + "/" + activation["property_code"],),
    ).fetchone():
        c.execute(
            "UPDATE workflow_run SET state='G3_MANAGED',paused=true,version=version+1 WHERE id=%s",
            (wid,),
        )
        return get(c, "workflow_run", wid)
    resolve(c, run, "PROCESSING", event_id)
    resolve(c, run, "WAIT_SCOPE", event_id)
    resolve(c, run, "WAIT_QUALITY", event_id)
    step(c, run, "ACTIVATE", str(generation["id"]), aid)
    if run["activation_id"] != aid:
        c.execute(
            "UPDATE workflow_run SET activation_id=%s,snapshot_id=NULL,task_id=NULL,state='RESOLVE_MAPPINGS',version=version+1 WHERE id=%s",
            (aid, wid),
        )
        run = get(c, "workflow_run", wid)
    try:
        offers.dependencies(c, aid)
    except HTTPException as exc:
        gate = "WAIT_MAPPING" if exc.detail in {"MAPPING_REQUIRED", "MAPPING_STALE"} else "BLOCKED"
        wait(c, run, gate, aid, 1)
        return get(c, "workflow_run", wid)
    resolve(c, run, "WAIT_MAPPING", event_id)
    if not run["snapshot_id"]:
        try:
            with c.transaction():
                snapshot = offers.compile_offer(c, actor, aid)
                if snapshot["status"] == "COMPILED":
                    snapshot = offers.submit(c, actor, snapshot["id"], snapshot["version"])
        except HTTPException:
            wait(c, run, "BLOCKED", aid, 1)
            return get(c, "workflow_run", wid)
        c.execute(
            "UPDATE workflow_run SET snapshot_id=%s,version=version+1 WHERE id=%s",
            (snapshot["id"], wid),
        )
        step(c, run, "COMPILE", digest(snapshot["input_versions"]), snapshot["id"])
        run = get(c, "workflow_run", wid)
    snapshot = get(c, "offer_snapshot", run["snapshot_id"])
    if snapshot["status"] == "REJECTED":
        c.execute("UPDATE workflow_run SET state='REJECTED',version=version+1 WHERE id=%s", (wid,))
        return get(c, "workflow_run", wid)
    if snapshot["status"] != "APPROVED":
        wait(c, run, "WAIT_APPROVAL", snapshot["id"], snapshot["version"])
        return get(c, "workflow_run", wid)
    resolve(c, run, "WAIT_APPROVAL", event_id)
    if not run["task_id"]:
        task = distribution.create_task(
            c, actor, snapshot["id"], f"workflow:{wid}:{snapshot['id']}"
        )
        jobs.enqueue(c, run["tenant_id"], "PUBLISH", task["id"], str(task["id"]))
        c.execute(
            "UPDATE workflow_run SET task_id=%s,version=version+1 WHERE id=%s", (task["id"], wid)
        )
        run = get(c, "workflow_run", wid)
    task = get(c, "publish_task", run["task_id"])
    state = (
        "COMPLETED"
        if task["status"] == "VERIFIED"
        else "WAIT_EXTERNAL"
        if task["status"] == "UNKNOWN"
        else "BLOCKED"
        if task["status"] in {"BLOCKED", "FAILED"}
        else "RUNNING"
    )
    c.execute("UPDATE workflow_run SET state=%s,version=version+1 WHERE id=%s", (state, wid))
    if state == "COMPLETED":
        step(c, run, "PUBLISH", str(snapshot["id"]), task["id"])
        resolve(c, run, "WAIT_EXTERNAL", event_id)
    audit(c, {**actor, "subject": "system.orchestrator"}, "workflow.advanced", wid, state)
    return get(c, "workflow_run", wid)


def consume(c, event):
    inserted = c.execute(
        "INSERT INTO consumer_inbox(id,tenant_id,event_id,consumer,result) VALUES(%s,%s,%s,'workflow-v1','APPLIED') ON CONFLICT(tenant_id,event_id,consumer) DO NOTHING RETURNING id",
        (uuid4(), event["tenant_id"], event["id"]),
    ).fetchone()
    if not inserted:
        return "DUPLICATE"
    workflows = []
    kind = event["event_type"]
    if kind in {"processing.created", "processing.completed", "source.activated"}:
        run = get(c, "processing_run", event["aggregate_id"])
        # Inspect durable current facts. A late 'processing.completed' cannot regress ACTIVE.
        workflows = [ensure(c, event["tenant_id"], run)]
        if kind == "source.activated":
            batch = get(c, "import_batch", run["batch_id"])
            c.execute(
                "UPDATE workflow_run w SET state='STALE',version=w.version+1 FROM processing_run p,import_batch b WHERE w.processing_id=p.id AND p.batch_id=b.id AND b.supplier_id=%s AND b.manifest->'scope'->'property_codes'->>0=%s AND w.processing_id!=%s AND w.state NOT IN ('COMPLETED','CANCELLED')",
                (batch["supplier_id"], batch["manifest"]["scope"]["property_codes"][0], run["id"]),
            )
    elif kind == "mapping.decided":
        source = get(c, "supplier_entity_version", event["aggregate_id"])
        from services.core.g1.matching import mapping

        current = mapping(c, source)
        if not current or current["version"] != event["aggregate_version"]:
            c.execute(
                "UPDATE consumer_inbox SET result='STALE' WHERE event_id=%s AND consumer='workflow-v1'",
                (event["id"],),
            )
            return "STALE"
        workflows = c.execute(
            "SELECT * FROM workflow_run WHERE activation_id=%s", (source["activation_id"],)
        ).fetchall()
    elif kind == "approval.decided":
        workflows = c.execute(
            "SELECT * FROM workflow_run WHERE snapshot_id=%s", (event["aggregate_id"],)
        ).fetchall()
    elif kind == "task.updated":
        workflows = c.execute(
            "SELECT * FROM workflow_run WHERE task_id=%s", (event["aggregate_id"],)
        ).fetchall()
    elif kind == "workflow.resume":
        workflows = [get(c, "workflow_run", event["aggregate_id"])]
    for run in workflows:
        jobs.enqueue(c, event["tenant_id"], "WORKFLOW", run["id"], f"{run['id']}:{event['id']}")
    return "APPLIED"


def relay_one(db, tenant, after_consume=None, event_id=None):
    with db.transaction(tenant) as c:
        event = c.execute(
            "SELECT * FROM outbox_event WHERE delivered_at IS NULL AND next_run_at<=clock_timestamp() AND attempt<5 AND (%s::uuid IS NULL OR id=%s) ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1",
            (event_id, event_id),
        ).fetchone()
        if not event:
            return False
        c.execute(
            "UPDATE outbox_event SET attempt=attempt+1,next_run_at=clock_timestamp()+interval '1 second' WHERE id=%s",
            (event["id"],),
        )
    try:
        with db.transaction(tenant) as c:
            from services.core.g1.common import REQUEST_TRACE
            from services.core.g5.tracing import CAUSE_EVENT

            trace = REQUEST_TRACE.set(event["trace_id"])
            cause = CAUSE_EVENT.set(event["id"])
            try:
                consume(c, event)
            finally:
                REQUEST_TRACE.reset(trace)
                CAUSE_EVENT.reset(cause)
        if after_consume:
            after_consume()
        with db.transaction(tenant) as c:
            c.execute(
                "UPDATE outbox_event SET delivered_at=clock_timestamp(),error=NULL WHERE id=%s",
                (event["id"],),
            )
    except Exception as exc:
        with db.transaction(tenant) as c:
            c.execute(
                "UPDATE outbox_event SET error=%s WHERE id=%s", (type(exc).__name__, event["id"])
            )
        raise
    return True
