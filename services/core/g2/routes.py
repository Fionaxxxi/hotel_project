import csv
import hashlib
import io
import json
import shutil
from uuid import UUID, uuid4

from fastapi import Depends, Header, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from services.core.g1.common import audit, authorize, check_version, fail, get
from services.core.g1.ingestion import STORE
from services.core.g2 import jobs, processing, views


class BodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") != "/v2/imports":
            return await self.app(scope, receive, send)
        total = 0

        async def limited():
            nonlocal total
            msg = await receive()
            total += len(msg.get("body", b""))
            if total > 128 * 1024 * 1024:
                fail("UPLOAD_TOO_LARGE", 413)
            return msg

        await self.app(scope, limited, send)


class RunBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template: str = "supplier-v2"
    normalizer: str = "normalized-v2.0"


class ActivateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_versions: dict[str, int]
    selected_dates: list[str]
    partial_confirmed: bool = False
    expected_run_version: int = Field(ge=1)


class WorkflowCommand(BaseModel):
    expected_version: int = Field(ge=1)
    action: str


def mount(app, db, identity):
    app.add_middleware(BodyLimit)

    @app.post("/v2/imports", status_code=201)
    async def upload(request: Request, actor=Depends(identity)):
        authorize(actor)
        form = await request.form(max_files=6, max_fields=0)
        bid = uuid4()
        folder = STORE / str(bid)
        folder.mkdir(parents=True, exist_ok=False)
        metadata = []
        names = []
        total = 0
        keep = False
        try:
            for _, file in form.multi_items():
                if (
                    not hasattr(file, "filename")
                    or file.filename
                    not in {
                        "manifest.json",
                        "data.json",
                        "data.xlsx",
                        "properties.csv",
                        "rooms.csv",
                        "rate_plans.csv",
                        "inventory.csv",
                        "rates.csv",
                    }
                    or file.filename in names
                ):
                    fail("INVALID_FILE_NAME", 422)
                names.append(file.filename)
                h = hashlib.sha256()
                size = 0
                with (folder / file.filename).open("wb") as output:
                    while chunk := await file.read(1024 * 1024):
                        size += len(chunk)
                        total += len(chunk)
                        if size > 64 * 1024 * 1024 or total > 120 * 1024 * 1024:
                            fail("UPLOAD_TOO_LARGE", 413)
                        h.update(chunk)
                        output.write(chunk)
                metadata.append({"name": file.filename, "sha256": h.hexdigest(), "size": size})
            if "manifest.json" not in names:
                fail("MANIFEST_REQUIRED", 422)
            if (folder / "manifest.json").stat().st_size > 256 * 1024:
                fail("MANIFEST_TOO_LARGE", 413)
            try:
                manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
            except (ValueError, UnicodeError):
                fail("MANIFEST_INVALID", 422)
            processing.validate_manifest(manifest, names)
            with db.transaction(actor["tenant_id"], actor["subject"]) as c:
                result = processing.register(c, actor, bid, manifest, metadata)
            keep = not result["reused"]
            return result
        finally:
            if not keep and folder.resolve().is_relative_to(STORE.resolve()):
                shutil.rmtree(folder)
            await form.close()

    @app.get("/v2/imports")
    def imports(actor=Depends(identity)):
        with db.transaction(actor["tenant_id"], actor["subject"]) as c:
            return {
                "items": c.execute(
                    "SELECT id,status,version,manifest,created_at FROM import_batch WHERE manifest->>'schema_version'='2.0' ORDER BY created_at DESC LIMIT 100"
                ).fetchall()
            }

    @app.get("/v2/imports/{bid}/preview")
    def preview(bid: UUID, actor=Depends(identity)):
        with db.transaction(actor["tenant_id"], actor["subject"]) as c:
            return processing.preview(c, bid)

    @app.post("/v2/imports/{bid}/runs", status_code=202)
    def start(bid: UUID, body: RunBody, actor=Depends(identity)):
        with db.transaction(actor["tenant_id"], actor["subject"]) as c:
            return processing.start(c, actor, bid, body.template, body.normalizer)

    @app.get("/v2/processing-runs")
    def runs(actor=Depends(identity)):
        with db.transaction(actor["tenant_id"], actor["subject"]) as c:
            return {
                "items": c.execute(
                    "SELECT * FROM processing_run ORDER BY created_at DESC LIMIT 100"
                ).fetchall()
            }

    @app.get("/v2/processing-runs/{rid}")
    def run(rid: UUID, actor=Depends(identity)):
        with db.transaction(actor["tenant_id"], actor["subject"]) as c:
            result = get(c, "processing_run", rid)
            result["checkpoints"] = c.execute(
                "SELECT kind,position FROM parse_checkpoint WHERE run_id=%s", (rid,)
            ).fetchall()
            result["workflow"] = c.execute(
                "SELECT * FROM workflow_run WHERE processing_id=%s", (rid,)
            ).fetchone()
            result["issue_count"] = c.execute(
                "SELECT count(*) AS n FROM dq_issue WHERE run_id=%s", (rid,)
            ).fetchone()["n"]
            batch = get(c, "import_batch", result["batch_id"])
            scope = batch["manifest"]["scope"]
            result["scope"] = scope
            current = views.versions(c, batch["supplier_id"], scope["property_codes"][0])
            result["expected_versions"] = {
                day: current.get(day, {}).get("version", 0)
                for day in ["MASTER", *views.dates(scope)]
            }
            return result

    @app.get("/v2/processing-runs/{rid}/issues")
    def issues(
        rid: UUID,
        limit: int = 100,
        offset: int = 0,
        partition: str | None = None,
        actor=Depends(identity),
    ):
        if not 1 <= limit <= 500 or offset < 0:
            fail("INVALID_PAGINATION", 422)
        with db.transaction(actor["tenant_id"], actor["subject"]) as c:
            get(c, "processing_run", rid)
            return {
                "items": c.execute(
                    "SELECT * FROM dq_issue WHERE run_id=%s AND (%s::text IS NULL OR partition_key=%s) ORDER BY id LIMIT %s OFFSET %s",
                    (rid, partition, partition, limit, offset),
                ).fetchall()
            }

    @app.get("/v2/processing-runs/{rid}/issues.csv")
    def export(rid: UUID, actor=Depends(identity)):
        # Bounded export page; raw source values are not echoed as executable spreadsheet cells.
        with db.transaction(actor["tenant_id"], actor["subject"]) as c:
            get(c, "processing_run", rid)
            rows = c.execute(
                "SELECT code,partition_key,location FROM dq_issue WHERE run_id=%s LIMIT 10000",
                (rid,),
            ).fetchall()
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(["code", "partition", "location"])
        for row in rows:
            values = [
                str(row["code"]),
                str(row["partition_key"]),
                json.dumps(row["location"], ensure_ascii=False),
            ]
            from services.core.g5.security import safe_csv

            writer.writerow([safe_csv(v) for v in values])
        return Response(
            stream.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="quality.csv"'},
        )

    @app.post("/v2/processing-runs/{rid}/activate")
    def activate(
        rid: UUID,
        body: ActivateBody,
        idempotency_key: str = Header(min_length=1, max_length=200),
        actor=Depends(identity),
    ):
        with db.transaction(actor["tenant_id"], actor["subject"]) as c:
            # The idempotent completed request is checked before version rejection.
            prior = c.execute(
                "SELECT 1 FROM activation_command WHERE run_id=%s AND key=%s",
                (rid, idempotency_key),
            ).fetchone()
            if not prior:
                check_version(get(c, "processing_run", rid), body.expected_run_version)
            return views.activate(c, actor, rid, body.model_dump(), idempotency_key)

    @app.get("/v2/workflows/{wid}")
    def workflow(wid: UUID, actor=Depends(identity)):
        with db.transaction(actor["tenant_id"], actor["subject"]) as c:
            result = get(c, "workflow_run", wid)
            result["steps"] = c.execute(
                "SELECT * FROM workflow_step WHERE run_id=%s ORDER BY step_key", (wid,)
            ).fetchall()
            result["waits"] = c.execute(
                "SELECT * FROM workflow_wait WHERE run_id=%s", (wid,)
            ).fetchall()
            return result

    @app.post("/v2/workflows/{wid}/commands")
    def command(wid: UUID, body: WorkflowCommand, actor=Depends(identity)):
        authorize(actor)
        with db.transaction(actor["tenant_id"], actor["subject"]) as c:
            row = get(c, "workflow_run", wid, True)
            check_version(row, body.expected_version)
            if body.action == "pause":
                c.execute(
                    "UPDATE workflow_run SET paused=true,version=version+1 WHERE id=%s", (wid,)
                )
            elif body.action == "resume":
                if row["state"] == "CANCELLED":
                    fail("CANCELLED_RUN_CANNOT_RESUME")
                if row["snapshot_id"] and not row["task_id"]:
                    from services.core.g1.common import now

                    snapshot = get(c, "offer_snapshot", row["snapshot_id"])
                    if snapshot["status"] == "REJECTED" or snapshot["expires_at"] <= now():
                        c.execute("UPDATE workflow_run SET snapshot_id=NULL WHERE id=%s", (wid,))
                c.execute(
                    "UPDATE workflow_run SET paused=false,version=version+1 WHERE id=%s", (wid,)
                )
                jobs.emit(c, actor["tenant_id"], "workflow.resume", wid, row["version"] + 1, {})
            elif body.action == "cancel":
                if row["task_id"]:
                    task = get(c, "publish_task", row["task_id"], True)
                    if task["status"] in {"SENDING", "UNKNOWN", "ACKNOWLEDGED", "VERIFIED"}:
                        fail("EXTERNAL_EFFECT_MUST_BE_QUERIED")
                    c.execute(
                        "UPDATE publish_task SET status='BLOCKED',error_code='WORKFLOW_CANCELLED' WHERE id=%s",
                        (task["id"],),
                    )
                c.execute(
                    "UPDATE workflow_run SET state='CANCELLED',paused=true,version=version+1 WHERE id=%s",
                    (wid,),
                )
            else:
                fail("INVALID_WORKFLOW_COMMAND", 422)
            audit(c, actor, "workflow.command", wid, body.action)
            return get(c, "workflow_run", wid)

    @app.get("/v2/operations")
    def operations(actor=Depends(identity)):
        with db.transaction(actor["tenant_id"], actor["subject"]) as c:
            return {
                "jobs": c.execute(
                    "SELECT id,kind,resource_id,state,attempt,max_attempts,lease_until,next_run_at,error FROM execution_job ORDER BY created_at DESC LIMIT 100"
                ).fetchall(),
                "outbox": c.execute(
                    "SELECT event_type,count(*) AS pending FROM outbox_event WHERE delivered_at IS NULL GROUP BY event_type"
                ).fetchall(),
                "waits": c.execute(
                    "SELECT gate,count(*) AS count FROM workflow_wait WHERE state='WAITING' GROUP BY gate"
                ).fetchall(),
            }

    @app.post("/v2/jobs/{jid}/retry")
    def retry(jid: UUID, actor=Depends(identity)):
        authorize(actor)
        with db.transaction(actor["tenant_id"], actor["subject"]) as c:
            job = get(c, "execution_job", jid, True)
            if job["state"] not in {"MANUAL", "BLOCKED"}:
                fail("JOB_NOT_MANUAL")
            c.execute(
                "UPDATE execution_job SET state='READY',attempt=0,error=NULL,next_run_at=clock_timestamp() WHERE id=%s",
                (jid,),
            )
            audit(
                c,
                actor,
                "job.retry.budget.renewed",
                jid,
                "Explicit new retry budget; original logical key and fencing history retained",
            )
            return {"id": jid, "state": "READY"}

    @app.post("/v2/events/{eid}/retry")
    def retry_event(eid: UUID, actor=Depends(identity)):
        authorize(actor, {"tenant_admin"})
        with db.transaction(actor["tenant_id"], actor["subject"]) as c:
            event = get(c, "outbox_event", eid, True)
            if event["delivered_at"] is None:
                c.execute(
                    "UPDATE outbox_event SET attempt=0,next_run_at=clock_timestamp(),error=NULL WHERE id=%s",
                    (eid,),
                )
                audit(
                    c,
                    actor,
                    "event.redelivery.requested",
                    eid,
                    "Same event id and payload retained",
                )
            return {"id": eid, "already_delivered": event["delivered_at"] is not None}
