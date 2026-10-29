from uuid import UUID

from fastapi import Depends, Header
from pydantic import BaseModel, ConfigDict, Field

from services.core.g1.common import audit, authorize, check_version, fail, get
from services.core.g3 import domain, legacy


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Allocation(Body):
    activation_id: UUID
    expected_version: int = Field(ge=0)
    buffer: int = Field(ge=0, le=100000)
    amounts: dict[str, int]


class Compile(Body):
    allocation_id: UUID
    channel: str
    plan_codes: list[str] = Field(min_length=1, max_length=20)


class Decision(Body):
    expected_version: int = Field(ge=1)
    decision: str
    reason: str = Field(min_length=1, max_length=500)
    allow_repair: bool = False


class Publish(Body):
    snapshot_ids: list[UUID] = Field(min_length=1, max_length=10)
    expected_revisions: dict[str, int]
    reopen: bool = False


class Stop(Body):
    expected_version: int = Field(ge=1)
    dates: list[str] = Field(min_length=1, max_length=366)
    reason: str = Field(min_length=1, max_length=500)


class Version(Body):
    expected_version: int = Field(ge=1)


class Command(Version):
    action: str
    reason: str = Field(min_length=1, max_length=500)


def mount(app, db, identity):
    @app.get("/v3/sources")
    def sources(actor=Depends(identity)):
        with db.transaction(actor["tenant_id"]) as c:
            return {
                "items": c.execute(
                    "SELECT DISTINCT a.id AS activation_id,a.property_code,s.code AS supplier_code,g.legacy_state FROM source_partition p JOIN source_activation a ON a.id=p.activation_id JOIN supplier s ON s.id=a.supplier_id LEFT JOIN g3_source_control g ON g.source_key=a.supplier_id::text||'/'||a.property_code WHERE NOT EXISTS(SELECT 1 FROM source_head h WHERE h.supplier_id=a.supplier_id AND h.property_code=a.property_code) OR EXISTS(SELECT 1 FROM source_head h JOIN generation_activation l ON l.generation_id=h.generation_id WHERE h.supplier_id=a.supplier_id AND h.property_code=a.property_code AND l.activation_id=a.id) ORDER BY a.property_code LIMIT 100"
                ).fetchall()
            }

    @app.post("/v3/sources/{aid}/adopt")
    def adopt(aid: UUID, actor=Depends(identity)):
        return legacy.inspect(db, actor, aid)

    @app.post("/v3/sources/{aid}/close-legacy")
    def close_legacy(aid: UUID, actor=Depends(identity)):
        return legacy.inspect(db, actor, aid, True)

    @app.get("/v3/overview")
    def overview(actor=Depends(identity)):
        with db.transaction(actor["tenant_id"]) as c:
            return {
                "simulation": True,
                "accounts": c.execute(
                    "SELECT id,code,capability,state,version,next_write_at,next_read_at,cooldown_until FROM g3_account ORDER BY code"
                ).fetchall(),
                "allocations": c.execute(
                    "SELECT * FROM g3_allocation ORDER BY created_at DESC LIMIT 100"
                ).fetchall(),
                "snapshots": c.execute(
                    "SELECT * FROM g3_snapshot ORDER BY created_at DESC LIMIT 100"
                ).fetchall(),
                "plans": c.execute(
                    "SELECT * FROM g3_plan ORDER BY created_at DESC LIMIT 100"
                ).fetchall(),
                "listings": c.execute(
                    "SELECT l.*,a.code FROM g3_listing l JOIN g3_account a ON a.id=l.account_id ORDER BY l.listing_key LIMIT 100"
                ).fetchall(),
                "diffs": c.execute(
                    "SELECT * FROM g3_diff ORDER BY created_at DESC LIMIT 100"
                ).fetchall(),
                "dead_letters": c.execute(
                    "SELECT d.*,t.version AS task_version,t.state AS task_state FROM g3_dead_letter d JOIN g3_task t ON t.id=d.task_id ORDER BY d.created_at DESC LIMIT 100"
                ).fetchall(),
            }

    @app.post("/v3/allocations", status_code=201)
    def allocate(b: Allocation, actor=Depends(identity)):
        with db.transaction(actor["tenant_id"]) as c:
            return domain.allocate(
                c, actor, b.activation_id, b.amounts, b.buffer, b.expected_version
            )

    @app.post("/v3/snapshots", status_code=201)
    def compile(b: Compile, actor=Depends(identity)):
        with db.transaction(actor["tenant_id"]) as c:
            return domain.compile_snapshot(c, actor, b.allocation_id, b.channel, b.plan_codes)

    @app.get("/v3/snapshots/{sid}")
    def snapshot(sid: UUID, actor=Depends(identity)):
        with db.transaction(actor["tenant_id"]) as c:
            return get(c, "g3_snapshot", sid)

    @app.get("/v3/snapshots/{sid}/provenance")
    def provenance(sid: UUID, actor=Depends(identity)):
        from services.core.g1.offers import provenance

        with db.transaction(actor["tenant_id"]) as c:
            s = get(c, "g3_snapshot", sid)
            return {
                "snapshot": s,
                "allocation": get(c, "g3_allocation", s["allocation_id"]),
                "source": provenance(c, s["base_snapshot_id"]),
            }

    @app.post("/v3/snapshots/{sid}/decisions")
    def decide(sid: UUID, b: Decision, actor=Depends(identity)):
        with db.transaction(actor["tenant_id"]) as c:
            return domain.decide(
                c, actor, sid, b.expected_version, b.decision, b.reason, b.allow_repair
            )

    @app.post("/v3/plans", status_code=202)
    def publish(
        b: Publish,
        idempotency_key: str = Header(min_length=1, max_length=200),
        actor=Depends(identity),
    ):
        with db.transaction(actor["tenant_id"]) as c:
            return domain.publish(
                c, actor, b.snapshot_ids, idempotency_key, b.expected_revisions, b.reopen
            )

    @app.get("/v3/plans/{pid}")
    def plan(pid: UUID, actor=Depends(identity)):
        with db.transaction(actor["tenant_id"]) as c:
            return domain.plan_detail(c, pid)

    @app.post("/v3/plans/{pid}/cancel")
    def cancel(pid: UUID, b: Command, actor=Depends(identity)):
        with db.transaction(actor["tenant_id"]) as c:
            return domain.cancel_initial(c, actor, pid, b.reason)

    @app.get("/v3/listings/{lid}")
    def listing(lid: UUID, actor=Depends(identity)):
        with db.transaction(actor["tenant_id"]) as c:
            li = get(c, "g3_listing", lid)
            li["intent"] = get(c, "g3_intent", li["intent_id"]) if li["intent_id"] else None
            li["observations"] = c.execute(
                "SELECT * FROM g3_observation WHERE listing_id=%s ORDER BY created_at DESC LIMIT 20",
                (lid,),
            ).fetchall()
            return li

    @app.post("/v3/listings/{lid}/reconcile", status_code=202)
    def reconcile(lid: UUID, actor=Depends(identity)):
        authorize(actor)
        with db.transaction(actor["tenant_id"]) as c:
            li = get(c, "g3_listing", lid)
            li_id = li["id"]
            domain.lock_source(c, actor["tenant_id"], li["source_key"])
            get(c, "g3_listing", lid, True)
            from uuid import uuid4

            from services.core.g2.jobs import enqueue

            job = c.execute(
                "SELECT * FROM execution_job WHERE kind IN ('G3_RECON','G3_VERIFY') AND resource_id=%s AND state IN ('READY','RETRY_WAIT','RUNNING') ORDER BY (kind='G3_VERIFY') DESC,created_at LIMIT 1",
                (li_id,),
            ).fetchone()
            if job and job["kind"] == "G3_RECON" and job["state"] != "RUNNING":
                c.execute("UPDATE execution_job SET kind='G3_VERIFY' WHERE id=%s", (job["id"],))
            job = job or enqueue(
                c, actor["tenant_id"], "G3_VERIFY", li_id, "manual:" + str(uuid4())
            )
            audit(c, actor, "g3.reconcile.requested", lid, "On-demand observed-state check")
            return {"job_id": job["id"], "state": "QUEUED"}

    @app.post("/v3/listings/{lid}/stop", status_code=202)
    def stop(
        lid: UUID,
        b: Stop,
        idempotency_key: str = Header(min_length=1, max_length=200),
        actor=Depends(identity),
    ):
        with db.transaction(actor["tenant_id"]) as c:
            return domain.stop(
                c, actor, lid, idempotency_key, b.expected_version, b.dates, b.reason
            )

    @app.post("/v3/diffs/{did}/repair", status_code=202)
    def repair(did: UUID, b: Version, actor=Depends(identity)):
        with db.transaction(actor["tenant_id"]) as c:
            return domain.repair(c, actor, did, b.expected_version)

    @app.post("/v3/listings/{lid}/commands")
    def command(lid: UUID, b: Command, actor=Depends(identity)):
        authorize(actor)
        if b.action not in {"pause", "resume"}:
            fail("INVALID_COMMAND", 422)
        with db.transaction(actor["tenant_id"]) as c:
            row = get(c, "g3_listing", lid, True)
            check_version(row, b.expected_version)
            c.execute(
                "UPDATE g3_listing SET paused=%s,version=version+1 WHERE id=%s",
                (b.action == "pause", lid),
            )
            audit(c, actor, "g3.listing." + b.action, lid, b.reason)
            return get(c, "g3_listing", lid)

    @app.post("/v3/accounts/{aid}/resume")
    def resume_account(aid: UUID, b: Command, actor=Depends(identity)):
        authorize(actor, {"tenant_admin"})
        with db.transaction(actor["tenant_id"]) as c:
            row = get(c, "g3_account", aid, True)
            check_version(row, b.expected_version)
            c.execute("UPDATE g3_account SET state='ACTIVE',version=version+1 WHERE id=%s", (aid,))
            audit(c, actor, "g3.account.resumed", aid, b.reason)
            return {"id": aid, "state": "ACTIVE"}

    @app.post("/v3/dead-letters/{did}/retry")
    def retry(did: UUID, b: Command, actor=Depends(identity)):
        authorize(actor)
        with db.transaction(actor["tenant_id"]) as c:
            d = get(c, "g3_dead_letter", did, True)
            t = get(c, "g3_task", d["task_id"], True)
            check_version(t, b.expected_version)
            i = get(c, "g3_intent", t["intent_id"])
            li = get(c, "g3_listing", i["listing_id"])
            if li["intent_id"] != i["id"] or t["state"] != "MANUAL":
                fail("TASK_NOT_CURRENT_MANUAL")
            if t["budget_cycle"] >= 3:
                fail("MANUAL_BUDGET_LIMIT")
            if i["action"] != "STOP":
                domain.validate_snapshot(c, get(c, "g3_snapshot", i["snapshot_id"]))
            last = c.execute(
                "SELECT status,data FROM g3_receipt WHERE task_id=%s ORDER BY created_at DESC LIMIT 1",
                (t["id"],),
            ).fetchone()
            unknown = t["send_count"] > 0 and not (
                last
                and (last["status"] in {401, 403, 429} or last["data"].get("accepted") is False)
            )
            c.execute(
                "UPDATE g3_task SET state=%s,send_count=0,query_count=0,budget_cycle=budget_cycle+1,deadline=clock_timestamp()+interval '15 minutes',version=version+1 WHERE id=%s",
                ("UNKNOWN" if unknown else "READY", t["id"]),
            )
            c.execute("UPDATE g3_dead_letter SET state='RETRIED' WHERE id=%s", (did,))
            c.execute(
                "UPDATE execution_job SET state='READY',next_run_at=clock_timestamp(),attempt=0 WHERE kind='G3_DELIVER' AND resource_id=%s",
                (t["id"],),
            )
            audit(c, actor, "g3.deadletter.budget.renewed", did, b.reason)
            return {"state": "RETRIED", "task_id": t["id"]}
