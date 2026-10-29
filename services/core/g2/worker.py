"""Fair configured-tenant worker: no superuser or RLS bypass, no global dispatcher lock."""

import argparse
import logging
import os
import time
from uuid import uuid4

from fastapi import HTTPException

from infra.manage import uid
from services.core.config import Settings
from services.core.db import Database
from services.core.g1 import distribution
from services.core.g1.common import get
from services.core.g2 import jobs, processing, workflow
from services.core.g5.tracing import traced_job


@traced_job
def execute(db, job):
    tenant = job["tenant_id"]
    try:
        if job["kind"] == "PARSE":
            if job["attempt"] > job["max_attempts"]:
                with db.transaction(tenant) as c:
                    jobs.finish(c, job, "MANUAL", "RETRY_BUDGET_EXHAUSTED")
                    c.execute(
                        "UPDATE processing_run SET status='MANUAL',version=version+1 WHERE id=%s",
                        (job["resource_id"],),
                    )
                    jobs.emit(
                        c,
                        tenant,
                        "processing.completed",
                        job["resource_id"],
                        job["fencing_token"],
                        {"status": "MANUAL"},
                    )
                return
            processing.process(db, job)
        elif job["kind"] == "WORKFLOW":
            with db.transaction(tenant) as c:
                jobs.guard(c, job)
                workflow.advance(c, job["resource_id"], job["logical_key"].rsplit(":", 1)[-1])
                jobs.finish(c, job)
        elif job["kind"] == "PUBLISH":
            # Serialize one account/listing while allowing other listings/tenants concurrently.
            with db.pool.connection() as lock:
                with db.transaction(tenant) as c:
                    task = get(c, "publish_task", job["resource_id"])
                    snap = get(c, "offer_snapshot", task["snapshot_id"])
                    paused = c.execute(
                        "SELECT 1 FROM workflow_run WHERE task_id=%s AND paused", (task["id"],)
                    ).fetchone()
                    if task["status"] == "READY" and paused:
                        jobs.defer(c, job, "WORKFLOW_PAUSED")
                        return
                lock_key = str(tenant) + str(snap["channel_id"]) + snap["listing_key"]
                acquired = lock.execute(
                    "SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS ok", (lock_key,)
                ).fetchone()["ok"]
                lock.commit()
                if not acquired:
                    with db.transaction(tenant) as c:
                        jobs.defer(c, job, "LISTING_BUSY")
                    return
                try:
                    if task["status"] == "READY":
                        result = distribution.dispatch_one(db, tenant, task["id"], lease=job)
                    elif task["status"] == "VERIFIED":
                        result = task
                    else:
                        result = distribution.query_remote(db, tenant, task["id"], lease=job)
                    with db.transaction(tenant) as c:
                        jobs.guard(c, job)
                        jobs.emit(
                            c,
                            tenant,
                            "task.updated",
                            task["id"],
                            job["fencing_token"],
                            {"status": result["status"]},
                        )
                        jobs.finish(
                            c,
                            job,
                            "SUCCEEDED"
                            if result["status"] == "VERIFIED"
                            else "BLOCKED"
                            if result["status"] in {"FAILED", "BLOCKED"}
                            else "RETRY_WAIT",
                            result.get("error_code"),
                        )
                finally:
                    lock.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", (lock_key,))
                    lock.commit()
        else:
            with db.transaction(tenant) as c:
                jobs.finish(c, job, "BLOCKED", "UNKNOWN_JOB_KIND")
    except Exception as exc:
        if isinstance(exc, HTTPException) and exc.detail == "LEASE_LOST":
            return
        with db.transaction(tenant) as c:
            try:
                jobs.finish(
                    c,
                    job,
                    "RETRY_WAIT",
                    str(exc.detail) if isinstance(exc, HTTPException) else type(exc).__name__,
                )
                if job["kind"] == "PARSE" and job["attempt"] >= job["max_attempts"]:
                    c.execute(
                        "UPDATE processing_run SET status='MANUAL',version=version+1 WHERE id=%s",
                        (job["resource_id"],),
                    )
                    jobs.emit(
                        c,
                        tenant,
                        "processing.completed",
                        job["resource_id"],
                        job["fencing_token"],
                        {"status": "MANUAL"},
                    )
            except HTTPException:
                pass
        logging.getLogger("hotel.worker").error("job %s failed: %s", job["id"], type(exc).__name__)


def tick(db, owner, tenants=None, kinds=None, resources=None, event_resources=None):
    kinds = kinds or ["PARSE", "WORKFLOW", "PUBLISH"]
    # This is trusted server configuration, never a tenant supplied by an HTTP task body.
    tenants = tenants or [
        uid("tenant-" + label) for label in os.environ.get("HOTEL_WORKER_TENANTS", "A,B").split(",")
    ]
    worked = False
    for tenant in tenants:
        try:
            for _ in range(10):
                event_id = None
                if event_resources is not None:
                    with db.transaction(tenant) as c:
                        event = c.execute(
                            "SELECT id FROM outbox_event WHERE aggregate_id=ANY(%s::uuid[]) AND delivered_at IS NULL AND next_run_at<=clock_timestamp() AND attempt<5 ORDER BY created_at LIMIT 1",
                            (event_resources,),
                        ).fetchone()
                    if not event:
                        break
                    event_id = event["id"]
                if not workflow.relay_one(db, tenant, event_id=event_id):
                    break
                worked = True
        except Exception as exc:
            logging.getLogger("hotel.worker").error("relay failed: %s", type(exc).__name__)
        with db.transaction(tenant) as c:
            job = jobs.claim(c, owner, kinds=kinds, resource_ids=resources)
        if job:
            execute(db, job)
            worked = True
    return worked


def main(once=False, lane="all"):
    from services.core.g5.logging import configure

    configure("g2-" + lane)
    db = Database(Settings.load().database_url, max_size=4)
    db.pool.open()
    owner = str(uuid4())
    try:
        while True:
            from services.core.g5.worker import periodic_heartbeat

            periodic_heartbeat(db, owner, "g2-" + lane)
            kinds = {
                "all": None,
                "parse": ["PARSE"],
                "control": ["WORKFLOW"],
                "publish": ["PUBLISH"],
            }[lane]
            tick(db, owner, kinds=kinds)
            if once:
                return
            time.sleep(0.2)
    finally:
        db.pool.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--once", action="store_true")
    p.add_argument("--lane", choices=["all", "parse", "control", "publish"], default="all")
    args = p.parse_args()
    main(args.once, args.lane)
