"""Versioned, deterministic wire contract. No network or database effects."""

import copy
from datetime import UTC, datetime
from decimal import Decimal
from email.utils import parsedate_to_datetime

from services.core.g1.common import digest, fail

PROFILES = {
    "SIM-A": {"port": 8001, "limit": 30, "rate": 5, "capability": "sim-a-g3-v1"},
    "SIM-B": {"port": 8002, "limit": 10, "rate": 2, "capability": "sim-b-v1"},
}


def cell_key(cell):
    return "/".join([cell["kind"], cell["key"], cell["date"]])


def validate_content(content, channel):
    if set(content) != {"hotel_key", "scope", "pools", "plans"}:
        fail("CONTENT_SCHEMA", 422)
    if not content["pools"] or not content["plans"]:
        fail("EMPTY_PRODUCT", 422)
    for p in content["plans"]:
        if p["adults"] != 2 or p["currency"] != "USD":
            fail("UNSUPPORTED_OCCUPANCY_CURRENCY", 422)
        if p["pool_key"] not in content["pools"]:
            fail("POOL_REFERENCE", 422)
        policy = p["cancellation"]
        if channel == "SIM-B" and policy != {
            "kind": "NONREF",
            "deadline_hours": 0,
            "penalty_basis": "stay_total",
        }:
            fail("CANCELLATION_NOT_REPRESENTABLE", 422)
        if channel == "SIM-A" and policy.get("kind") not in {"FLEX", "NONREF"}:
            fail("CANCELLATION_NOT_REPRESENTABLE", 422)


def wire_content(content, channel):
    result = copy.deepcopy(content)
    if channel == "SIM-B":
        validate_content(result, channel)
        for p in result["plans"]:
            p["cancellation"] = {"refundable": False}
    return result


def normalize_content(content, channel):
    result = copy.deepcopy(content)
    if channel == "SIM-B":
        for p in result["plans"]:
            if p["cancellation"] != {"refundable": False}:
                fail("UNREPRESENTABLE_REMOTE_POLICY", 422)
            p["cancellation"] = {
                "kind": "NONREF",
                "deadline_hours": 0,
                "penalty_basis": "stay_total",
            }
    validate_content(result, channel)
    return result


def validate_cell(c):
    from datetime import date

    date.fromisoformat(c["date"])
    if type(c["sellable"]) is not bool:
        fail("CELL_BOOLEAN", 422)
    if c["kind"] == "pool":
        if (
            set(c) != {"kind", "key", "date", "quantity", "sellable"}
            or type(c["quantity"]) is not int
            or c["quantity"] < 0
        ):
            fail("POOL_CELL", 422)
    elif c["kind"] == "rate":
        if set(c) != {"kind", "key", "date", "net_amount", "sell_amount", "sellable"}:
            fail("RATE_CELL", 422)
        for name in ["net_amount", "sell_amount"]:
            v = c[name]
            if not isinstance(v, str) or not Decimal(v).is_finite() or Decimal(v) <= 0:
                fail("RATE_MONEY", 422)
    else:
        fail("CELL_KIND", 422)


def chunks(payload, channel):
    cells = sorted(payload["cells"], key=cell_key)
    if len({cell_key(c) for c in cells}) != len(cells):
        fail("DUPLICATE_CELL", 422)
    limit = PROFILES[channel]["limit"]
    return [cells[i : i + limit] for i in range(0, len(cells), limit)]


def close_payload(payload, dates):
    result = copy.deepcopy(payload)
    for c in result["cells"]:
        if c["date"] in dates:
            c["sellable"] = False
            if c["kind"] == "pool":
                c["quantity"] = 0
    return result


def retry_after(value, clock=None):
    clock = clock or datetime.now(UTC)
    try:
        if str(value).isdigit():
            return max(0, int(value))
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return max(0, (parsed - clock).total_seconds())
    except (ValueError, TypeError, OverflowError):
        return None


def payload_hash(payload):
    return digest(payload)
