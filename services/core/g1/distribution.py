import os
from uuid import uuid4

import httpx

from infra.manage import uid
from services.core.g1.common import audit, authorize, digest, fail, get, j
from services.core.g1.offers import valid_snapshot

SIM_URL = (
    "http://127.0.0.1:8006"
    if os.environ.get("G5_ISOLATED_SIMULATORS") == "true"
    else "http://127.0.0.1:8001"
)


def create_task(conn, actor, sid, command_key):
    authorize(actor)
    snapshot = get(conn, "offer_snapshot", sid, True)
    activation = get(conn, "source_activation", snapshot["activation_id"])
    if conn.execute(
        "SELECT 1 FROM g3_source_control WHERE source_key=%s",
        (str(activation["supplier_id"]) + "/" + activation["property_code"],),
    ).fetchone():
        fail("SOURCE_MANAGED_BY_G3")
    request_hash = digest({"snapshot_id": str(sid)})
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        (str(actor["tenant_id"]) + command_key,),
    )
    prior = conn.execute(
        "SELECT * FROM command_idempotency WHERE route='publish' AND key=%s", (command_key,)
    ).fetchone()
    if prior:
        if prior["request_hash"] != request_hash:
            fail("IDEMPOTENCY_CONFLICT")
        return get(conn, "publish_task", prior["resource_id"])
    valid_snapshot(conn, snapshot, approved=True)
    task = conn.execute("SELECT * FROM publish_task WHERE snapshot_id=%s", (sid,)).fetchone()
    if task is None:
        task_id = uuid4()
        key = digest(
            {
                "tenant": str(actor["tenant_id"]),
                "channel": str(snapshot["channel_id"]),
                "listing": snapshot["listing_key"],
                "revision": snapshot["revision"],
                "chunk": 0,
            }
        )
        conn.execute(
            "INSERT INTO publish_task(id,tenant_id,snapshot_id,idempotency_key,created_by) VALUES(%s,%s,%s,%s,%s)",
            (task_id, actor["tenant_id"], sid, key, actor["subject"]),
        )
        task = get(conn, "publish_task", task_id)
        audit(conn, actor, "publish.requested", task_id, "Persisted publish intent")
        from services.core.g2.jobs import enqueue

        enqueue(conn, actor["tenant_id"], "PUBLISH", task_id, str(task_id))
    conn.execute(
        "INSERT INTO command_idempotency VALUES(%s,%s,'publish',%s,%s,%s)",
        (uuid4(), actor["tenant_id"], command_key, request_hash, task["id"]),
    )
    return task


def credentials(tenant):
    label = next(
        (label for label in ["A", "B"] if str(uid(f"tenant-{label}")) == str(tenant)), None
    )
    if label is None:
        fail("CHANNEL_ACCOUNT_NOT_CONFIGURED")
    return {"Authorization": "Bearer " + os.environ[f"SIM_{label}_TOKEN"]}


def task_detail(conn, tid):
    task = get(conn, "publish_task", tid)
    task["receipts"] = conn.execute(
        "SELECT data,created_at FROM channel_receipt WHERE task_id=%s ORDER BY created_at", (tid,)
    ).fetchall()
    return task


def query_remote(db, tenant, tid, actor_subject="system.dispatcher", lease=None):
    with db.transaction(tenant, actor_subject) as conn:
        task = get(conn, "publish_task", tid)
        snapshot = get(conn, "offer_snapshot", task["snapshot_id"])
    verified = False
    try:
        with httpx.Client(
            base_url=SIM_URL, trust_env=False, follow_redirects=False, timeout=3
        ) as client:
            op = client.get(
                "/v1/operations/" + task["idempotency_key"], headers=credentials(tenant)
            )
            listing = client.get(
                "/v1/listings/" + snapshot["listing_key"], headers=credentials(tenant)
            )
        verified = False
        result = {"operation_status": op.status_code, "listing_status": listing.status_code}
        if op.status_code == 200 and listing.status_code == 200:
            operation, remote = op.json(), listing.json()
            verified = (
                operation["revision"] == snapshot["revision"]
                and operation["payload_hash"] == snapshot["payload_hash"]
                and remote["revision"] == snapshot["revision"]
                and remote["payload_hash"] == snapshot["payload_hash"]
                and digest(remote["payload"]) == snapshot["payload_hash"]
            )
            result.update(
                {
                    "operation": operation,
                    "listing_revision": remote["revision"],
                    "listing_hash": remote["payload_hash"],
                }
            )
        status = "VERIFIED" if verified else "UNKNOWN"
    except (httpx.HTTPError, ValueError, KeyError):
        result = {"error": "REMOTE_QUERY_UNAVAILABLE"}
        status = "UNKNOWN"
    with db.transaction(tenant, actor_subject) as conn:
        current = get(conn, "publish_task", tid, True)
        if lease:
            from services.core.g2.jobs import guard

            guard(conn, lease)
        # Querying a never-sent or blocked task must not manufacture a publish side effect.
        if current["status"] in {"SENDING", "ACKNOWLEDGED", "UNKNOWN", "VERIFIED"}:
            conn.execute(
                "UPDATE publish_task SET status=%s,error_code=%s WHERE id=%s",
                (status, None if verified else "REMOTE_NOT_VERIFIED", tid),
            )
            conn.execute(
                "INSERT INTO channel_receipt(id,tenant_id,task_id,data) VALUES(%s,%s,%s,%s)",
                (uuid4(), tenant, tid, j(result)),
            )
            audit(
                conn,
                {"tenant_id": tenant, "subject": actor_subject},
                "publish.queried",
                tid,
                status,
            )
            if current["status"] != status:
                from services.core.g2.jobs import emit

                emit(conn, tenant, "task.updated", tid, current["attempt"], {"status": status})
        return task_detail(conn, tid)


def dispatch_one(db, tenant, tid, delay=False, lease=None, after_remote=None):
    with db.transaction(tenant) as conn:
        if lease:
            from services.core.g2.jobs import guard

            guard(conn, lease)
        task = get(conn, "publish_task", tid, True)
        if task["status"] != "READY":
            return task
        snapshot = get(conn, "offer_snapshot", task["snapshot_id"])
        try:
            activation = get(conn, "source_activation", snapshot["activation_id"])
            from services.core.g5.recovery import guard as recovery_guard

            recovery_guard(conn, str(activation["supplier_id"]) + "/" + activation["property_code"])
            if conn.execute(
                "SELECT 1 FROM g3_source_control WHERE source_key=%s",
                (str(activation["supplier_id"]) + "/" + activation["property_code"],),
            ).fetchone():
                fail("SOURCE_MANAGED_BY_G3")
            # A disabled original requester no longer authorizes new dispatch.
            conn.execute("SELECT set_config('app.subject',%s,true)", (task["created_by"],))
            if not conn.execute(
                "SELECT 1 FROM membership WHERE tenant_id=%s AND status='active' AND role IN ('operator','tenant_admin')",
                (tenant,),
            ).fetchone():
                fail("REQUESTER_NOT_AUTHORIZED")
            valid_snapshot(conn, snapshot, True)
        except Exception as exc:
            from fastapi import HTTPException

            if not isinstance(exc, HTTPException):
                raise
            conn.execute(
                "UPDATE publish_task SET status='BLOCKED',error_code=%s WHERE id=%s",
                (str(exc.detail), tid),
            )
            return get(conn, "publish_task", tid)
        conn.execute(
            "UPDATE publish_task SET status='SENDING',attempt=attempt+1 WHERE id=%s", (tid,)
        )
        attempt = task["attempt"] + 1
    status = "UNKNOWN"
    error = None
    try:
        headers = {**credentials(tenant), "Idempotency-Key": task["idempotency_key"]}
        if delay:
            headers["X-Sim-Delay"] = "true"
        with httpx.Client(
            base_url=SIM_URL, trust_env=False, follow_redirects=False, timeout=0.3 if delay else 3
        ) as client:
            response = client.put(
                "/v1/listings/" + snapshot["listing_key"],
                headers=headers,
                json={
                    "revision": snapshot["revision"],
                    "payload_hash": snapshot["payload_hash"],
                    "payload": snapshot["payload"],
                },
            )
        if response.status_code == 200:
            status = "ACKNOWLEDGED"
        elif response.status_code in {400, 401, 403, 409, 422}:
            status = "FAILED"
            error = "CHANNEL_REJECTED_" + str(response.status_code)
    except httpx.HTTPError:
        error = "REMOTE_RESULT_UNKNOWN"
    if after_remote:
        after_remote()
    with db.transaction(tenant) as conn:
        if lease:
            from services.core.g2.jobs import guard

            guard(conn, lease)
        conn.execute(
            "UPDATE publish_task SET status=%s,error_code=%s WHERE id=%s", (status, error, tid)
        )
        conn.execute(
            "INSERT INTO task_attempt(id,tenant_id,task_id,attempt,request_hash,outcome) VALUES(%s,%s,%s,%s,%s,%s)",
            (uuid4(), tenant, tid, attempt, snapshot["payload_hash"], status),
        )
    if status == "ACKNOWLEDGED":
        return query_remote(db, tenant, tid, lease=lease)
    with db.transaction(tenant) as conn:
        return task_detail(conn, tid)
