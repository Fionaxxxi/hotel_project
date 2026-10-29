from uuid import UUID

from infra.manage import uid
from services.core.g4 import ai, workflow
from services.core.g4.local_model import LocalModel
from tests.g1_helpers import auth, ok
from tests.g2_helpers import parse, start_run
from tests.g3_helpers import drain, prepared


def configured(client, settings, **limits):
    bundle = prepared(client, settings)
    p = ok(
        client.post(
            "/v4/policies",
            headers=auth(settings, "tenant_admin"),
            json={
                "baseline_ids": [s["id"] for s in bundle["snapshots"]],
                "settings": {"auto_activate": True, "auto_publish": True, **limits},
            },
        )
    )
    for action, role in [
        ("submit", "tenant_admin"),
        ("approve", "reviewer"),
        ("enable", "tenant_admin"),
    ]:
        p = ok(
            client.post(
                "/v4/policies/" + p["id"] + "/commands",
                headers=auth(settings, role),
                json={
                    "action": action,
                    "expected_version": p["version"],
                    "reason": "G4 explicit synthetic-only authorization",
                },
            )
        )
    sub = ok(
        client.post(
            "/v4/subscriptions", headers=auth(settings, "tenant_admin"), json={"policy_id": p["id"]}
        )
    )
    return bundle, p, sub


def update(client, settings, bundle, edit=None, sequence=2):
    code = bundle["allocation"]["source_key"].split("/")[1]
    _, run = start_run(client, settings, code=code, sequence=sequence, edit=edit)
    parse(client, settings, run)
    return run


def tick(client, sub):
    workflow.tick(client.app.state.db, uid("tenant-A"), [UUID(sub["id"])])
    return next(
        r
        for r in ok(client.get("/v4/overview", headers=auth(client.app.state.settings)))["runs"]
        if r["subscription_id"] == sub["id"]
    )


def test_g4_regular_update_automatically_creates_dual_plan(client, settings):
    bundle, p, sub = configured(client, settings)
    update(client, settings, bundle)
    r = tick(client, sub)
    assert r["plan_id"], r
    assert r["state"] == "RUNNING"
    plan = ok(client.get("/v3/plans/" + r["plan_id"], headers=auth(settings)))
    client.app.state.db.g3_assigned_listings = [UUID(b["listing_id"]) for b in plan["branches"]]
    drain(
        client,
        settings,
        lambda: (
            ok(client.get("/v3/plans/" + r["plan_id"], headers=auth(settings)))["state"]
            == "VERIFIED"
        ),
    )
    assert tick(client, sub)["state"] == "COMPLETED"
    detail = ok(client.get("/v4/runs/" + r["id"], headers=auth(settings)))
    assert len(detail["decisions"]) == 2
    assert all(d["result"] == "ALLOW" for d in detail["decisions"])


def test_g4_real_ai_and_disabled_fallback(client, settings, monkeypatch):
    bundle = prepared(client, settings)
    with client.app.state.db.transaction(uid("tenant-A")) as c:
        source = c.execute(
            "SELECT id FROM supplier_entity_version WHERE activation_id=%s AND kind='property'",
            (bundle["gen"]["activation_id"],),
        ).fetchone()["id"]
    task = ok(
        client.post("/v4/suggestions", headers=auth(settings), json={"source_id": str(source)})
    )
    monkeypatch.setenv("G4_AI_ENABLED", "true")
    model = LocalModel()
    try:
        assert ai.tick(client.app.state.db, uid("tenant-A"), "g4-test", model, [UUID(task["id"])])
    finally:
        model.close()
    item = ok(client.get("/v4/suggestions/" + task["id"], headers=auth(settings)))
    assert item["result"]["status"] == "AI_AVAILABLE", item
    assert item["result"]["ranked_candidates"][0]["evidence"]


def test_g4_policy_self_approval_denied(client, settings):
    bundle = prepared(client, settings)
    p = ok(
        client.post(
            "/v4/policies",
            headers=auth(settings, "tenant_admin"),
            json={"baseline_ids": [s["id"] for s in bundle["snapshots"]]},
        )
    )
    p = ok(
        client.post(
            "/v4/policies/" + p["id"] + "/commands",
            headers=auth(settings, "tenant_admin"),
            json={"action": "submit", "expected_version": p["version"], "reason": "submit"},
        )
    )
    r = client.post(
        "/v4/policies/" + p["id"] + "/commands",
        headers=auth(settings, "tenant_admin"),
        json={"action": "approve", "expected_version": p["version"], "reason": "self"},
    )
    assert r.status_code == 403
