from typing import Literal
from uuid import UUID

from fastapi import Depends, Header, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from services.core.g1 import distribution, ingestion, matching, offers
from services.core.g1.common import authorize, fail, get


class Version(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0)


class MappingDecision(Version):
    target_id: UUID
    decision: Literal["APPROVED", "REJECTED", "NEEDS_INFO"]
    reason: str = Field(min_length=1, max_length=500)


class Decision(Version):
    decision: Literal["APPROVED", "REJECTED"]
    reason: str = Field(min_length=1, max_length=500)


class Activation(BaseModel):
    activation_id: UUID


class Publish(BaseModel):
    snapshot_id: UUID


def mount(app, db, identity):
    @app.middleware("http")
    async def bounded_upload(request, call_next):
        if request.method == "POST" and request.url.path == "/v1/imports":
            chunks = []
            size = 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > 21 * 1024 * 1024:
                    return JSONResponse(
                        status_code=413,
                        content={
                            "code": "UPLOAD_TOO_LARGE",
                            "message": "Maximum package size is 20 MiB",
                            "trace_id": getattr(request.state, "trace_id", "upload-limit"),
                            "details": [],
                        },
                    )
                chunks.append(chunk)
            request._body = b"".join(chunks)
        return await call_next(request)

    @app.get("/v1/config")
    def config(actor=Depends(identity)):
        import os

        return {
            "demo_clock_enabled": os.environ.get("DEMO_CLOCK_ENABLED") == "true",
            "business_time": offers.business_now(),
            "channel": "SIM-A",
            "rule": "markup-10pct-v1",
        }

    @app.post(
        "/v1/imports",
        status_code=201,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "multipart/form-data": {
                        "schema": {
                            "type": "object",
                            "required": ["files"],
                            "properties": {
                                "files": {
                                    "type": "array",
                                    "minItems": 6,
                                    "maxItems": 6,
                                    "items": {"type": "string", "format": "binary"},
                                }
                            },
                        }
                    }
                },
            }
        },
    )
    async def upload(request: Request, actor=Depends(identity)):
        authorize(actor)
        form = await request.form(max_files=6, max_fields=0, max_part_size=5 * 1024 * 1024)
        items = form.multi_items()
        if len(items) != 6:
            fail("EXACTLY_SIX_FILES_REQUIRED", 422)
        contents = {}
        total = 0
        for _, file in items:
            if (
                not hasattr(file, "filename")
                or file.filename in contents
                or file.filename not in {"manifest.json", *[f"{k}.csv" for k in ingestion.REQUIRED]}
            ):
                fail("INVALID_FILE_NAME", 422)
            chunks = []
            size = 0
            while chunk := await file.read(65536):
                size += len(chunk)
                total += len(chunk)
                if size > 5 * 1024 * 1024 or total > 20 * 1024 * 1024:
                    fail("UPLOAD_TOO_LARGE", 413)
                chunks.append(chunk)
            contents[file.filename] = b"".join(chunks)
        result = None
        try:
            with db.transaction(actor["tenant_id"], actor["subject"]) as conn:
                result = ingestion.upload(conn, actor, contents)
            return result
        except Exception:
            if result and not result["reused"]:
                import shutil

                shutil.rmtree(ingestion.batch_dir(result["id"]), ignore_errors=True)
            raise

    @app.get("/v1/imports")
    def imports(actor=Depends(identity)):
        with db.transaction(actor["tenant_id"], actor["subject"]) as c:
            return {
                "items": c.execute(
                    "SELECT id,status,version,created_at,manifest FROM import_batch WHERE manifest->>'schema_version'='1.0' AND created_by!='system.g2-projection' ORDER BY created_at DESC LIMIT 100"
                ).fetchall()
            }

    @app.get("/v1/imports/{bid}")
    def preview(bid: UUID, actor=Depends(identity)):
        with db.transaction(actor["tenant_id"], actor["subject"]) as c:
            result = ingestion.preview(c, bid)
            activation = c.execute(
                "SELECT id FROM source_activation WHERE batch_id=%s", (bid,)
            ).fetchone()
            result["activation_id"] = activation["id"] if activation else None
            return result

    @app.get("/v1/imports/{bid}/issues")
    def issues(bid: UUID, actor=Depends(identity)):
        with db.transaction(actor["tenant_id"], actor["subject"]) as c:
            return {"items": get(c, "import_batch", bid)["issues"]}

    @app.post("/v1/imports/{bid}/confirm")
    def confirm(bid: UUID, body: Version, actor=Depends(identity)):
        with db.transaction(actor["tenant_id"], actor["subject"]) as c:
            return ingestion.confirm(c, actor, bid, body.expected_version)

    @app.post("/v1/imports/{bid}/activate")
    def activate(bid: UUID, body: Version, actor=Depends(identity)):
        with db.transaction(actor["tenant_id"], actor["subject"]) as c:
            return ingestion.activate(c, actor, bid, body.expected_version)

    @app.get("/v1/artifacts/{aid}/download")
    def download(aid: UUID, actor=Depends(identity)):
        with db.transaction(actor["tenant_id"], actor["subject"]) as c:
            artifact = get(c, "artifact", aid)
        path = (ingestion.STORE / artifact["storage_key"]).resolve()
        if not path.is_relative_to(ingestion.STORE.resolve()) or not path.is_file():
            fail("ARTIFACT_UNAVAILABLE", 404)
        return FileResponse(path, filename=artifact["name"], media_type="application/octet-stream")

    @app.get("/v1/match-proposals")
    def proposals(activation_id: UUID, actor=Depends(identity)):
        with db.transaction(actor["tenant_id"], actor["subject"]) as c:
            return {"items": matching.proposals(c, activation_id)}

    @app.post("/v1/mappings/{kind}/{source_id}/decisions")
    def map_decision(kind: str, source_id: UUID, body: MappingDecision, actor=Depends(identity)):
        with db.transaction(actor["tenant_id"], actor["subject"]) as c:
            return matching.decide(c, actor, kind, source_id, body)

    @app.post("/v1/offers/compile")
    def compile_offer(body: Activation, actor=Depends(identity)):
        with db.transaction(actor["tenant_id"], actor["subject"]) as c:
            return offers.compile_offer(c, actor, body.activation_id)

    @app.get("/v1/snapshots")
    def snapshots(actor=Depends(identity)):
        with db.transaction(actor["tenant_id"], actor["subject"]) as c:
            return {
                "items": c.execute(
                    "SELECT id,status,version,revision,payload_hash,expires_at FROM offer_snapshot ORDER BY created_at DESC LIMIT 100"
                ).fetchall()
            }

    @app.get("/v1/snapshots/{sid}")
    def snapshot(sid: UUID, actor=Depends(identity)):
        with db.transaction(actor["tenant_id"], actor["subject"]) as c:
            return get(c, "offer_snapshot", sid)

    @app.post("/v1/snapshots/{sid}/submit")
    def submit(sid: UUID, body: Version, actor=Depends(identity)):
        with db.transaction(actor["tenant_id"], actor["subject"]) as c:
            return offers.submit(c, actor, sid, body.expected_version)

    @app.post("/v1/snapshots/{sid}/decisions")
    def decide(sid: UUID, body: Decision, actor=Depends(identity)):
        with db.transaction(actor["tenant_id"], actor["subject"]) as c:
            return offers.decide(c, actor, sid, body)

    @app.post("/v1/publish-plans", status_code=202)
    def publish(
        body: Publish,
        idempotency_key: str = Header(min_length=1, max_length=200),
        actor=Depends(identity),
    ):
        with db.transaction(actor["tenant_id"], actor["subject"]) as c:
            return distribution.create_task(c, actor, body.snapshot_id, idempotency_key)

    @app.get("/v1/publish-tasks")
    def tasks(actor=Depends(identity)):
        with db.transaction(actor["tenant_id"], actor["subject"]) as c:
            return {
                "items": c.execute(
                    "SELECT * FROM publish_task ORDER BY created_at DESC LIMIT 100"
                ).fetchall()
            }

    @app.get("/v1/publish-tasks/{tid}")
    def task(tid: UUID, actor=Depends(identity)):
        with db.transaction(actor["tenant_id"], actor["subject"]) as c:
            return distribution.task_detail(c, tid)

    @app.post("/v1/publish-tasks/{tid}/query")
    def query(tid: UUID, actor=Depends(identity)):
        authorize(actor)
        return distribution.query_remote(db, actor["tenant_id"], tid, actor["subject"])

    @app.get("/v1/snapshots/{sid}/provenance")
    def provenance(sid: UUID, actor=Depends(identity)):
        with db.transaction(actor["tenant_id"], actor["subject"]) as c:
            return offers.provenance(c, sid)

    @app.get("/")
    def index():
        return RedirectResponse("/admin/")

    dist = ingestion.ROOT / "apps/admin/dist"
    if dist.exists():
        app.mount("/admin", StaticFiles(directory=dist, html=True), name="admin")
