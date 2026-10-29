import random
from datetime import timedelta
from uuid import uuid4

import httpx
from fastapi import HTTPException

from infra.manage import uid
from services.core.g1.common import REQUEST_TRACE, audit, fail, get, j, now
from services.core.g2 import jobs
from services.core.g3 import domain, transport
from services.core.g3.contract import PROFILES, cell_key, normalize_content, retry_after
from services.core.g5.tracing import CAUSE_EVENT, traced_job


def schedule(c, tenant, tid):
    job = jobs.enqueue(c, tenant, "G3_DELIVER", tid, str(tid))
    c.execute("UPDATE execution_job SET max_attempts=100000 WHERE id=%s", (job["id"],))


def relay(db, tenant, after_consume=None, event_id=None):
    with db.transaction(tenant) as c:
        c.execute(
            "UPDATE outbox_event e SET delivered_at=clock_timestamp() WHERE e.tenant_id=%s AND e.event_type='g3.intent.created' AND e.delivered_at IS NULL AND EXISTS(SELECT 1 FROM consumer_inbox i WHERE i.tenant_id=e.tenant_id AND i.event_id=e.id AND i.consumer='g3-delivery-v1')",
            (tenant,),
        )
        event = c.execute(
            "SELECT e.* FROM outbox_event e WHERE e.tenant_id=%s AND e.event_type='g3.intent.created'"
            + (" AND e.id=%s" if event_id is not None else "")
            + " AND NOT EXISTS(SELECT 1 FROM consumer_inbox i WHERE i.tenant_id=e.tenant_id AND i.event_id=e.id AND i.consumer='g3-delivery-v1') ORDER BY e.created_at LIMIT 1 FOR UPDATE OF e SKIP LOCKED",
            (tenant, event_id) if event_id is not None else (tenant,),
        ).fetchone()
        if not event:
            return False
        claimed = c.execute(
            "INSERT INTO consumer_inbox VALUES(%s,%s,%s,'g3-delivery-v1','APPLIED') ON CONFLICT(tenant_id,event_id,consumer) DO NOTHING RETURNING id",
            (uuid4(), tenant, event["id"]),
        ).fetchone()
        if not claimed:
            # LockRows may acquire an unchanged outbox row after another
            # consumer commits its inbox insertion. Its earlier MVCC read
            # cannot certify ownership; the unique insertion is authoritative.
            return True
        if event["event_type"] == "g3.intent.created":
            for t in c.execute(
                "SELECT id FROM g3_task WHERE intent_id=%s", (event["aggregate_id"],)
            ).fetchall():
                trace = REQUEST_TRACE.set(event["trace_id"])
                cause = CAUSE_EVENT.set(event["id"])
                try:
                    schedule(c, tenant, t["id"])
                finally:
                    REQUEST_TRACE.reset(trace)
                    CAUSE_EVENT.reset(cause)
    if after_consume:
        after_consume()
    with db.transaction(tenant) as c:
        c.execute(
            "UPDATE outbox_event SET delivered_at=clock_timestamp() WHERE id=%s", (event["id"],)
        )
    return True


def quota(c, account, read=False):
    a = get(c, "g3_account", account["id"], True)
    clock = c.execute("SELECT clock_timestamp() AS t").fetchone()["t"]
    field = "next_read_at" if read else "next_write_at"
    due = max(a[field], a["cooldown_until"] if not read else a[field])
    if due > clock:
        return (due - clock).total_seconds()
    rate = 3 if read else PROFILES[a["code"]]["rate"]
    c.execute(
        f"UPDATE g3_account SET {field}=clock_timestamp()+(%s*interval '1 second') WHERE id=%s",
        (1 / rate, a["id"]),
    )
    return 0


def later(c, job, seconds, reason):
    jobs.defer(c, job, reason)
    c.execute(
        "UPDATE execution_job SET next_run_at=clock_timestamp()+(%s*interval '1 second') WHERE id=%s",
        (max(0.05, seconds), job["id"]),
    )


def dead(c, task, job, code):
    c.execute(
        "UPDATE g3_task SET state='MANUAL',error=%s,version=version+1 WHERE id=%s",
        (code, task["id"]),
    )
    c.execute("UPDATE g3_intent SET state='MANUAL' WHERE id=%s", (task["intent_id"],))
    c.execute(
        "INSERT INTO g3_dead_letter(id,tenant_id,task_id,cycle,code) VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
        (uuid4(), task["tenant_id"], task["id"], task["budget_cycle"], code),
    )
    jobs.finish(c, job, "MANUAL", code)


@traced_job
def execute(db, job, after_remote=None):
    tenant = job["tenant_id"]
    if job["kind"] in {"G3_RECON", "G3_VERIFY"}:
        reconcile(db, tenant, job["resource_id"], job=job, automatic=True)
        return
    with db.pool.connection() as mutex:
        with db.transaction(tenant) as c:
            task = get(c, "g3_task", job["resource_id"])
            intent = get(c, "g3_intent", task["intent_id"])
            li = get(c, "g3_listing", intent["listing_id"])
        lock_key = str(tenant) + ":g3io:" + str(li["id"])
        ok = mutex.execute(
            "SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS ok", (lock_key,)
        ).fetchone()["ok"]
        mutex.commit()
        if not ok:
            with db.transaction(tenant) as c:
                later(c, job, 0.2, "LISTING_BUSY")
            return
        try:
            _execute(db, job, after_remote)
        finally:
            mutex.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", (lock_key,))
            mutex.commit()


def _execute(db, job, after_remote):
    tenant = job["tenant_id"]
    with db.transaction(tenant) as c:
        initial = get(c, "g3_task", job["resource_id"])
        initial_intent = get(c, "g3_intent", initial["intent_id"])
        initial_listing = get(c, "g3_listing", initial_intent["listing_id"])
        domain.lock_source(c, tenant, initial_listing["source_key"])
        jobs.guard(c, job)
        t = get(c, "g3_task", job["resource_id"], True)
        i = get(c, "g3_intent", t["intent_id"])
        li = get(c, "g3_listing", i["listing_id"], True)
        a = get(c, "g3_account", li["account_id"])
        if t["state"] in {"APPLIED", "SUPERSEDED", "REJECTED"}:
            jobs.finish(c, job)
            return
        querying = t["state"] in {"SENDING", "UNKNOWN", "PROCESSING"}
        if not querying and i["action"] != "STOP":
            from services.core.g5.recovery import guard

            guard(c, li["source_key"])
        if i["id"] != li["intent_id"] and not querying:
            c.execute("UPDATE g3_task SET state='SUPERSEDED' WHERE id=%s", (t["id"],))
            jobs.finish(c, job)
            return
        if t["deadline"] <= now() or (t["query_count"] >= 20 if querying else t["send_count"] >= 6):
            dead(c, t, job, "QUERY_BUDGET_EXHAUSTED" if querying else "SEND_BUDGET_EXHAUSTED")
            return
        if not querying:
            if li["paused"] and i["action"] != "STOP":
                later(c, job, 1, "PAUSED")
                return
            # Stop content and cells retain priority even when ordinary jobs are queued.
            if a["state"] != "ACTIVE":
                later(c, job, 5, "ACCOUNT_PAUSED")
                return
            if (
                t["chunk_index"]
                and not c.execute(
                    "SELECT 1 FROM g3_task WHERE intent_id=%s AND chunk_index=0 AND state='APPLIED'",
                    (i["id"],),
                ).fetchone()
            ):
                later(c, job, 0.2, "CONTENT_PENDING")
                return
            if i["action"] != "STOP":
                try:
                    domain.validate_snapshot(c, get(c, "g3_snapshot", i["snapshot_id"]))
                    c.execute("SELECT set_config('app.subject',%s,true)", (i["created_by"],))
                    member = c.execute(
                        "SELECT m.role FROM membership m JOIN app_user u ON u.id=m.user_id WHERE u.subject=%s AND m.status='active'",
                        (i["created_by"],),
                    ).fetchone()
                    if not member or member["role"] not in {"operator", "tenant_admin"}:
                        fail("INITIATOR_REVOKED")
                except HTTPException as exc:
                    dead(c, t, job, str(exc.detail))
                    return
        wait = quota(c, a, querying)
        if wait:
            later(c, job, wait, "ACCOUNT_QUOTA")
            return
        c.execute(
            "UPDATE g3_task SET state=%s,send_count=send_count+%s,query_count=query_count+%s,version=version+1 WHERE id=%s",
            (
                "UNKNOWN" if querying else "SENDING",
                0 if querying else 1,
                1 if querying else 0,
                t["id"],
            ),
        )
    response = None
    data = {}
    status = 0
    try:
        response = transport.request(
            tenant,
            a["code"],
            "GET" if querying else "PUT",
            "/g3/operations/" + t["request_key"]
            if querying
            else transport.listing_path(li["listing_key"]),
            None if querying else t["body"],
            None if querying else t["request_key"],
        )
        status = response.status_code
        data = response.json()
    except (httpx.HTTPError, ValueError):
        data = {"code": "TRANSPORT_UNKNOWN"}
    if after_remote:
        after_remote()
    with db.transaction(tenant) as c:
        jobs.guard(c, job)
        current = get(c, "g3_task", t["id"], True)
        c.execute(
            "INSERT INTO g3_receipt(id,tenant_id,task_id,phase,status,data) VALUES(%s,%s,%s,%s,%s,%s)",
            (uuid4(), tenant, t["id"], "QUERY" if querying else "SEND", status, j(data)),
        )
        if status == 429:
            delay = retry_after(response.headers.get("Retry-After"))
            delay = (
                delay
                if delay is not None
                else random.uniform(0, min(60, 2 * 2 ** (current["send_count"] - 1)))
            )
            c.execute(
                "UPDATE g3_account SET cooldown_until=greatest(cooldown_until,clock_timestamp()+(%s*interval '1 second')) WHERE id=%s",
                (delay, a["id"]),
            )
            c.execute(
                "UPDATE g3_task SET state=%s,error='RATE_LIMITED' WHERE id=%s",
                ("UNKNOWN" if querying else "RETRY_WAIT", t["id"]),
            )
            later(c, job, delay + 0.02, "RATE_LIMITED")
            return
        if status in {401, 403}:
            c.execute(
                "UPDATE g3_account SET state='PAUSED',version=version+1 WHERE id=%s", (a["id"],)
            )
            dead(c, current, job, "CHANNEL_AUTHENTICATION")
            return
        if status in {400, 422}:
            c.execute(
                "UPDATE g3_task SET state='REJECTED',error='CHANNEL_VALIDATION' WHERE id=%s",
                (t["id"],),
            )
            c.execute("UPDATE g3_intent SET state='BLOCKED' WHERE id=%s", (i["id"],))
            jobs.finish(c, job, "BLOCKED", "CHANNEL_VALIDATION")
            return
        if status in {200, 202} and data.get("state") == "DONE":
            result = data.get("result", {})
            outcome = result.get("status")
            for cell in result.get("cells", []):
                c.execute(
                    "INSERT INTO g3_cell_result VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    (uuid4(), tenant, t["id"], cell["key"], cell["state"], cell.get("code")),
                )
            state = (
                "APPLIED"
                if outcome == "APPLIED"
                else "PARTIAL"
                if outcome == "PARTIAL"
                else "REJECTED"
            )
            c.execute(
                "UPDATE g3_task SET state=%s,remote_operation=%s,error=%s,version=version+1 WHERE id=%s",
                (state, data.get("operation_id"), result.get("code"), t["id"]),
            )
            jobs.finish(
                c,
                job,
                "SUCCEEDED" if state in {"APPLIED", "PARTIAL"} else "BLOCKED",
                result.get("code"),
            )
            if state == "REJECTED":
                c.execute("UPDATE g3_intent SET state='BLOCKED' WHERE id=%s", (i["id"],))
        elif status >= 500 and data.get("accepted") is False:
            c.execute(
                "UPDATE g3_task SET state='RETRY_WAIT',error='REMOTE_UNAVAILABLE' WHERE id=%s",
                (t["id"],),
            )
            later(
                c,
                job,
                random.uniform(0, min(60, 2 * 2 ** (current["send_count"] - 1))),
                "REMOTE_UNAVAILABLE",
            )
            return
        elif status == 409:
            c.execute(
                "UPDATE g3_task SET state='SUPERSEDED',error='REMOTE_REVISION_CONFLICT' WHERE id=%s",
                (t["id"],),
            )
            jobs.finish(c, job, "BLOCKED", "REMOTE_REVISION_CONFLICT")
            c.execute("UPDATE g3_intent SET state='BLOCKED' WHERE id=%s", (i["id"],))
        else:
            c.execute(
                "UPDATE g3_task SET state=%s,remote_operation=%s,error=%s WHERE id=%s",
                (
                    "PROCESSING"
                    if status == 202 or data.get("state") == "PROCESSING"
                    else "UNKNOWN",
                    data.get("operation_id"),
                    "REMOTE_NOT_CONFIRMED",
                    t["id"],
                ),
            )
            later(c, job, 0.25 if a["code"] == "SIM-B" else 0.5, "QUERY_ORIGINAL_OPERATION")
            return
        c.execute("UPDATE g3_listing SET next_due_at=clock_timestamp() WHERE id=%s", (li["id"],))


def reconcile(db, tenant, lid, job=None, automatic=False):
    with db.transaction(tenant) as c:
        if job:
            jobs.guard(c, job)
        li = get(c, "g3_listing", lid)
        i = get(c, "g3_intent", li["intent_id"])
        a = get(c, "g3_account", li["account_id"])
        wait = quota(c, a, True)
        if wait and job:
            later(c, job, wait, "READ_QUOTA")
            return None
    status = 0
    data = {}
    try:
        response = transport.request(
            tenant, a["code"], "GET", transport.listing_path(li["listing_key"])
        )
        status = response.status_code
        data = response.json()
        if status == 200 and data.get("complete"):
            data["wire_content"] = data.get("content")
            data["content"] = normalize_content(data["content"], a["code"])
    except (httpx.HTTPError, ValueError, HTTPException, KeyError, TypeError):
        status = 0
        pass
    classification = "UNKNOWN"
    different = []
    complete = status == 404 or (status == 200 and data.get("complete") is True)
    expected = {cell_key(x): x for x in i["payload"]["cells"]}
    if complete:
        if status == 404:
            classification = "MISSING"
            different = list(expected)
        elif data.get("revision", 0) > i["delivery_revision"]:
            classification = "REMOTE_AHEAD"
        else:
            remote = {k: v["value"] for k, v in data.get("cells", {}).items()}
            different = [k for k in set(expected) | set(remote) if expected.get(k) != remote.get(k)]
            if data.get("content") != i["payload"]["content"]:
                different = list(expected)
            classification = (
                ("MISSING" if not any(k in remote for k in expected) else "DRIFT")
                if different
                else "CONSISTENT"
            )
    with db.transaction(tenant) as c:
        if job:
            jobs.guard(c, job)
        domain.lock_source(c, tenant, li["source_key"])
        current = get(c, "g3_listing", lid, True)
        # A read started on an old target never certifies the new target.
        if current["intent_id"] != i["id"]:
            classification = "STALE_OBSERVATION"
            complete = False
        if complete and status == 200:
            for ck, entry in data.get("cells", {}).items():
                value = entry.get("value", {})
                if (
                    ck in expected
                    and value.get("kind") == "pool"
                    and type(value.get("quantity")) is int
                    and value["quantity"] >= 0
                ):
                    c.execute(
                        "UPDATE g3_reservation SET exposure=greatest(exposure,%s) WHERE source_key=%s AND account_id=%s AND pool_day=%s",
                        (
                            value["quantity"],
                            li["source_key"],
                            li["account_id"],
                            value["key"] + "/" + value["date"],
                        ),
                    )
        pending = c.execute(
            "SELECT 1 FROM g3_task WHERE intent_id=%s AND state IN ('READY','RETRY_WAIT','SENDING','PROCESSING','UNKNOWN') LIMIT 1",
            (i["id"],),
        ).fetchone()
        if complete and pending:
            classification = "PENDING"
        if classification == "CONSISTENT" and not pending:
            for value in i["payload"]["cells"]:
                if value["kind"] == "pool":
                    c.execute(
                        "UPDATE g3_reservation SET exposure=%s WHERE source_key=%s AND account_id=%s AND pool_day=%s",
                        (
                            value["quantity"],
                            li["source_key"],
                            li["account_id"],
                            value["key"] + "/" + value["date"],
                        ),
                    )
        if (
            complete
            and not pending
            and classification not in {"STALE_OBSERVATION", "REMOTE_AHEAD"}
            and domain.capacity_deficits(c, li["source_key"])
        ):
            classification = "CAPACITY_DEFICIT"
        oid = uuid4()
        c.execute(
            "INSERT INTO g3_observation(id,tenant_id,listing_id,desired_revision,classification,complete,data) VALUES(%s,%s,%s,%s,%s,%s,%s)",
            (
                oid,
                tenant,
                lid,
                i["desired_revision"],
                classification,
                complete,
                j({"http_status": status, **data}),
            ),
        )
        did = None
        if classification in {"DRIFT", "MISSING", "REMOTE_AHEAD", "UNKNOWN", "CAPACITY_DEFICIT"}:
            did = uuid4()
            c.execute(
                "INSERT INTO g3_diff(id,tenant_id,listing_id,observation_id,desired_revision,classification,cells) VALUES(%s,%s,%s,%s,%s,%s,%s)",
                (did, tenant, lid, oid, i["desired_revision"], classification, j(different)),
            )
        if classification == "CONSISTENT":
            state = (
                "STOPPED" if all(not x["sellable"] for x in i["payload"]["cells"]) else "VERIFIED"
            )
            c.execute(
                "UPDATE g3_listing SET state=%s,applied_revision=%s,observed_revision=%s,version=version+1 WHERE id=%s",
                (state, i["desired_revision"], data.get("revision"), lid),
            )
            c.execute(
                "UPDATE g3_intent SET state='VERIFIED' WHERE listing_id=%s AND desired_revision=%s AND payload_hash=%s",
                (lid, i["desired_revision"], i["payload_hash"]),
            )
            c.execute(
                "UPDATE g3_plan p SET state=CASE WHEN NOT EXISTS(SELECT 1 FROM g3_intent z WHERE z.plan_id=p.id AND z.state!='VERIFIED') THEN 'VERIFIED' ELSE 'PARTIAL' END WHERE EXISTS(SELECT 1 FROM g3_intent z WHERE z.plan_id=p.id AND z.listing_id=%s)",
                (lid,),
            )
            c.execute(
                "UPDATE g3_diff SET state='RESOLVED' WHERE listing_id=%s AND desired_revision=%s AND state IN ('OPEN','REPAIRING')",
                (lid, i["desired_revision"]),
            )
            for x in i["payload"]["cells"]:
                if x["kind"] == "pool":
                    c.execute(
                        "UPDATE g3_reservation SET exposure=%s WHERE source_key=%s AND account_id=%s AND pool_day=%s",
                        (
                            x["quantity"],
                            li["source_key"],
                            li["account_id"],
                            x["key"] + "/" + x["date"],
                        ),
                    )
        elif classification not in {"PENDING", "STALE_OBSERVATION"}:
            c.execute(
                "UPDATE g3_listing SET state=%s,version=version+1 WHERE id=%s",
                ("STOP_SELL_REQUESTED" if i["action"] == "STOP" else classification, lid),
            )
        c.execute(
            "UPDATE g3_listing SET next_due_at=clock_timestamp()+CASE WHEN %s THEN interval '0 seconds' WHEN state='STOPPED' THEN interval '5 minutes' ELSE interval '60 seconds' END WHERE id=%s",
            (classification == "STALE_OBSERVATION", lid),
        )
        if job:
            jobs.finish(c, job)
        result = {
            "id": str(oid),
            "classification": classification,
            "diff_id": str(did) if did else None,
            "cells": different,
        }
        # Partial permanent rejects do not get silently reinterpreted as drift repairs.
        rejected = c.execute(
            "SELECT 1 FROM g3_cell_result r JOIN g3_task t ON t.id=r.task_id WHERE t.intent_id=%s AND r.code='CELL_POLICY_REJECTED'",
            (i["id"],),
        ).fetchone()
        temporary = c.execute(
            "SELECT cell_key FROM g3_cell_result r JOIN g3_task t ON t.id=r.task_id WHERE t.intent_id=%s AND r.code='TEMPORARY_CELL'",
            (i["id"],),
        ).fetchall()
        if automatic and not pending and not rejected and classification in {"DRIFT", "MISSING"}:
            s = get(c, "g3_snapshot", i["snapshot_id"])
            actor = {"tenant_id": tenant, "subject": s["created_by"], "role": "operator"}
            if i["action"] == "STOP":
                count = c.execute(
                    "SELECT count(*) AS n FROM g3_intent WHERE listing_id=%s AND action='STOP'",
                    (lid,),
                ).fetchone()["n"]
                if count < 4:
                    domain.make_intent(
                        c,
                        actor,
                        current,
                        s,
                        "STOP",
                        i["payload"],
                        reason="Bounded close confirmation replay",
                    )
            elif s["allow_repair"] or temporary:
                try:
                    with c.transaction():
                        domain.repair(
                            c,
                            actor,
                            did,
                            get(c, "g3_listing", lid)["version"],
                            transient=bool(temporary),
                        )
                except HTTPException:
                    pass
        if rejected:
            c.execute("UPDATE g3_intent SET state='BLOCKED' WHERE id=%s", (i["id"],))
        return result


def safety_scan(db, tenant, listing_ids=None, scheduled=False):
    # Claim due scans briefly; release row locks BEFORE acquiring a source lock.
    due = " AND safety_due_at<=clock_timestamp()" if scheduled else ""
    with db.transaction(tenant) as c:
        rows = c.execute(
            "SELECT id FROM g3_listing WHERE intent_id IS NOT NULL AND state!='STOPPED'"
            + due
            + " AND (%s::uuid[] IS NULL OR id=ANY(%s)) ORDER BY safety_due_at,id LIMIT %s FOR UPDATE SKIP LOCKED",
            (listing_ids, listing_ids, 2 if scheduled else 20),
        ).fetchall()
        for r in rows:
            c.execute(
                "UPDATE g3_listing SET safety_due_at=clock_timestamp()+interval '3 seconds' WHERE id=%s",
                (r["id"],),
            )
    for r in rows:
        with db.transaction(tenant) as c:
            li = get(c, "g3_listing", r["id"])
            domain.lock_source(c, tenant, li["source_key"])
            li = get(c, "g3_listing", r["id"], True)
            intent = get(c, "g3_intent", li["intent_id"])
            if intent["action"] == "STOP":
                if intent["state"] == "VERIFIED":
                    continue
                if now() - intent["created_at"] > timedelta(minutes=5):
                    task = c.execute(
                        "SELECT * FROM g3_task WHERE intent_id=%s ORDER BY chunk_index LIMIT 1",
                        (intent["id"],),
                    ).fetchone()
                    alert = c.execute(
                        "INSERT INTO g3_dead_letter(id,tenant_id,task_id,cycle,code) VALUES(%s,%s,%s,%s,'STOP_UNCONFIRMED_OVER_5_MINUTES') ON CONFLICT DO NOTHING RETURNING id",
                        (uuid4(), tenant, task["id"], task["budget_cycle"]),
                    ).fetchone()
                    if alert:
                        audit(
                            c,
                            {"tenant_id": tenant, "subject": "system.safety"},
                            "g3.stop.overdue",
                            li["id"],
                            "Remote close remains unconfirmed; no false STOPPED",
                        )
                continue
            s = get(c, "g3_snapshot", intent["snapshot_id"])
            deficits = domain.capacity_deficits(c, li["source_key"])
            if deficits:
                affected = sorted(set(deficits) & {x["date"] for x in intent["payload"]["cells"]})
                if affected:
                    domain.stop(
                        c,
                        {"tenant_id": tenant, "subject": "system.safety", "role": "tenant_admin"},
                        li["id"],
                        "capacity:" + str(li["desired_revision"]),
                        li["version"],
                        affected,
                        "Safety close: CAPACITY_DEFICIT",
                        system=True,
                    )
                    continue
            try:
                with c.transaction():
                    domain.validate_snapshot(c, s)
            except HTTPException as exc:
                # Allocation reconfiguration is not by itself a source withdrawal.
                if exc.detail in {"ALLOCATION_STALE", "POLICY_AUTHORIZATION_REVOKED"}:
                    continue
                actor = {"tenant_id": tenant, "subject": "system.safety", "role": "tenant_admin"}
                domain.stop(
                    c,
                    actor,
                    li["id"],
                    "safety:" + str(li["desired_revision"]),
                    li["version"],
                    sorted({x["date"] for x in intent["payload"]["cells"]}),
                    "Safety close: " + str(exc.detail),
                    system=True,
                )


def tick(db, owner, tenants=None, listing_ids=None):
    if listing_ids is not None:
        from uuid import UUID

        listing_ids = [UUID(str(x)) for x in listing_ids]
    tenants = tenants or [uid("tenant-A"), uid("tenant-B")]
    worked = False
    for tenant in tenants:
        for _ in range(10):
            if not relay(db, tenant):
                break
            worked = True
        safety_scan(db, tenant, listing_ids, scheduled=True)
        with db.transaction(tenant) as c:
            due = c.execute(
                "SELECT * FROM g3_listing WHERE intent_id IS NOT NULL AND next_due_at<=clock_timestamp() AND (%s::uuid[] IS NULL OR id=ANY(%s)) ORDER BY next_due_at LIMIT 10 FOR UPDATE SKIP LOCKED",
                (listing_ids, listing_ids),
            ).fetchall()
            for li in due:
                key = str(li["id"]) + ":" + li["next_due_at"].isoformat()
                pending_read = c.execute(
                    "SELECT id,kind,state FROM execution_job WHERE kind IN ('G3_RECON','G3_VERIFY') AND resource_id=%s AND state IN ('READY','RETRY_WAIT','RUNNING') ORDER BY (kind='G3_VERIFY') DESC,created_at LIMIT 1",
                    (li["id"],),
                ).fetchone()
                needs_verification = li["observed_revision"] != li["desired_revision"]
                if not pending_read:
                    jobs.enqueue(
                        c, tenant, "G3_VERIFY" if needs_verification else "G3_RECON", li["id"], key
                    )
                elif (
                    needs_verification
                    and pending_read["kind"] == "G3_RECON"
                    and pending_read["state"] != "RUNNING"
                ):
                    c.execute(
                        "UPDATE execution_job SET kind='G3_VERIFY' WHERE id=%s",
                        (pending_read["id"],),
                    )
                c.execute(
                    "UPDATE g3_listing SET next_due_at=clock_timestamp()+CASE WHEN state='STOPPED' THEN interval '5 minutes' ELSE interval '60 seconds' END WHERE id=%s",
                    (li["id"],),
                )
            # A stop across any listing precedes ordinary queued work; same-account quota still applies.
            preferred = c.execute(
                "SELECT j.id FROM execution_job j JOIN g3_task t ON (t.tenant_id,t.id)=(j.tenant_id,j.resource_id) JOIN g3_intent i ON (i.tenant_id,i.id)=(t.tenant_id,t.intent_id) WHERE j.tenant_id=%s AND j.kind='G3_DELIVER' AND i.action='STOP' AND (%s::uuid[] IS NULL OR i.listing_id=ANY(%s)) AND j.state IN ('READY','RETRY_WAIT') AND j.next_run_at<=clock_timestamp() ORDER BY j.created_at LIMIT 1",
                (tenant, listing_ids, listing_ids),
            ).fetchone()
            if preferred:
                c.execute(
                    "UPDATE execution_job SET next_run_at=clock_timestamp()-interval '1 hour' WHERE id=%s",
                    (preferred["id"],),
                )
            resources = None
            if listing_ids is not None:
                resources = [
                    *listing_ids,
                    *[
                        r["id"]
                        for r in c.execute(
                            "SELECT t.id FROM g3_task t JOIN g3_intent i ON (i.tenant_id,i.id)=(t.tenant_id,t.intent_id) WHERE t.tenant_id=%s AND i.listing_id=ANY(%s)",
                            (tenant, listing_ids),
                        ).fetchall()
                    ],
                ]
            turns = getattr(db, "g5_delivery_turns", {})
            turn = turns.get(str(tenant), 0)
            turns[str(tenant)] = (turn + 1) % 5
            db.g5_delivery_turns = turns
            first = ["G3_DELIVER", "G3_VERIFY"] if preferred or turn < 4 else ["G3_RECON"]
            second = ["G3_RECON"] if len(first) == 2 else ["G3_DELIVER", "G3_VERIFY"]
            job = jobs.claim(c, owner, kinds=first, resource_ids=resources)
            if not job:
                job = jobs.claim(c, owner, kinds=second, resource_ids=resources)
        if job:
            try:
                execute(db, job)
            except HTTPException as exc:
                if exc.detail != "LEASE_LOST":
                    with db.transaction(tenant) as c:
                        try:
                            later(c, job, 1, str(exc.detail))
                        except HTTPException:
                            pass
            except Exception as exc:
                import logging

                logging.getLogger("hotel.g3").error("G3 job failed: %s", type(exc).__name__)
                with db.transaction(tenant) as c:
                    try:
                        later(c, job, 1, type(exc).__name__)
                    except HTTPException:
                        pass
            worked = True
    return worked
