from uuid import UUID

import pytest

from infra.manage import uid
from services.core.g1.common import get
from services.core.g3 import engine
from services.core.g4 import ai
from services.core.g4.local_model import LocalModel
from tests.g1_helpers import auth, ok
from tests.g3_helpers import prepared
from tests.test_g4 import configured, tick, update


def test_actual_model_process_deadline_terminates_process(monkeypatch):
    monkeypatch.setenv("G4_AI_ENABLED", "true")
    model = LocalModel()
    try:
        model.start()
        process = model.process
        with pytest.raises(TimeoutError):
            model.receive(0.02)
        assert process.poll() is not None
        assert model.process is None
    finally:
        model.close()


def test_policy_revocation_stops_real_g3_unsent_tasks(client, settings):
    bundle, p, sub = configured(client, settings)
    update(client, settings, bundle)
    run = tick(client, sub)
    plan = ok(client.get("/v3/plans/" + run["plan_id"], headers=auth(settings)))
    lids = [UUID(b["listing_id"]) for b in plan["branches"]]
    ok(
        client.post(
            "/v4/policies/" + p["id"] + "/commands",
            headers=auth(settings, "tenant_admin"),
            json={
                "action": "revoke",
                "expected_version": p["version"],
                "reason": "revoke before any HTTP sale",
            },
        )
    )
    for _ in range(12):
        engine.tick(client.app.state.db, "revoke-test", listing_ids=lids)
    with client.app.state.db.transaction(uid("tenant-A")) as c:
        tasks = c.execute(
            "SELECT t.* FROM g3_task t JOIN g3_intent i ON i.id=t.intent_id WHERE i.plan_id=%s",
            (plan["id"],),
        ).fetchall()
        assert all(t["send_count"] == 0 for t in tasks)
        assert any(t["error"] == "POLICY_AUTHORIZATION_REVOKED" for t in tasks)


def test_newer_source_supersedes_waiting_old_run(client, settings):
    bundle, p, sub = configured(client, settings)

    def change(data, manifest):
        for row in data["rates"]:
            row["net_amount"] = "200"

    update(client, settings, bundle, change)
    first = tick(client, sub)
    assert first["state"] == "WAIT_APPROVAL"
    update(client, settings, bundle, sequence=3)
    tick(client, sub)
    tick(client, sub)
    old = ok(client.get("/v4/runs/" + first["id"], headers=auth(settings)))
    assert old["state"] == "STALE"


def test_scope_change_waits_explicit_review(client, settings):
    bundle, p, sub = configured(client, settings)

    def change(data, manifest):
        manifest["scope"]["stay_date_to_exclusive"] = "2027-01-14"

    update(client, settings, bundle, change)
    run = tick(client, sub)
    assert run["state"] == "WAIT_SCOPE" and not run["activation_id"]


def test_inventory_source_jump_not_hidden_by_static_target_quantity(client, settings):
    bundle, p, sub = configured(client, settings)

    def change(data, manifest):
        for row in data["inventory"]:
            row["quantity"] = "9"

    update(client, settings, bundle, change)
    run = tick(client, sub)
    assert run["state"] == "WAIT_APPROVAL", run
    d = ok(client.get("/v4/runs/" + run["id"], headers=auth(settings)))
    assert any(
        "SOURCE_INVENTORY_LIMIT" in reason
        for decision in d["decisions"]
        for reason in decision["reasons"]
    )


def test_system_actor_body_cannot_forge_approval(client, settings):
    b = prepared(client, settings, approve=False)
    result = client.post(
        "/v3/snapshots/" + b["snapshots"][0]["id"] + "/decisions",
        headers=auth(settings),
        json={
            "decision": "APPROVED",
            "expected_version": 1,
            "reason": "fake",
            "system_actor": "system.policy",
        },
    )
    assert result.status_code == 422


def test_cross_tenant_ai_reference_and_readonly_mutations(client, settings):
    b = prepared(client, settings)
    with client.app.state.db.transaction(uid("tenant-A")) as c:
        source = c.execute(
            "SELECT id FROM supplier_entity_version WHERE activation_id=%s LIMIT 1",
            (b["gen"]["activation_id"],),
        ).fetchone()["id"]
    assert (
        client.post(
            "/v4/suggestions",
            headers=auth(settings, "operator", "B"),
            json={"source_id": str(source)},
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/v4/suggestions", headers=auth(settings, "auditor"), json={"source_id": str(source)}
        ).status_code
        == 403
    )


def test_new_tables_direct_db_rls_and_immutable_policy(client, settings):
    bundle, p, sub = configured(client, settings)
    from psycopg.errors import RaiseException

    with client.app.state.db.transaction(uid("tenant-B")) as c:
        assert not c.execute("SELECT 1 FROM g4_policy WHERE id=%s", (p["id"],)).fetchone()
    with pytest.raises(RaiseException):
        with client.app.state.db.transaction(uid("tenant-A")) as c:
            c.execute("UPDATE g4_policy SET body='{}' WHERE id=%s", (p["id"],))


def test_ai_old_owner_cannot_commit_result(client, settings):
    b = prepared(client, settings)
    with client.app.state.db.transaction(uid("tenant-A")) as c:
        source = c.execute(
            "SELECT id FROM supplier_entity_version WHERE activation_id=%s AND kind='property'",
            (b["gen"]["activation_id"],),
        ).fetchone()["id"]
        task = ai.create(
            c, {"tenant_id": uid("tenant-A"), "subject": "a.operator", "role": "operator"}, source
        )

    class StealingModel:
        def rank(self, texts):
            with client.app.state.db.transaction(uid("tenant-A")) as c:
                c.execute(
                    "UPDATE g4_ai_task SET owner='new-owner',fence=fence+1 WHERE id=%s",
                    (task["id"],),
                )
            return {"scores": [0.9] * (len(texts) - 1)}

    ai.tick(client.app.state.db, uid("tenant-A"), "old-owner", StealingModel(), [task["id"]])
    with client.app.state.db.transaction(uid("tenant-A")) as c:
        result = get(c, "g4_ai_task", task["id"])
        assert result["owner"] == "new-owner" and result["result"] is None


def test_g2_legacy_workflow_owned_by_g4_before_activation(client, settings):
    bundle, p, sub = configured(client, settings)
    run = update(client, settings, bundle)
    from services.core.g2 import workflow as old

    with client.app.state.db.transaction(uid("tenant-A")) as c:
        w = old.ensure(c, uid("tenant-A"), get(c, "processing_run", UUID(run["id"])))
        result = old.advance(c, w["id"])
        assert result["state"] == "G4_MANAGED" and result["paused"]


def test_rejected_pair_changes_frozen_candidate_set(client, settings):
    b = prepared(client, settings)
    proposals = ok(
        client.get(
            "/v1/match-proposals?activation_id=" + b["gen"]["activation_id"], headers=auth(settings)
        )
    )["items"]
    source = next(x for x in proposals if x["kind"] == "property")
    target = source["mapping"]["target_id"]
    with client.app.state.db.transaction(uid("tenant-A")) as c:
        before = ai.package(c, UUID(source["id"]))
    ok(
        client.post(
            "/v1/mappings/property/" + source["id"] + "/decisions",
            headers=auth(settings, "reviewer"),
            json={
                "decision": "REJECTED",
                "expected_version": source["mapping"]["version"],
                "target_id": target,
                "reason": "Preserve negative-pair evidence for this exact source and target version",
            },
        )
    )
    with client.app.state.db.transaction(uid("tenant-A")) as c:
        after = ai.package(c, UUID(source["id"]))
        assert target not in [x["id"] for x in after["candidates"]]
        assert before["candidate_set_hash"] != after["candidate_set_hash"]
        assert c.execute(
            "SELECT 1 FROM g4_matching_feedback WHERE source_id=%s AND decision='REJECTED'",
            (source["id"],),
        ).fetchone()


def test_ai_fallback_budget_renewal_is_finite(client, settings, monkeypatch):
    b = prepared(client, settings)
    source = next(
        x
        for x in ok(
            client.get(
                "/v1/match-proposals?activation_id=" + b["gen"]["activation_id"],
                headers=auth(settings),
            )
        )["items"]
        if x["kind"] == "property"
    )
    task = ok(
        client.post("/v4/suggestions", headers=auth(settings), json={"source_id": source["id"]})
    )
    monkeypatch.setenv("G4_AI_ENABLED", "false")
    model = LocalModel()
    try:
        for cycle in range(1, 4):
            ai.tick(client.app.state.db, uid("tenant-A"), "budget-test", model, [UUID(task["id"])])
            task = ok(client.get("/v4/suggestions/" + task["id"], headers=auth(settings)))
            assert task["budget_cycle"] == cycle and task["result"]["status"] == "RULE_FALLBACK"
            response = client.post(
                "/v4/suggestions/" + task["id"] + "/retry",
                headers=auth(settings),
                json={
                    "expected_version": task["fence"],
                    "action": "retry",
                    "reason": "Explicit finite budget renewal",
                },
            )
            if cycle < 3:
                task = ok(response)
            else:
                assert response.status_code == 409
    finally:
        model.close()
