import asyncio
import hmac
import os
from contextlib import asynccontextmanager, contextmanager
from datetime import date
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from services.core.config import Settings
from services.core.db import Database
from services.core.g1.common import digest


class Write(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(gt=0, strict=True)
    payload_hash: str = Field(pattern="^[0-9a-f]{64}$")
    payload: dict


def validate_payload(payload, key):
    try:
        if (
            set(payload) != {"capability", "hotel_key", "scope", "pools", "plans"}
            or payload["capability"] != "sim-a-v1"
            or payload["hotel_key"] != key
        ):
            raise ValueError()
        scope = payload["scope"]
        start, end = (
            date.fromisoformat(scope["stay_date_from"]),
            date.fromisoformat(scope["stay_date_to_exclusive"]),
        )
        from services.core.domain import money, stay_dates

        expected = {d.isoformat() for d in stay_dates(start, end)}
        if not 1 <= len(expected) <= 366:
            raise ValueError()
        poolkeys = set()
        for pool in payload["pools"]:
            if set(pool) != {"key", "calendar"} or any(
                set(d) != {"date", "quantity"} for d in pool["calendar"]
            ):
                raise ValueError()
            if pool["key"] in poolkeys:
                raise ValueError()
            poolkeys.add(pool["key"])
            if (
                len(pool["calendar"]) != len(expected)
                or {d["date"] for d in pool["calendar"]} != expected
            ):
                raise ValueError()
            if any(type(d["quantity"]) is not int or d["quantity"] < 0 for d in pool["calendar"]):
                raise ValueError()
        plankeys = set()
        if not payload["plans"] or not poolkeys:
            raise ValueError()
        for plan in payload["plans"]:
            if (
                set(plan)
                != {
                    "key",
                    "room_key",
                    "pool_key",
                    "adults",
                    "currency",
                    "meal",
                    "payment",
                    "tax_basis",
                    "cancellation",
                    "calendar",
                }
                or type(plan["adults"]) is not int
            ):
                raise ValueError()
            if (
                plan["key"] in plankeys
                or plan["pool_key"] not in poolkeys
                or plan["adults"] != 2
                or plan["currency"] != "USD"
            ):
                raise ValueError()
            plankeys.add(plan["key"])
            if (
                plan["meal"] not in {"BREAKFAST_2", "NONE"}
                or plan["payment"] not in {"AT_PROPERTY", "PREPAID"}
                or plan["tax_basis"] not in {"NET_EXCLUSIVE", "GROSS_INCLUSIVE"}
            ):
                raise ValueError()
            policy = plan["cancellation"]
            if set(policy) != {"kind", "deadline_hours", "penalty_basis"}:
                raise ValueError()
            if (
                policy["kind"] not in {"FLEX", "NONREF"}
                or type(policy["deadline_hours"]) is not int
                or policy["deadline_hours"] < 0
                or policy["penalty_basis"] != "stay_total"
            ):
                raise ValueError()
            if (
                len(plan["calendar"]) != len(expected)
                or {r["date"] for r in plan["calendar"]} != expected
            ):
                raise ValueError()
            for r in plan["calendar"]:
                if (
                    set(r) != {"date", "net_amount", "sell_amount"}
                    or not isinstance(r["net_amount"], str)
                    or not isinstance(r["sell_amount"], str)
                ):
                    raise ValueError()
                money(r["net_amount"])
                amount = money(r["sell_amount"])
                if amount.as_tuple().exponent != -2:
                    raise ValueError()
    except (ValueError, KeyError, TypeError, ArithmeticError):
        raise HTTPException(422, "CAPABILITY_VALIDATION_FAILED") from None


def create_app():
    Settings.load()
    db = Database(os.environ["SIM_DATABASE_URL"])

    @asynccontextmanager
    async def lifespan(app):
        db.pool.open()
        yield
        db.pool.close()

    app = FastAPI(title="SIM-A independent channel", version="1.0", lifespan=lifespan)

    def account(authorization: str = Header(default="")):
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "INVALID_CHANNEL_CREDENTIAL")
        token = authorization.removeprefix("Bearer ")
        for label, key in [("A", "SIM_A_TOKEN"), ("B", "SIM_B_TOKEN")]:
            configured = os.environ.get(key, "")
            if configured and hmac.compare_digest(token, configured):
                return label
        raise HTTPException(401, "INVALID_CHANNEL_CREDENTIAL")

    @contextmanager
    def tx(account_id):
        with db.transaction() as c:
            c.execute("SELECT set_config('sim.account',%s,true)", (account_id,))
            yield c

    @app.get("/health/ready")
    def ready():
        with db.transaction() as c:
            assert c.execute("SELECT version FROM sim_revision").fetchone()["version"] == "001"
        return {"status": "ready", "migration": "001"}

    @app.put("/v1/listings/{key}")
    async def put(
        key: str,
        body: Write,
        idempotency_key: str = Header(min_length=1, max_length=200),
        x_sim_delay: bool = Header(default=False),
        acc=Depends(account),
    ):
        validate_payload(body.payload, key)
        if digest(body.payload) != body.payload_hash:
            raise HTTPException(422, "PAYLOAD_HASH_MISMATCH")
        request_hash = digest(body.model_dump())
        with tx(acc) as c:
            c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (acc + key,))
            old = c.execute(
                "SELECT * FROM sim_operation WHERE account_id=%s AND idempotency_key=%s",
                (acc, idempotency_key),
            ).fetchone()
            if old:
                if old["request_hash"] != request_hash:
                    raise HTTPException(409, "IDEMPOTENCY_CONFLICT")
                return old
            listing = c.execute(
                "SELECT * FROM sim_listing WHERE account_id=%s AND listing_key=%s", (acc, key)
            ).fetchone()
            if listing and (
                body.revision < listing["revision"]
                or (
                    body.revision == listing["revision"]
                    and body.payload_hash != listing["payload_hash"]
                )
            ):
                raise HTTPException(409, "REVISION_CONFLICT")
            c.execute(
                "INSERT INTO sim_listing VALUES(%s,%s,%s,%s,%s) ON CONFLICT(account_id,listing_key) DO UPDATE SET revision=excluded.revision,payload_hash=excluded.payload_hash,payload=excluded.payload",
                (acc, key, body.revision, body.payload_hash, Jsonb(body.payload)),
            )
            operation = {
                "id": uuid4(),
                "account_id": acc,
                "idempotency_key": idempotency_key,
                "request_hash": request_hash,
                "listing_key": key,
                "revision": body.revision,
                "payload_hash": body.payload_hash,
            }
            c.execute(
                "INSERT INTO sim_operation VALUES(%s,%s,%s,%s,%s,%s,%s)", tuple(operation.values())
            )
        if x_sim_delay and os.environ.get("SIM_FAULTS_ENABLED") == "true":
            await asyncio.sleep(2)
        return operation

    @app.get("/v1/operations/{key}")
    def operation(key: str, acc=Depends(account)):
        with tx(acc) as c:
            row = c.execute(
                "SELECT * FROM sim_operation WHERE account_id=%s AND idempotency_key=%s", (acc, key)
            ).fetchone()
        if not row:
            raise HTTPException(404, "OPERATION_NOT_FOUND")
        return row

    @app.get("/v1/listings/{key}")
    def listing(key: str, acc=Depends(account)):
        with tx(acc) as c:
            row = c.execute(
                "SELECT * FROM sim_listing WHERE account_id=%s AND listing_key=%s", (acc, key)
            ).fetchone()
        if not row:
            raise HTTPException(404, "LISTING_NOT_FOUND")
        return row

    from services.sim_g3 import mount

    mount(app, db, "SIM-A")
    return app
