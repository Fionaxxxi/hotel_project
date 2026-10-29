import hashlib
import json
from pathlib import Path
from uuid import uuid4

from services.core.auth import issue_token

ROOT = Path(__file__).resolve().parents[1]


def auth(settings, role="operator", tenant="A"):
    from infra.manage import uid

    return {
        "Authorization": "Bearer " + issue_token(f"{tenant.lower()}.{role}", settings.jwt_secret),
        "X-Tenant-ID": str(uid(f"tenant-{tenant}")),
    }


def package(folder="F00-valid", sequence=None, property_code=None, edit=None):
    content = {
        p.name: p.read_bytes()
        for p in (ROOT / "datasets/fixtures" / folder).iterdir()
        if p.name == "manifest.json" or p.suffix == ".csv"
    }
    if sequence is None and property_code is None and edit is None:
        return [
            ("files", (name, data, "application/octet-stream")) for name, data in content.items()
        ]
    manifest = json.loads(content["manifest.json"])
    manifest["source_sequence"] = sequence or 1
    code = property_code or "T" + uuid4().hex[:10]
    manifest["scope"]["property_codes"] = [code]
    for name, data in list(content.items()):
        if name.endswith(".csv"):
            content[name] = data.decode().replace("001", code).encode()
    if edit:
        edit(content, manifest)
    for f in manifest["files"]:
        f["sha256"] = hashlib.sha256(content[f["name"]]).hexdigest()
    content["manifest.json"] = json.dumps(manifest).encode()
    return [("files", (name, data, "application/octet-stream")) for name, data in content.items()]


def ok(response):
    assert response.status_code < 300, (response.status_code, response.text[:500])
    return response.json()


def import_active(client, settings, **kwargs):
    h = auth(settings)
    bid = ok(client.post("/v1/imports", headers=h, files=package(**kwargs)))["id"]
    validated = ok(
        client.post(f"/v1/imports/{bid}/confirm", headers=h, json={"expected_version": 1})
    )
    assert validated["status"] == "READY", validated["issues"]
    activation = ok(
        client.post(
            f"/v1/imports/{bid}/activate",
            headers=h,
            json={"expected_version": validated["version"]},
        )
    )
    return bid, activation["id"]


def approve_mappings(client, settings, aid):
    h = auth(settings, "reviewer")
    for kind in ["property", "room", "rate"]:
        sources = ok(client.get("/v1/match-proposals", headers=h, params={"activation_id": aid}))[
            "items"
        ]
        for source in [s for s in sources if s["kind"] == kind]:
            candidates = [c for c in source["candidates"] if not c["hard_conflicts"]]
            assert candidates, source["natural_key"]
            candidate = candidates[0]
            ok(
                client.post(
                    f"/v1/mappings/{kind}/{source['id']}/decisions",
                    headers=h,
                    json={
                        "target_id": candidate["target_id"],
                        "decision": "APPROVED",
                        "reason": "Synthetic facts checked",
                        "expected_version": source["mapping"]["version"]
                        if source["mapping"]
                        else 0,
                    },
                )
            )


def approved_snapshot(client, settings, aid):
    approve_mappings(client, settings, aid)
    snapshot = ok(
        client.post("/v1/offers/compile", headers=auth(settings), json={"activation_id": aid})
    )
    snapshot = ok(
        client.post(
            f"/v1/snapshots/{snapshot['id']}/submit",
            headers=auth(settings),
            json={"expected_version": snapshot["version"]},
        )
    )
    return ok(
        client.post(
            f"/v1/snapshots/{snapshot['id']}/decisions",
            headers=auth(settings, "reviewer"),
            json={
                "expected_version": snapshot["version"],
                "decision": "APPROVED",
                "reason": "Reviewed immutable snapshot",
            },
        )
    )
