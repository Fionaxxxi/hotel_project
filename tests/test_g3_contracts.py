import copy
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from uuid import uuid4

import pytest
from fastapi import HTTPException

from services.core.g1.common import digest
from services.core.g3.contract import chunks, normalize_content, retry_after, wire_content
from tests.g1_helpers import auth, ok
from tests.g3_helpers import prepared


@pytest.mark.parametrize(
    "value,expected", [("2", 2), ("0", 0), ("nonsense", None), (None, None), ("-2", None)]
)
def test_retry_after_variants(value, expected):
    assert retry_after(value) == expected


def test_retry_after_http_date():
    clock = datetime(2026, 1, 1, tzinfo=UTC)
    assert retry_after(format_datetime(clock + timedelta(seconds=120)), clock) == 120


@pytest.mark.parametrize(
    "channel,number,expected",
    [("SIM-A", 30, 1), ("SIM-A", 31, 2), ("SIM-B", 10, 1), ("SIM-B", 11, 2), ("SIM-B", 100, 10)],
)
def test_real_cell_chunk_counts(channel, number, expected):
    cells = [{"kind": "rate", "key": str(i), "date": "2027-01-10"} for i in range(number)]
    result = chunks({"cells": cells}, channel)
    assert len(result) == expected and sum(map(len, result)) == number
    assert digest(result) == digest(chunks({"cells": list(reversed(cells))}, channel))


def test_duplicate_cell_rejected():
    with pytest.raises(HTTPException):
        chunks({"cells": [{"kind": "pool", "key": "P", "date": "2027-01-10"}] * 2}, "SIM-B")


def test_capability_blocks_lossy_policy_and_enforces_approval(client, settings):
    b = prepared(client, settings, approve=False)
    allocation = b["allocation"]
    for channel, code in [("SIM-B", 422), ("SIM-A", 201)]:
        response = client.post(
            "/v3/snapshots",
            headers=auth(settings),
            json={"allocation_id": allocation["id"], "channel": channel, "plan_codes": ["FLEX"]},
        )
        assert response.status_code == code
    s = b["snapshots"][0]
    assert (
        client.post(
            f"/v3/snapshots/{s['id']}/decisions",
            headers=auth(settings),
            json={"expected_version": 1, "decision": "APPROVED", "reason": "self"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/v3/plans",
            headers={**auth(settings), "Idempotency-Key": str(uuid4())},
            json={"snapshot_ids": [s["id"]], "expected_revisions": {s["account_id"]: 0}},
        ).status_code
        == 409
    )


def test_wire_policy_is_distinct_and_lossless(client, settings):
    b = prepared(client, settings)
    canonical = b["snapshots"][1]["payload"]["content"]
    wire = wire_content(canonical, "SIM-B")
    assert wire["plans"][0]["cancellation"] == {"refundable": False}
    assert normalize_content(wire, "SIM-B") == canonical and digest(wire) != digest(canonical)


def test_atomic_target_set_and_idempotency(client, settings):
    b = prepared(client, settings)
    h = {**auth(settings), "Idempotency-Key": str(uuid4())}
    body = {
        "snapshot_ids": [s["id"] for s in b["snapshots"]],
        "expected_revisions": {s["account_id"]: 0 for s in b["snapshots"]},
    }
    p = ok(client.post("/v3/plans", headers=h, json=body))
    assert ok(client.post("/v3/plans", headers=h, json=body))["id"] == p["id"]
    bad = copy.deepcopy(body)
    bad["snapshot_ids"] = bad["snapshot_ids"][:1]
    assert client.post("/v3/plans", headers=h, json=bad).status_code == 409
    bad["expected_revisions"] = {bad["snapshot_ids"][0]: 999}
    assert (
        client.post(
            "/v3/plans", headers={**auth(settings), "Idempotency-Key": str(uuid4())}, json=bad
        ).status_code
        == 409
    )


def test_cross_tenant_roles_and_no_endpoints_injection(client, settings):
    b = prepared(client, settings)
    s = b["snapshots"][0]
    for path in [f"/v3/snapshots/{s['id']}", f"/v3/snapshots/{s['id']}/provenance"]:
        assert client.get(path, headers=auth(settings, tenant="B")).status_code == 404
    assert (
        client.post(
            "/v3/allocations",
            headers=auth(settings, "auditor"),
            json={
                "activation_id": b["gen"]["activation_id"],
                "amounts": {"SIM-A": 2, "SIM-B": 1},
                "buffer": 1,
                "expected_version": 1,
            },
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/v3/snapshots",
            headers=auth(settings),
            json={
                "allocation_id": b["allocation"]["id"],
                "channel": "SIM-B",
                "plan_codes": ["NONREF"],
                "endpoint": "https://example.invalid",
            },
        ).status_code
        == 422
    )


def test_overallocation_and_stale_allocation(client, settings):
    b = prepared(client, settings)
    body = {
        "activation_id": b["gen"]["activation_id"],
        "amounts": {"SIM-A": 3, "SIM-B": 1},
        "buffer": 1,
        "expected_version": 1,
    }
    assert client.post("/v3/allocations", headers=auth(settings), json=body).status_code == 409
    body["amounts"] = {"SIM-A": 1, "SIM-B": 2}
    new = ok(client.post("/v3/allocations", headers=auth(settings), json=body))
    assert new["version"] == 2
    assert client.post("/v3/allocations", headers=auth(settings), json=body).status_code == 409
    assert (
        client.post(
            "/v3/plans",
            headers={**auth(settings), "Idempotency-Key": str(uuid4())},
            json={
                "snapshot_ids": [s["id"] for s in b["snapshots"]],
                "expected_revisions": {s["account_id"]: 0 for s in b["snapshots"]},
            },
        ).status_code
        == 409
    )
