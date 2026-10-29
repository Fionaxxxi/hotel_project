from uuid import UUID

from fastapi import Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from services.core.g1.common import audit as write_audit
from services.core.g1.common import authorize
from services.core.g5 import audit, operations


class Acknowledge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500)


class Release(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_key: str = Field(min_length=1, max_length=300)
    reason: str = Field(min_length=1, max_length=500)


def mount(app, db, identity):
    @app.get("/v5/recovery/scopes")
    def scopes(actor=Depends(identity)):
        with db.transaction(actor["tenant_id"]) as c:
            return {
                "items": c.execute(
                    "SELECT * FROM g5_recovery_scope ORDER BY created_at DESC,source_key LIMIT 100"
                ).fetchall()
            }

    @app.post("/v5/recovery/listings/{lid}/stop")
    def recovery_stop(lid: UUID, body: Acknowledge, actor=Depends(identity)):
        from services.core.g5.recovery import stop_restored

        return stop_restored(db, actor, lid, body.expected_version, body.reason)

    @app.post("/v5/recovery/release")
    def release(body: Release, actor=Depends(identity)):
        from services.core.g5.recovery import release_scope

        return release_scope(db, actor, body.source_key, body.reason)

    @app.get("/v5/operations/metrics")
    def metrics(actor=Depends(identity)):
        with db.transaction(actor["tenant_id"]) as c:
            data = operations.metrics(c)
        return data

    @app.get("/v5/operations/alerts")
    def alerts(
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0, le=10000),
        actor=Depends(identity),
    ):
        with db.transaction(actor["tenant_id"]) as c:
            return {
                "items": c.execute(
                    "SELECT * FROM g5_alert ORDER BY first_seen DESC,id LIMIT %s OFFSET %s",
                    (limit, offset),
                ).fetchall(),
                "limit": limit,
                "offset": offset,
            }

    @app.post("/v5/operations/collect")
    def collect(actor=Depends(identity)):
        authorize(actor)
        with db.transaction(actor["tenant_id"]) as c:
            return operations.collect(c, actor)

    @app.post("/v5/operations/alerts/{aid}/acknowledge")
    def ack(aid: UUID, body: Acknowledge, actor=Depends(identity)):
        with db.transaction(actor["tenant_id"]) as c:
            return operations.acknowledge(c, actor, aid, body.expected_version, body.reason)

    @app.get("/v5/audit/{kind}/{rid}")
    def replay(
        kind: str, rid: UUID, limit: int = Query(400, ge=10, le=600), actor=Depends(identity)
    ):
        with db.transaction(actor["tenant_id"], repeatable=True) as c:
            return audit.replay(c, kind, rid, limit)

    @app.post("/v5/audit/{kind}/{rid}/export")
    def export(kind: str, rid: UUID, actor=Depends(identity)):
        authorize(actor, {"auditor", "tenant_admin"})
        with db.transaction(actor["tenant_id"], repeatable=True) as c:
            data = audit.replay(c, kind, rid)
            package = audit.evidence_package(data)
            write_audit(c, actor, "g5.audit.export", rid, "Tenant-scoped redacted evidence export")
            return package
