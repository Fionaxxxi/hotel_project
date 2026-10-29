from uuid import uuid4

import pytest

from infra.manage import uid
from services.core.g1.distribution import dispatch_one
from tests.g1_helpers import approved_snapshot, auth, import_active, ok, package


def test_g1_normal_chain(client, settings):
    bid, aid = import_active(client, settings, sequence=1)
    snapshot = approved_snapshot(client, settings, aid)
    assert len(snapshot["payload"]["pools"]) == 1
    assert all(c["quantity"] == 4 for c in snapshot["payload"]["pools"][0]["calendar"])
    assert len(snapshot["payload"]["plans"]) == 2
    assert all(
        c["sell_amount"] == "110.00" for p in snapshot["payload"]["plans"] for c in p["calendar"]
    )
    h = {**auth(settings), "Idempotency-Key": str(uuid4())}
    task = ok(client.post("/v1/publish-plans", headers=h, json={"snapshot_id": snapshot["id"]}))
    again = ok(client.post("/v1/publish-plans", headers=h, json={"snapshot_id": snapshot["id"]}))
    assert task["id"] == again["id"]
    result = dispatch_one(client.app.state.db, uid("tenant-A"), task["id"])
    assert result["status"] == "VERIFIED", result
    provenance = ok(
        client.get(f"/v1/snapshots/{snapshot['id']}/provenance", headers=auth(settings))
    )
    assert provenance["activation"]["batch_id"] == bid
    assert provenance["source_records"][0]["record_number"] >= 2
    assert client.get(f"/v1/imports/{bid}", headers=auth(settings, tenant="B")).status_code == 404
    artifact = provenance["source_records"][0]["artifact_id"]
    assert (
        client.get(
            f"/v1/artifacts/{artifact}/download", headers=auth(settings, tenant="B")
        ).status_code
        == 404
    )
    assert (
        client.get(f"/v1/artifacts/{artifact}/download", headers=auth(settings)).status_code == 200
    )


@pytest.mark.parametrize(
    "folder",
    ["F01-invalid-inventory", "F02-missing-parent", "F03-conflicting-key", "F05-hash-mismatch"],
)
def test_g1_invalid_files(client, settings, folder):
    bid = ok(client.post("/v1/imports", headers=auth(settings), files=package(folder)))["id"]
    result = ok(
        client.post(
            f"/v1/imports/{bid}/confirm", headers=auth(settings), json={"expected_version": 1}
        )
    )
    assert result["status"] == "INVALID"
    assert result["issues"]
    assert (
        client.post(
            f"/v1/imports/{bid}/activate",
            headers=auth(settings),
            json={"expected_version": result["version"]},
        ).status_code
        == 409
    )


def test_g1_upload_and_roles(client, settings):
    assert (
        client.post("/v1/imports", headers=auth(settings, "auditor"), files=package()).status_code
        == 403
    )
    assert (
        client.post(
            "/v1/imports", headers=auth(settings), files=package("F04-invalid-scope")
        ).status_code
        == 422
    )
    invalid = package()
    invalid[0] = ("files", ("../manifest.json", b"{}", "application/json"))
    assert client.post("/v1/imports", headers=auth(settings), files=invalid).status_code == 422
    assert (
        client.post("/v1/imports", headers=auth(settings), files=package()[:4]).status_code == 422
    )


def test_g1_duplicate_and_old_sequence(client, settings):
    code = "P" + uuid4().hex[:8]
    files = package(sequence=2, property_code=code)
    first = ok(client.post("/v1/imports", headers=auth(settings), files=files))
    assert (
        ok(client.post("/v1/imports", headers=auth(settings), files=list(reversed(files))))["id"]
        == first["id"]
    )
    result = ok(
        client.post(
            f"/v1/imports/{first['id']}/confirm",
            headers=auth(settings),
            json={"expected_version": 1},
        )
    )
    ok(
        client.post(
            f"/v1/imports/{first['id']}/activate",
            headers=auth(settings),
            json={"expected_version": result["version"]},
        )
    )
    old = ok(
        client.post(
            "/v1/imports", headers=auth(settings), files=package(sequence=1, property_code=code)
        )
    )
    r = ok(
        client.post(
            f"/v1/imports/{old['id']}/confirm", headers=auth(settings), json={"expected_version": 1}
        )
    )
    assert (
        client.post(
            f"/v1/imports/{old['id']}/activate",
            headers=auth(settings),
            json={"expected_version": r["version"]},
        ).status_code
        == 409
    )
