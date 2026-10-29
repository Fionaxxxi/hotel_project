from uuid import uuid4

from infra.manage import uid
from services.core.g3 import engine
from tests.g1_helpers import auth, ok
from tests.g3_helpers import drain, fault, listing, verified


def test_g3_dual_channel_real_http(client, settings):
    bundle, plan = verified(client, settings)
    assert {b["code"] for b in plan["branches"]} == {"SIM-A", "SIM-B"}
    assert all(all(t["state"] == "APPLIED" for t in b["tasks"]) for b in plan["branches"])
    for s in bundle["snapshots"]:
        quantity = 2 if s == bundle["snapshots"][0] else 1
        assert {c["quantity"] for c in s["payload"]["cells"] if c["kind"] == "pool"} == {quantity}
        assert len(s["payload"]["cells"]) == 6


def test_g3_drift_repair_really_rewrites_remote(client, settings):
    _, plan = verified(client, settings)
    b = plan["branches"][1]
    lid = b["listing_id"]
    li = listing(client, settings, lid)
    fault(settings, "SIM-B", li["listing_key"], "drift")
    result = engine.reconcile(client.app.state.db, uid("tenant-A"), lid)
    assert result["classification"] == "DRIFT"
    li = listing(client, settings, lid)
    new = ok(
        client.post(
            "/v3/diffs/" + result["diff_id"] + "/repair",
            headers=auth(settings),
            json={"expected_version": li["version"]},
        )
    )
    assert (
        new["delivery_revision"] > b["delivery_revision"]
        and new["payload_hash"] == b["payload_hash"]
    )
    drain(client, settings, lambda: listing(client, settings, lid)["state"] == "VERIFIED")


def test_g3_stop_confirmation_and_fence(client, settings):
    bundle, plan = verified(client, settings)
    lids = [b["listing_id"] for b in plan["branches"]]
    for lid in lids:
        li = listing(client, settings, lid)
        response = client.post(
            "/v3/listings/" + str(lid) + "/stop",
            headers={**auth(settings), "Idempotency-Key": str(uuid4())},
            json={
                "expected_version": li["version"],
                "dates": ["2027-01-10", "2027-01-11", "2027-01-12"],
                "reason": "Emergency test",
            },
        )
        assert response.status_code == 403
        ok(
            client.post(
                "/v3/listings/" + str(lid) + "/stop",
                headers={**auth(settings, "tenant_admin"), "Idempotency-Key": str(uuid4())},
                json={
                    "expected_version": li["version"],
                    "dates": ["2027-01-10", "2027-01-11", "2027-01-12"],
                    "reason": "Emergency test",
                },
            )
        )
    drain(
        client,
        settings,
        lambda: all(listing(client, settings, lid)["state"] == "STOPPED" for lid in lids),
    )
    response = client.post(
        "/v3/plans",
        headers={**auth(settings), "Idempotency-Key": str(uuid4())},
        json={
            "snapshot_ids": [s["id"] for s in bundle["snapshots"]],
            "expected_revisions": {s["account_id"]: 2 for s in bundle["snapshots"]},
            "reopen": True,
        },
    )
    assert response.status_code == 409
