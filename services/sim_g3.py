"""Independent channel-side protocol. Core sees this state only through HTTP."""

import asyncio
import hmac
import json
import logging
import os
from contextlib import contextmanager
from uuid import UUID, uuid4

from fastapi import Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from services.core.g1.common import digest, j
from services.core.g3.contract import PROFILES, cell_key, normalize_content, validate_cell


class Wire(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(gt=0, strict=True)
    action: str
    manifest_hash: str = Field(pattern="^[0-9a-f]{64}$")
    chunk_index: int = Field(ge=0)
    content: dict | None = None
    manifest: dict | None = None
    cells: list[dict] = Field(default_factory=list)


def mount(app, db, channel):
    from services.core.g5.logging import configure

    configure("sim-" + channel.lower())

    @app.middleware("http")
    async def correlation_log(request, call_next):
        supplied = request.headers.get("X-Correlation-ID", "")
        try:
            correlation = str(UUID(supplied))
        except ValueError:
            correlation = str(uuid4())
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation
        logging.getLogger("hotel.sim").info(
            json.dumps(
                {
                    "event": "channel.http",
                    "channel": channel,
                    "correlation_id": correlation,
                    "method": request.method,
                    "status": response.status_code,
                }
            )
        )
        return response

    profile = PROFILES[channel]

    def account(authorization: str = Header(default="")):
        for label in ["A", "B"]:
            name = f"G3_{channel.replace('-', '_')}_TENANT_{label}_TOKEN"
            token = os.environ.get(name, "")
            if token and hmac.compare_digest(authorization, "Bearer " + token):
                return label
        raise HTTPException(401, "INVALID_CHANNEL_CREDENTIAL")

    @contextmanager
    def tx(acc):
        with db.transaction() as c:
            c.execute("SELECT set_config('sim.account',%s,true)", (acc,))
            yield c

    def control(token):
        if os.environ.get("G3_FAULTS_ENABLED") != "true":
            raise HTTPException(404, "NOT_FOUND")
        expected = os.environ.get("G3_CONTROL_TOKEN", "")
        if not expected or not hmac.compare_digest(token, expected):
            raise HTTPException(403, "CONTROL_FORBIDDEN")

    def fault(c, acc, key, consume=False):
        f = c.execute(
            "SELECT * FROM g3_remote_fault WHERE account_id=%s AND listing_key=%s FOR UPDATE",
            (acc, key),
        ).fetchone()
        if f and f["remaining"] > 0:
            if consume:
                c.execute(
                    "UPDATE g3_remote_fault SET remaining=remaining-1 WHERE account_id=%s AND listing_key=%s",
                    (acc, key),
                )
            return f
        return None

    def apply(c, op, mode=None):
        b = op["body"]
        acc = op["account_id"]
        key = op["listing_key"]
        c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (acc + key,))
        row = c.execute(
            "SELECT * FROM g3_remote_listing WHERE account_id=%s AND listing_key=%s FOR UPDATE",
            (acc, key),
        ).fetchone()
        if row and b["revision"] < row["revision"]:
            result = {
                "status": "REJECTED",
                "code": "STALE_REVISION",
                "revision": row["revision"],
                "cells": [],
            }
        else:
            if not row:
                c.execute(
                    "INSERT INTO g3_remote_listing(account_id,listing_key) VALUES(%s,%s)",
                    (acc, key),
                )
                row = {"content": {}, "cells": {}, "revision": 0, "manifest": {}}
            cells = dict(row["cells"])
            content = row["content"]
            results = []
            manifest = row["manifest"]
            if b["action"] == "CONTENT":
                if (
                    digest(b["manifest"]) != b["manifest_hash"]
                    or digest(b["content"]) != b["manifest"]["content_hash"]
                ):
                    raise HTTPException(422, "MANIFEST_HASH")
                if (
                    b["revision"] == row["revision"]
                    and manifest
                    and digest(manifest) != b["manifest_hash"]
                ):
                    raise HTTPException(409, "REVISION_MANIFEST_CONFLICT")
                content = b["content"]
                manifest = b["manifest"]
            else:
                if not content:
                    raise HTTPException(409, "CONTENT_REQUIRED")
                if b["revision"] != row["revision"] or digest(manifest) != b["manifest_hash"]:
                    raise HTTPException(409, "CONTENT_REVISION_REQUIRED")
                if manifest["chunks"].get(str(b["chunk_index"])) != digest(b["cells"]):
                    raise HTTPException(422, "CHUNK_HASH")
            for i, cell in enumerate(b["cells"]):
                scope = content["scope"]
                if not scope["stay_date_from"] <= cell["date"] < scope["stay_date_to_exclusive"]:
                    raise HTTPException(422, "CELL_OUTSIDE_SCOPE")
                allowed = (
                    content["pools"]
                    if cell["kind"] == "pool"
                    else [p["key"] for p in content["plans"]]
                )
                if cell["key"] not in allowed:
                    raise HTTPException(422, "CELL_REFERENCE")
                ck = cell_key(cell)
                if mode in {"partial_temp", "partial_permanent"} and i == 0:
                    results.append(
                        {
                            "key": ck,
                            "state": "REJECTED",
                            "code": "TEMPORARY_CELL"
                            if mode == "partial_temp"
                            else "CELL_POLICY_REJECTED",
                        }
                    )
                else:
                    cells[ck] = {"value": cell, "revision": b["revision"]}
                    results.append({"key": ck, "state": "APPLIED", "code": None})
            c.execute(
                "UPDATE g3_remote_listing SET revision=%s,content=%s,cells=%s,manifest=%s WHERE account_id=%s AND listing_key=%s",
                (b["revision"], j(content), j(cells), j(manifest), acc, key),
            )
            result = {
                "status": "PARTIAL"
                if any(r["state"] == "REJECTED" for r in results)
                else "APPLIED",
                "revision": b["revision"],
                "cells": results,
                "manifest_hash": b["manifest_hash"],
            }
        c.execute(
            "UPDATE g3_remote_operation SET state='DONE',result=%s WHERE id=%s",
            (j(result), op["id"]),
        )
        return result

    @app.get("/g3/health")
    def health():
        with db.transaction() as c:
            c.execute("SELECT id FROM g3_remote_operation LIMIT 0")
            c.execute("SELECT account_id FROM g3_remote_quota LIMIT 0")
        return {"channel": channel, "capability": profile["capability"], "simulation": True}

    @app.put("/g3/listings/{key}")
    async def write(
        key: str,
        body: Wire,
        idempotency_key: str = Header(min_length=1, max_length=200),
        acc=Depends(account),
    ):
        b = body.model_dump()
        hash_ = digest(b)
        if body.action not in {"CONTENT", "PUBLISH", "REPAIR", "STOP"}:
            raise HTTPException(422, "ACTION")
        if len(body.cells) > profile["limit"]:
            raise HTTPException(422, "CELL_LIMIT")
        try:
            if body.action == "CONTENT":
                if body.cells or not body.content or not body.manifest:
                    raise ValueError()
                normalize_content(body.content, channel)
            elif body.content is not None:
                raise ValueError()
            for cell in body.cells:
                validate_cell(cell)
                if body.action == "STOP" and (
                    cell["sellable"] or (cell["kind"] == "pool" and cell["quantity"] != 0)
                ):
                    raise ValueError()
            if len({cell_key(c) for c in body.cells}) != len(body.cells):
                raise ValueError()
        except (ValueError, KeyError, TypeError, ArithmeticError):
            raise HTTPException(422, "WIRE_SCHEMA") from None
        with tx(acc) as c:
            c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (acc + key,))
            old = c.execute(
                "SELECT * FROM g3_remote_operation WHERE account_id=%s AND request_key=%s",
                (acc, idempotency_key),
            ).fetchone()
            if old:
                if old["request_hash"] != hash_:
                    raise HTTPException(409, "IDEMPOTENCY_CONFLICT")
                return {
                    "operation_id": str(old["id"]),
                    "state": old["state"],
                    "result": old["result"],
                }
            f = fault(c, acc, key, True)
            mode = f["mode"] if f else None
        if mode not in {"hold", "429", "500", "auth"}:
            from decimal import Decimal

            with tx(acc) as c:
                c.execute(
                    "INSERT INTO g3_remote_quota VALUES(%s,%s,clock_timestamp()) ON CONFLICT DO NOTHING",
                    (acc, profile["rate"]),
                )
                bucket = c.execute(
                    "SELECT *,clock_timestamp() AS clock FROM g3_remote_quota WHERE account_id=%s FOR UPDATE",
                    (acc,),
                ).fetchone()
                tokens = min(
                    Decimal(profile["rate"]),
                    bucket["tokens"]
                    + Decimal(str((bucket["clock"] - bucket["updated_at"]).total_seconds()))
                    * profile["rate"],
                )
                allowed = tokens >= 1
                c.execute(
                    "UPDATE g3_remote_quota SET tokens=%s,updated_at=%s WHERE account_id=%s",
                    (max(0, tokens - 1) if allowed else tokens, bucket["clock"], acc),
                )
            if not allowed:
                return JSONResponse(
                    {"code": "RATE_LIMITED", "accepted": False}, 429, headers={"Retry-After": "1"}
                )
        if mode == "429":
            return JSONResponse(
                {"code": "RATE_LIMITED", "accepted": False},
                429,
                headers={"Retry-After": f["arg"] or "1"},
            )
        if mode == "500":
            return JSONResponse({"code": "UNAVAILABLE", "accepted": False}, 500)
        if mode == "auth":
            raise HTTPException(401, "ACCOUNT_DISABLED")
        if mode == "hold":
            # Actual delayed execution, not merely a delayed response. Never hold a DB transaction.
            for _ in range(300):
                with tx(acc) as c:
                    row = c.execute(
                        "SELECT released FROM g3_remote_fault WHERE account_id=%s AND listing_key=%s",
                        (acc, key),
                    ).fetchone()
                if row and row["released"]:
                    break
                await asyncio.sleep(0.05)
            else:
                raise HTTPException(503, "BARRIER_TIMEOUT")
        with tx(acc) as c:
            c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (acc + key,))
            old = c.execute(
                "SELECT * FROM g3_remote_operation WHERE account_id=%s AND request_key=%s",
                (acc, idempotency_key),
            ).fetchone()
            if old:
                if old["request_hash"] != hash_:
                    raise HTTPException(409, "IDEMPOTENCY_CONFLICT")
                return {
                    "operation_id": str(old["id"]),
                    "state": old["state"],
                    "result": old["result"],
                }
            asynchronous = channel == "SIM-B" and body.action != "CONTENT"
            op = c.execute(
                "INSERT INTO g3_remote_operation(id,account_id,request_key,listing_key,request_hash,body,state,ready_at) VALUES(%s,%s,%s,%s,%s,%s,%s,clock_timestamp()+interval '0.15 second') RETURNING *",
                (
                    uuid4(),
                    acc,
                    idempotency_key,
                    key,
                    hash_,
                    j(b),
                    "PROCESSING" if asynchronous else "READY",
                ),
            ).fetchone()
            if asynchronous and mode in {"partial_temp", "partial_permanent"}:
                c.execute(
                    "UPDATE g3_remote_operation SET result=%s WHERE id=%s",
                    (j({"fault": mode}), op["id"]),
                )
            result = {} if asynchronous else apply(c, op, mode)
        if mode in {"after_commit", "lost_operation"}:
            await asyncio.sleep(1.5)
        return JSONResponse(
            {
                "operation_id": str(op["id"]),
                "state": "PROCESSING" if asynchronous else "DONE",
                "result": result,
            },
            202 if asynchronous else 200,
        )

    @app.get("/g3/operations/{key}")
    def query(key: str, acc=Depends(account)):
        with tx(acc) as c:
            op = c.execute(
                "SELECT * FROM g3_remote_operation WHERE account_id=%s AND request_key=%s FOR UPDATE",
                (acc, key),
            ).fetchone()
            if not op:
                raise HTTPException(404, "OPERATION_NOT_FOUND")
            f = fault(c, acc, op["listing_key"])
            if f and f["mode"] == "read_failure":
                raise HTTPException(503, "READ_UNAVAILABLE")
            if (
                op["state"] == "PROCESSING"
                and c.execute("SELECT clock_timestamp()>%s AS due", (op["ready_at"],)).fetchone()[
                    "due"
                ]
            ):
                result = apply(c, op, op["result"].get("fault"))
                op = {**op, "state": "DONE", "result": result}
            return {"operation_id": str(op["id"]), "state": op["state"], "result": op["result"]}

    @app.get("/g3/listings/{key}")
    def read(key: str, acc=Depends(account)):
        with tx(acc) as c:
            f = fault(c, acc, key)
            if f and f["mode"] in {"read_failure", "auth"}:
                raise HTTPException(503 if f["mode"] == "read_failure" else 401, "READ_UNAVAILABLE")
            row = c.execute(
                "SELECT * FROM g3_remote_listing WHERE account_id=%s AND listing_key=%s", (acc, key)
            ).fetchone()
            if not row:
                raise HTTPException(404, "LISTING_NOT_FOUND")
            return {
                "revision": row["revision"],
                "content": row["content"],
                "cells": row["cells"],
                "complete": not (f and f["mode"] == "incomplete"),
                "simulation": True,
            }

    @app.post("/g3/control/{key}")
    def inject(
        key: str, body: dict, x_control_token: str = Header(default=""), acc=Depends(account)
    ):
        control(x_control_token)
        mode = body.get("mode", "clear")
        allowed = {
            "clear",
            "429",
            "500",
            "auth",
            "hold",
            "release",
            "after_commit",
            "lost_operation",
            "partial_temp",
            "partial_permanent",
            "read_failure",
            "incomplete",
            "drift",
            "missing",
            "remote_ahead",
        }
        if mode not in allowed:
            raise HTTPException(422, "FAULT_MODE")
        with tx(acc) as c:
            c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (acc + key,))
            if mode in {"drift", "missing", "remote_ahead"}:
                row = c.execute(
                    "SELECT * FROM g3_remote_listing WHERE account_id=%s AND listing_key=%s FOR UPDATE",
                    (acc, key),
                ).fetchone()
                if not row:
                    raise HTTPException(404, "LISTING_NOT_FOUND")
                cells = dict(row["cells"])
                ck = body.get("cell_key") or next(
                    (k for k, v in cells.items() if v["value"]["kind"] == "rate"),
                    next(iter(cells), None),
                )
                if mode == "missing":
                    cells = {}
                elif mode == "drift" and ck in cells:
                    value = {
                        **cells[ck]["value"],
                        **body.get(
                            "value",
                            {"sell_amount": "111.00"}
                            if cells[ck]["value"]["kind"] == "rate"
                            else {"quantity": 9},
                        ),
                    }
                    validate_cell(value)
                    cells[ck] = {**cells[ck], "value": value}
                c.execute(
                    "UPDATE g3_remote_listing SET cells=%s,revision=revision+%s WHERE account_id=%s AND listing_key=%s",
                    (j(cells), 100 if mode == "remote_ahead" else 0, acc, key),
                )
            elif mode == "release":
                c.execute(
                    "UPDATE g3_remote_fault SET released=true WHERE account_id=%s AND listing_key=%s",
                    (acc, key),
                )
            else:
                c.execute(
                    "INSERT INTO g3_remote_fault VALUES(%s,%s,%s,%s,%s,false) ON CONFLICT(account_id,listing_key) DO UPDATE SET mode=excluded.mode,arg=excluded.arg,remaining=excluded.remaining,released=false",
                    (acc, key, mode, str(body.get("arg", "")), int(body.get("remaining", 1))),
                )
            return {"mode": mode, "simulation": True}

    @app.get("/g3/control/{key}")
    def control_status(key: str, x_control_token: str = Header(default=""), acc=Depends(account)):
        control(x_control_token)
        with tx(acc) as c:
            row = c.execute(
                "SELECT mode,remaining,released FROM g3_remote_fault WHERE account_id=%s AND listing_key=%s",
                (acc, key),
            ).fetchone()
            return dict(row) if row else {"mode": "none"}
