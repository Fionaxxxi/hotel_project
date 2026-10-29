from uuid import uuid4

import psycopg

from infra.manage import uid
from services.core.g5.recovery import permit
from tests.g1_helpers import auth, ok
from tests.g3_helpers import drain, fault, listing, prepared, verified


def hold(settings, key):
    with psycopg.connect(settings.migration_url) as c:
        c.execute("SELECT set_config('app.tenant_id',%s,true)", (str(uid("tenant-A")),))
        c.execute(
            "INSERT INTO g5_recovery_scope(tenant_id,source_key,backup_id,reason) VALUES(%s,%s,'synthetic-restore-window','G5 test quarantine')",
            (uid("tenant-A"), key),
        )


def test_restore_namespace_defaults_readonly():
    assert not permit("hotel_project_g5_restore_no_such_run_core")
    assert permit("hotel_project_g5_test")


def test_recovery_fence_prevents_new_publish(client, settings):
    bundle = prepared(client, settings)
    hold(settings, bundle["allocation"]["source_key"])
    response = client.post(
        "/v3/plans",
        headers={**auth(settings), "Idempotency-Key": str(uuid4())},
        json={
            "snapshot_ids": [s["id"] for s in bundle["snapshots"]],
            "expected_revisions": {s["account_id"]: 0 for s in bundle["snapshots"]},
        },
    )
    assert response.status_code == 409
    assert response.json()["code"] == "RECOVERY_SCOPE_HELD"


def test_recovery_fresh_stop_and_release_retains_stop_fence(client, settings):
    bundle, plan = verified(client, settings)
    key = bundle["allocation"]["source_key"]
    hold(settings, key)
    assert (
        client.post(
            "/v5/recovery/release",
            headers=auth(settings, "tenant_admin"),
            json={"source_key": key, "reason": "premature"},
        ).json()["code"]
        == "RECOVERY_FRESH_STOP_REQUIRED"
    )
    assert (
        client.post(
            "/v5/recovery/release",
            headers=auth(settings),
            json={"source_key": key, "reason": "not admin"},
        ).status_code
        == 403
    )
    for branch in plan["branches"]:
        li = listing(client, settings, branch["listing_id"])
        fault(settings, branch["code"], li["listing_key"], "remote_ahead")
        intent = ok(
            client.post(
                "/v5/recovery/listings/" + li["id"] + "/stop",
                headers=auth(settings, "tenant_admin"),
                json={
                    "expected_version": li["version"],
                    "reason": "Observed newer remote, explicit closure during restore",
                },
            )
        )
        assert intent["action"] == "STOP" and intent["delivery_revision"] > li["delivery_revision"]
    drain(
        client,
        settings,
        lambda: all(
            listing(client, settings, b["listing_id"])["state"] == "STOPPED"
            for b in plan["branches"]
        ),
    )
    release = ok(
        client.post(
            "/v5/recovery/release",
            headers=auth(settings, "tenant_admin"),
            json={"source_key": key, "reason": "Fresh dual close checked against actual remote"},
        )
    )
    assert release["stop_fence"] == "RETAINED_NEW_APPROVAL_REQUIRED"
    for branch in plan["branches"]:
        li = listing(client, settings, branch["listing_id"])
        assert li["state"] == "STOPPED" and li["stop_epoch"] > 0
        assert not li["paused"]


def test_recovery_unknown_read_never_permits_stop_or_release(client, settings):
    bundle, plan = verified(client, settings)
    key = bundle["allocation"]["source_key"]
    hold(settings, key)
    branch = plan["branches"][0]
    li = listing(client, settings, branch["listing_id"])
    fault(settings, branch["code"], li["listing_key"], "read_failure")
    try:
        response = client.post(
            "/v5/recovery/listings/" + li["id"] + "/stop",
            headers=auth(settings, "tenant_admin"),
            json={"expected_version": li["version"], "reason": "Must retain unknown"},
        )
        assert response.status_code == 409
        assert response.json()["code"] == "RECOVERY_REMOTE_UNKNOWN"
        assert listing(client, settings, li["id"])["desired_revision"] == li["desired_revision"]
    finally:
        fault(settings, branch["code"], li["listing_key"], "clear")
