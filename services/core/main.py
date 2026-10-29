import json
import logging
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from psycopg import Error as DatabaseError
from psycopg_pool import PoolTimeout
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from services.core.auth import ROLES, decode_token, issue_token, require_role
from services.core.config import Settings
from services.core.db import Database
from services.core.g1.common import REQUEST_TRACE
from services.core.version import SCHEMA_VERSION

logger = logging.getLogger("hotel")
bearer = HTTPBearer(auto_error=False)


def create_app(settings=None):
    from services.core.g5.logging import configure

    configure("api")
    settings = settings or Settings.load()
    db = Database(settings.database_url)

    @asynccontextmanager
    async def lifespan(app):
        db.pool.open()
        yield
        db.pool.close()

    app = FastAPI(title="Hotel Project — G5", version="0.6.0", lifespan=lifespan)
    app.state.db = db
    app.state.settings = settings

    @app.middleware("http")
    async def tracing(request, call_next):
        started = time.monotonic()
        request.state.trace_id = str(uuid4())
        trace_token = REQUEST_TRACE.set(request.state.trace_id)
        try:
            response = await call_next(request)
        except Exception as exc:
            logger.error(
                json.dumps(
                    {
                        "event": "request_failed",
                        "trace_id": request.state.trace_id,
                        "timestamp": datetime.now(UTC).isoformat(),
                        "error_type": type(exc).__name__,
                    }
                )
            )
            response = JSONResponse(
                status_code=500,
                content={
                    "code": "INTERNAL_ERROR",
                    "message": "Internal error",
                    "trace_id": request.state.trace_id,
                    "details": [],
                },
            )
        response.headers["X-Trace-ID"] = request.state.trace_id
        logger.info(
            json.dumps(
                {
                    "event": "request",
                    "tenant_id": getattr(request.state, "tenant_id", None),
                    "timestamp": datetime.now(UTC).isoformat(),
                    "service": "api",
                    "duration_seconds": time.monotonic() - started,
                    "db_pool_waiting": db.pool.get_stats().get("requests_waiting"),
                    "db_pool_size": db.pool.get_stats().get("pool_size"),
                    "method": request.method,
                    "status": response.status_code,
                    "trace_id": request.state.trace_id,
                }
            )
        )
        REQUEST_TRACE.reset(trace_token)
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": str(exc.detail),
                "message": str(exc.detail),
                "trace_id": request.state.trace_id,
                "details": [],
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse(
            status_code=422,
            content={
                "code": "INVALID_REQUEST",
                "message": "Invalid request",
                "trace_id": request.state.trace_id,
                "details": [{"location": list(e["loc"]), "type": e["type"]} for e in exc.errors()],
            },
        )

    def subject(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if credentials is None:
            raise HTTPException(401, "AUTHENTICATION_REQUIRED")
        return decode_token(credentials.credentials, settings.jwt_secret)

    def identity(
        request: Request, sub=Depends(subject), x_tenant_id: UUID | None = Header(default=None)
    ):
        with db.transaction(subject=sub) as conn:
            memberships = conn.execute(
                "SELECT tenant_id, role FROM membership WHERE status='active' ORDER BY tenant_id"
            ).fetchall()
        if not memberships:
            raise HTTPException(403, "NO_ACTIVE_MEMBERSHIP")
        tenant = x_tenant_id or memberships[0]["tenant_id"]
        selected = next((m for m in memberships if m["tenant_id"] == tenant), None)
        if not selected:
            raise HTTPException(403, "TENANT_FORBIDDEN")
        with db.transaction(tenant, sub) as conn:
            if not conn.execute("SELECT 1 FROM tenant WHERE id=%s", (tenant,)).fetchone():
                raise HTTPException(403, "TENANT_DISABLED")
        require_role(selected["role"], ROLES)
        request.state.tenant_id = str(tenant)
        return {
            "subject": sub,
            "tenant_id": tenant,
            "role": selected["role"],
            "memberships": memberships,
        }

    @app.get("/health/live")
    def live():
        return {"status": "alive"}

    @app.get("/health/ready")
    def ready():
        try:
            with db.transaction() as conn:
                revision = conn.execute("SELECT version FROM schema_revision").fetchone()
                if revision is None or revision["version"] != SCHEMA_VERSION:
                    raise HTTPException(503, "MIGRATIONS_NOT_READY")
        except (DatabaseError, PoolTimeout):
            raise HTTPException(503, "DATABASE_NOT_READY") from None
        return {"status": "ready", "migration": SCHEMA_VERSION}

    @app.get("/v1/me")
    def me(actor=Depends(identity)):
        return actor

    @app.get("/v1/suppliers")
    def suppliers(limit: int = 20, offset: int = 0, actor=Depends(identity)):
        if limit < 1 or limit > 100 or offset < 0:
            raise HTTPException(422, "INVALID_PAGINATION")
        with db.transaction(actor["tenant_id"], actor["subject"]) as conn:
            rows = conn.execute(
                "SELECT id, code, name, contract_version FROM supplier "
                "WHERE tenant_id=%s ORDER BY code LIMIT %s OFFSET %s",
                (actor["tenant_id"], limit, offset),
            ).fetchall()
        return {"items": rows, "limit": limit, "offset": offset}

    @app.get("/v1/properties/{property_id}")
    def property_detail(property_id: UUID, actor=Depends(identity)):
        with db.transaction(actor["tenant_id"], actor["subject"]) as conn:
            row = conn.execute(
                "SELECT p.id, p.current_version_id, v.name, v.city, v.timezone "
                "FROM property p JOIN property_version v "
                "ON (p.tenant_id,p.id,p.current_version_id)=(v.tenant_id,v.property_id,v.id) "
                "WHERE p.tenant_id=%s AND p.id=%s",
                (actor["tenant_id"], property_id),
            ).fetchone()
        if row is None:
            raise HTTPException(404, "RESOURCE_NOT_FOUND")
        return row

    class Login(BaseModel):
        subject: str = Field(min_length=1, max_length=100)
        password: str = Field(min_length=1, max_length=1024)

    if settings.dev_auth:
        from services.core.g5.login import credential_hash, login_allowed, valid

        # Normal services require a configured PBKDF2 verifier, without plaintext fallback.
        encoded_password = credential_hash()

        @app.post("/dev/token", tags=["development only"])
        def login(body: Login, request: Request):
            import os

            login_allowed(db, request.client.host if request.client else "unknown", body.subject)
            # Isolated historical tests intentionally replace credentials between requests.
            selected = (
                credential_hash()
                if os.environ.get("HOTEL_G1_TEST_MODE") == "true"
                else encoded_password
            )
            if not valid(body.password, selected):
                raise HTTPException(401, "INVALID_CREDENTIALS")
            with db.transaction(subject=body.subject) as conn:
                member = conn.execute(
                    "SELECT 1 FROM membership WHERE status='active' LIMIT 1"
                ).fetchone()
            if not member:
                raise HTTPException(401, "INVALID_CREDENTIALS")
            return {
                "access_token": issue_token(body.subject, settings.jwt_secret),
                "token_type": "bearer",
                "expires_in": 1800,
            }

    from services.core.g1.routes import mount

    mount(app, db, identity)
    from services.core.g2.routes import mount as mount_g2

    mount_g2(app, db, identity)
    from services.core.g3.routes import mount as mount_g3

    mount_g3(app, db, identity)
    from services.core.g4.routes import mount as mount_g4

    mount_g4(app, db, identity)
    from services.core.g5.routes import mount as mount_g5

    mount_g5(app, db, identity)
    return app
