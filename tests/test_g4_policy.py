import copy
from uuid import UUID

import pytest
from fastapi import HTTPException

from infra.manage import uid
from services.core.g4 import ai, policy
from tests.g1_helpers import auth, ok
from tests.test_g4 import configured, tick, update


def payload(price="100", qty=2):
    return {
        "content": {"policy": "NONREF"},
        "cells": [
            {
                "kind": "rate",
                "key": "p",
                "date": "2027-01-10",
                "net_amount": price,
                "sell_amount": price,
                "sellable": True,
            },
            {
                "kind": "pool",
                "key": "pool",
                "date": "2027-01-10",
                "quantity": qty,
                "sellable": True,
            },
        ],
    }


@pytest.mark.parametrize(
    "price,allowed",
    [("100", True), ("110", True), ("90", True), ("110.01", False), ("89.99", False), ("0", False)],
)
def test_price_exact_decimal_boundaries(price, allowed):
    reasons = policy.differences(
        payload(),
        payload(),
        payload(price),
        {"relative_limit": "0.10", "absolute_limit": "20", "inventory_limit": 0},
    )
    assert (not reasons) == allowed


def test_cumulative_price_does_not_roll_anchor():
    reasons = policy.differences(
        payload(),
        payload("109"),
        payload("118"),
        {"relative_limit": "0.10", "absolute_limit": "20", "inventory_limit": 0},
    )
    assert any("BASELINE_PRICE_LIMIT" in r for r in reasons)
    assert not any("PREVIOUS_PRICE_LIMIT" in r for r in reasons)


@pytest.mark.parametrize(
    "value", [float("nan"), float("inf"), 1.1, True, "NaN", "Infinity", "-1", "bad"]
)
def test_decimal_rejects_unsafe_values(value):
    with pytest.raises(HTTPException):
        policy.decimal(value)


@pytest.mark.parametrize("change", ["terms", "scope", "stopped", "inventory"])
def test_policy_denies_semantic_changes(change):
    target = copy.deepcopy(payload())
    if change == "terms":
        target["content"]["policy"] = "FLEX"
    if change == "scope":
        target["cells"][0]["date"] = "2027-01-11"
    if change == "stopped":
        target["cells"][0]["sellable"] = False
    if change == "inventory":
        target["cells"][1]["quantity"] = 3
    assert policy.differences(
        payload(),
        payload(),
        target,
        {"relative_limit": "0.10", "absolute_limit": "20", "inventory_limit": 0},
    )


@pytest.mark.parametrize("scores", [[float("nan")], [float("inf")], [2], [], ["0.8"], [True]])
def test_model_output_schema(scores):
    with pytest.raises(HTTPException):
        ai.validate({"candidates": [{}]}, scores)


def test_price_exception_waits_then_human_approval_resumes(client, settings):
    bundle, p, sub = configured(client, settings)

    def edit(data, manifest):
        for row in data["rates"]:
            row["net_amount"] = "150.00"

    update(client, settings, bundle, edit)
    r = tick(client, sub)
    assert r["state"] == "WAIT_APPROVAL", r
    assert not r["plan_id"]
    for sid in r["snapshot_ids"]:
        snapshot = ok(client.get("/v3/snapshots/" + sid, headers=auth(settings)))
        ok(
            client.post(
                "/v3/snapshots/" + sid + "/decisions",
                headers=auth(settings, "reviewer"),
                json={
                    "decision": "APPROVED",
                    "expected_version": snapshot["version"],
                    "reason": "Reviewed exact out-of-policy synthetic update",
                },
            )
        )
    resumed = tick(client, sub)
    assert resumed["plan_id"] and resumed["state"] == "RUNNING"


def test_policy_pause_before_activation_blocks(client, settings):
    bundle, p, sub = configured(client, settings)
    ok(
        client.post(
            "/v4/policies/" + p["id"] + "/commands",
            headers=auth(settings, "tenant_admin"),
            json={
                "action": "pause",
                "expected_version": p["version"],
                "reason": "pause before source activation",
            },
        )
    )
    update(client, settings, bundle)
    r = tick(client, sub)
    assert r["state"] == "WAIT_SCOPE" and not r["activation_id"]


def test_policy_revoked_decision_no_longer_authorizes(client, settings):
    bundle, p, sub = configured(client, settings)
    update(client, settings, bundle)
    r = tick(client, sub)
    assert r["plan_id"]
    ok(
        client.post(
            "/v4/policies/" + p["id"] + "/commands",
            headers=auth(settings, "tenant_admin"),
            json={
                "action": "pause",
                "expected_version": p["version"],
                "reason": "pause after decision before send",
            },
        )
    )
    with client.app.state.db.transaction(uid("tenant-A")) as c:
        s = c.execute(
            "SELECT * FROM g3_snapshot WHERE id=%s", (UUID(r["snapshot_ids"][0]),)
        ).fetchone()
        with pytest.raises(HTTPException, match="POLICY_AUTHORIZATION_REVOKED"):
            policy.guard(c, s)


def test_ai_and_policy_cross_tenant_hidden(client, settings):
    bundle, p, sub = configured(client, settings)
    result = client.post(
        "/v4/policies/" + p["id"] + "/commands",
        headers=auth(settings, "tenant_admin", "B"),
        json={"action": "pause", "expected_version": p["version"], "reason": "unauthorized"},
    )
    assert result.status_code == 404
    assert not ok(client.get("/v4/overview", headers=auth(settings, "auditor", "B")))["policies"]


def test_repeated_workflow_tick_no_duplicate_effect(client, settings):
    bundle, p, sub = configured(client, settings)
    update(client, settings, bundle)
    first = tick(client, sub)
    for _ in range(100):
        assert tick(client, sub)["plan_id"] == first["plan_id"]
    detail = ok(client.get("/v4/runs/" + first["id"], headers=auth(settings)))
    assert len(detail["decisions"]) == 2


def test_inventory_deficit_waits_no_automatic_reallocation(client, settings):
    bundle, p, sub = configured(client, settings)

    def edit(data, manifest):
        for row in data["inventory"]:
            row["quantity"] = "2"

    update(client, settings, bundle, edit)
    r = tick(client, sub)
    assert r["state"] == "WAIT_ALLOCATION" and not r["plan_id"], r


def test_new_mapping_facts_require_human(client, settings):
    bundle, p, sub = configured(client, settings)

    def edit(data, manifest):
        data["properties"][0]["name"] = "Different Annex Hotel"

    update(client, settings, bundle, edit)
    r = tick(client, sub)
    assert r["state"] == "WAIT_MAPPING", r
