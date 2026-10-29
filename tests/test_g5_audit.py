from uuid import UUID

from infra.manage import uid
from services.core.g5.audit import verify_package
from tests.g1_helpers import auth, ok
from tests.g3_helpers import drain, prepared, publish


def test_actual_published_audit_graph_and_export(client, settings):
    bundle = prepared(client, settings)
    plan = publish(client, settings, bundle)
    drain(
        client,
        settings,
        lambda: (
            ok(client.get("/v3/plans/" + plan["id"], headers=auth(settings)))["state"] == "VERIFIED"
        ),
    )
    listing = plan["branches"][0]["listing_id"]
    with client.app.state.db.transaction(uid("tenant-A")) as c:
        before_read = c.execute("SELECT (SELECT count(*) FROM execution_job) AS jobs,(SELECT count(*) FROM g3_task) AS tasks,(SELECT count(*) FROM outbox_event) AS events,(SELECT count(*) FROM g4_ai_task) AS ai_tasks").fetchone()
    response = client.get("/v5/audit/listing/" + listing, headers=auth(settings, "auditor"))
    data = ok(response)
    assert data["complete"], data["missing"]
    tables = {n["table"] for n in data["nodes"]}
    assert {
        "g3_listing",
        "g3_snapshot",
        "g3_task",
        "g3_receipt",
        "g3_observation",
        "source_activation",
        "artifact",
        "processing_record",
    } <= tables
    assert not any(x["hash_status"] == "MISMATCH" for x in data["nodes"])
    snapshot_graph = ok(
        client.get(
            "/v5/audit/snapshot/" + bundle["snapshots"][0]["id"], headers=auth(settings, "auditor")
        )
    )
    assert {"g3_intent", "g3_task", "g3_receipt", "g3_observation"} <= {
        x["table"] for x in snapshot_graph["nodes"]
    }
    assert all(n["data"].get("storage_key") in {None, "[REDACTED]"} for n in data["nodes"])
    with client.app.state.db.transaction(uid("tenant-A")) as c:
        assert c.execute("SELECT (SELECT count(*) FROM execution_job) AS jobs,(SELECT count(*) FROM g3_task) AS tasks,(SELECT count(*) FROM outbox_event) AS events,(SELECT count(*) FROM g4_ai_task) AS ai_tasks").fetchone() == before_read
    assert client.get(
        "/v5/audit/listing/" + listing, headers=auth(settings, "auditor", "B")
    ).status_code in {403, 404}
    tiny = ok(client.get("/v5/audit/listing/" + listing + "?limit=10", headers=auth(settings)))
    assert tiny["truncated"] and not tiny["complete"]
    package = ok(
        client.post("/v5/audit/listing/" + listing + "/export", headers=auth(settings, "auditor"))
    )
    assert verify_package(package)
    with client.app.state.db.transaction(uid("tenant-A")) as c:
        jobs = c.execute(
            "SELECT trace_id,cause_event_id FROM execution_job WHERE resource_id IN(SELECT t.id FROM g3_task t JOIN g3_intent i ON i.id=t.intent_id WHERE i.listing_id=%s)",
            (UUID(listing),),
        ).fetchall()
        assert any(x["trace_id"] and x["cause_event_id"] for x in jobs)
