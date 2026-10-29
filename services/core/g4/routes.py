import os
from uuid import UUID

from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from services.core.g1.common import authorize, check_version, get
from services.core.g4 import ai, policy, workflow
from services.core.g4.local_model import DIRECTORY, REVISION


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Limits(Body):
    auto_activate: StrictBool = False
    auto_publish: StrictBool = False
    allow_repair: StrictBool = False
    relative_limit: str = Field(default="0.10", pattern=r"^\d+(\.\d{1,4})?$")
    absolute_limit: str = Field(default="20.00", pattern=r"^\d+(\.\d{1,4})?$")
    inventory_limit: int = Field(default=0, strict=True, ge=0, le=100)


class CreatePolicy(Body):
    baseline_ids: list[UUID] = Field(min_length=2, max_length=2)
    settings: Limits = Field(default_factory=Limits)


class Command(Body):
    expected_version: int = Field(ge=1)
    action: str
    reason: str = Field(min_length=1, max_length=500)


class Subscribe(Body):
    policy_id: UUID


class Suggest(Body):
    source_id: UUID


class Rebind(Body):
    policy_id: UUID
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500)


def mount(app, db, identity):
    @app.post("/v4/suggestions/{tid}/retry")
    def retry_suggestion(tid: UUID, body: Command, actor=Depends(identity)):
        from services.core.g1.common import audit, fail

        authorize(actor)
        with db.transaction(actor["tenant_id"]) as c:
            task = get(c, "g4_ai_task", tid, True)
            if (
                body.expected_version != task["fence"]
                or body.action != "retry"
                or task["state"] != "COMPLETED"
                or task["result"]["status"] != "RULE_FALLBACK"
                or task["budget_cycle"] >= 3
            ):
                fail("AI_RETRY_NOT_ALLOWED")
            c.execute(
                "UPDATE g4_ai_task SET state='READY',result=NULL,attempts=0,budget_cycle=budget_cycle+1,fence=fence+1 WHERE id=%s",
                (tid,),
            )
            audit(c, actor, "g4.ai.budget-renewed", tid, body.reason)
            return get(c, "g4_ai_task", tid)

    @app.post("/v4/subscriptions/{sid}/policy")
    def rebind(sid: UUID, body: Rebind, actor=Depends(identity)):
        from services.core.g1.common import audit, fail
        from services.core.g3.domain import lock_source

        authorize(actor, {"tenant_admin"})
        with db.transaction(actor["tenant_id"]) as c:
            sub = get(c, "g4_subscription", sid)
            lock_source(c, actor["tenant_id"], sub["source_key"])
            sub = get(c, "g4_subscription", sid, True)
            check_version(sub, body.expected_version)
            old = get(c, "g4_policy", sub["policy_id"])
            new = get(c, "g4_policy", body.policy_id)
            if (
                old["state"] == "ENABLED"
                or new["state"] != "ENABLED"
                or new["source_key"] != sub["source_key"]
            ):
                fail("POLICY_REBIND_REQUIRES_OLD_DISABLED_AND_NEW_ENABLED")
            c.execute(
                "UPDATE g4_subscription SET policy_id=%s,initial_activation_id=%s,version=version+1 WHERE id=%s",
                (new["id"], UUID(new["body"]["activation_id"]), sid),
            )
            c.execute(
                "UPDATE g4_run SET snapshot_ids='[]',version=version+1 WHERE subscription_id=%s AND plan_id IS NULL AND state NOT IN ('COMPLETED','STALE','CANCELLED')",
                (sid,),
            )
            audit(
                c,
                actor,
                "g4.subscription.policy-rebound",
                sid,
                str(old["id"]) + " -> " + str(new["id"]) + ": " + body.reason,
            )
            return get(c, "g4_subscription", sid)

    @app.get("/v4/overview")
    def overview(actor=Depends(identity)):
        with db.transaction(actor["tenant_id"]) as c:
            return {
                "policies": c.execute(
                    "SELECT * FROM g4_policy ORDER BY created_at DESC LIMIT 100"
                ).fetchall(),
                "subscriptions": c.execute(
                    "SELECT * FROM g4_subscription ORDER BY created_at DESC LIMIT 100"
                ).fetchall(),
                "runs": c.execute(
                    "SELECT * FROM g4_run ORDER BY created_at DESC LIMIT 100"
                ).fetchall(),
                "suggestions": c.execute(
                    "SELECT * FROM g4_ai_task ORDER BY created_at DESC LIMIT 50"
                ).fetchall(),
                "model": {
                    "enabled": os.environ.get("G4_AI_ENABLED", "false") == "true",
                    "prepared": (DIRECTORY / "manifest.json").exists(),
                    "revision": REVISION,
                    "mode": "LOCAL_ONNX",
                    "prompt_version": None,
                },
            }

    @app.post("/v4/suggestions", status_code=201)
    def suggest(body: Suggest, actor=Depends(identity)):
        with db.transaction(actor["tenant_id"]) as c:
            return ai.create(c, actor, body.source_id)

    @app.get("/v4/suggestions/{tid}")
    def suggestion(tid: UUID, actor=Depends(identity)):
        with db.transaction(actor["tenant_id"]) as c:
            return {
                **get(c, "g4_ai_task", tid),
                "attempt_history": c.execute(
                    "SELECT * FROM g4_ai_attempt WHERE task_id=%s ORDER BY created_at", (tid,)
                ).fetchall(),
            }

    @app.post("/v4/policies", status_code=201)
    def create_policy(body: CreatePolicy, actor=Depends(identity)):
        with db.transaction(actor["tenant_id"]) as c:
            return policy.create(c, actor, body.baseline_ids, body.settings.model_dump())

    @app.post("/v4/policies/{pid}/commands")
    def policy_command(pid: UUID, body: Command, actor=Depends(identity)):
        with db.transaction(actor["tenant_id"]) as c:
            return policy.command(c, actor, pid, body.expected_version, body.action, body.reason)

    @app.post("/v4/subscriptions", status_code=201)
    def subscription(body: Subscribe, actor=Depends(identity)):
        with db.transaction(actor["tenant_id"]) as c:
            return workflow.subscribe(c, actor, body.policy_id)

    @app.post("/v4/subscriptions/{sid}/commands")
    def subscription_command(sid: UUID, body: Command, actor=Depends(identity)):
        authorize(actor, {"tenant_admin"})
        from services.core.g1.common import audit, fail
        from services.core.g3.domain import lock_source

        with db.transaction(actor["tenant_id"]) as c:
            s = get(c, "g4_subscription", sid)
            lock_source(c, actor["tenant_id"], s["source_key"])
            s = get(c, "g4_subscription", sid, True)
            check_version(s, body.expected_version)
            if body.action not in {"pause", "resume"}:
                fail("INVALID_COMMAND", 422)
            c.execute(
                "UPDATE g4_subscription SET state=%s,version=version+1 WHERE id=%s",
                ("PAUSED" if body.action == "pause" else "ACTIVE", sid),
            )
            audit(c, actor, "g4.subscription." + body.action, sid, body.reason)
            return get(c, "g4_subscription", sid)

    @app.get("/v4/runs/{rid}")
    def run(rid: UUID, actor=Depends(identity)):
        with db.transaction(actor["tenant_id"]) as c:
            result = get(c, "g4_run", rid)
            return {
                **result,
                "steps": c.execute(
                    "SELECT * FROM g4_step WHERE run_id=%s ORDER BY created_at", (rid,)
                ).fetchall(),
                "decisions": c.execute(
                    "SELECT * FROM g4_decision WHERE snapshot_id=ANY(%s::uuid[]) ORDER BY created_at",
                    ([UUID(x) for x in result["snapshot_ids"]],),
                ).fetchall(),
            }
