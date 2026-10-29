import hashlib
import json
from contextvars import ContextVar
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from services.core.auth import require_role

WRITE = {"operator", "tenant_admin"}
REVIEW = {"reviewer", "tenant_admin"}
REQUEST_TRACE = ContextVar("request_trace", default=None)


def canonical(data):
    return json.dumps(
        data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def digest(data):
    return hashlib.sha256(canonical(data).encode()).hexdigest()


def fail(code, status=409):
    raise HTTPException(status, code)


def get(conn, table, resource_id, lock=False):
    # Table names are exclusively module-controlled constants, never request input.
    from psycopg import sql

    row = conn.execute(
        sql.SQL("SELECT * FROM {} WHERE id=%s" + (" FOR UPDATE" if lock else "")).format(
            sql.Identifier(table)
        ),
        (resource_id,),
    ).fetchone()
    if row is None:
        fail("RESOURCE_NOT_FOUND", 404)
    return row


def check_version(row, version):
    if row["version"] != version:
        fail("VERSION_CONFLICT")


def audit(conn, actor, action, resource_id, reason):
    conn.execute(
        "INSERT INTO audit_event(id,tenant_id,actor,action,resource_id,trace_id,reason) VALUES(%s,%s,%s,%s,%s,%s,%s)",
        (
            uuid4(),
            actor["tenant_id"],
            actor["subject"],
            action,
            resource_id,
            REQUEST_TRACE.get() or uuid4(),
            reason,
        ),
    )


def authorize(actor, roles=WRITE):
    require_role(actor["role"], roles)


def now():
    return datetime.now(UTC)


def business_now():
    import os

    if os.environ.get("DEMO_CLOCK_ENABLED", "false").lower() == "true":
        if os.environ.get("APP_ENV") != "development":
            fail("DEMO_CLOCK_FORBIDDEN", 503)
        return datetime(2027, 1, 1, 2, tzinfo=UTC)
    return now()


def j(data):
    return Jsonb(data)
