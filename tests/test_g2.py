from uuid import uuid4

import pytest
from fastapi import HTTPException

from infra.manage import uid
from services.core.g2 import jobs, workflow
from tests.g1_helpers import approve_mappings, auth, ok
from tests.g2_helpers import activate, drain, parse, ready, start_run


def test_g2_formats_and_reparse(client, settings):
    code = "EQ" + uuid4().hex[:8]
    hashes = []
    payload_hashes = []
    for seq, fmt in enumerate(["csv", "xlsx", "json"], 1):
        bid, run = start_run(client, settings, fmt=fmt, code=code, sequence=seq)
        result = parse(client, settings, run)
        assert result["status"] == "READY_TO_ACTIVATE"
        gen = activate(client, settings, run)
        hashes.append(gen["view_hash"])
        approve_mappings(client, settings, gen["activation_id"])
        snapshot = ok(
            client.post(
                "/v1/offers/compile",
                headers=auth(settings),
                json={"activation_id": gen["activation_id"]},
            )
        )
        payload_hashes.append(snapshot["payload_hash"])
        repeated = ok(client.post(f"/v2/imports/{bid}/runs", headers=auth(settings), json={}))
        assert repeated["id"] == run["id"]
        alternate = ok(
            client.post(
                f"/v2/imports/{bid}/runs",
                headers=auth(settings),
                json={"template": "supplier-v2-reviewed"},
            )
        )
        assert alternate["id"] != run["id"]
        parse(client, settings, alternate)
    assert len(set(hashes)) == 1
    assert len(set(payload_hashes)) == 1


def test_delta_inheritance_tombstone_and_provenance(client, settings):
    code = "DE" + uuid4().hex[:8]
    base = ready(client, settings, code=code)
    g1 = activate(client, settings, base)

    def edit(data, m):
        data["rates"] = [
            r for r in data["rates"] if r["plan_code"] == "FLEX" and r["stay_date"] == "2027-01-11"
        ]
        data["rates"][0]["net_amount"] = "123.45"

    delta = ready(client, settings, code=code, sequence=2, mode="delta", only=["rates"], edit=edit)
    g2 = activate(client, settings, delta)
    with client.app.state.db.transaction(uid("tenant-A")) as c:
        facts = c.execute(
            "SELECT * FROM generation_fact WHERE generation_id=%s AND kind='rates' ORDER BY natural_key",
            (g2["id"],),
        ).fetchall()
        assert len(facts) == 6
        assert sum(r["data"]["net_amount"] == "123.4500" for r in facts) == 1
        assert g2["parent_id"] == g1["id"]

    def delete(data, m):
        data["rates"] = [
            r for r in data["rates"] if r["plan_code"] == "FLEX" and r["stay_date"] == "2027-01-12"
        ]
        data["rates"][0]["operation"] = "delete"

    d = ready(client, settings, code=code, sequence=3, mode="delta", only=["rates"], edit=delete)
    g3 = activate(client, settings, d)
    with client.app.state.db.transaction(uid("tenant-A")) as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM generation_fact WHERE generation_id=%s AND tombstone",
                (g3["id"],),
            ).fetchone()["n"]
            == 1
        )
        from services.core.g2.views import provenance

        trace = provenance(c, g3["activation_id"])
        assert len(trace["source_records"]) == 14
        assert len({r["artifact_id"] for r in trace["source_records"] if r["kind"] == "rates"}) == 3


def test_quality_partial_activation(client, settings):
    def edit(data, m):
        data["inventory"][1]["quantity"] = "-1"

    run = ready(client, settings, edit=edit)
    current = ok(client.get("/v2/processing-runs/" + run["id"], headers=auth(settings)))
    assert current["status"] == "QUALITY_REVIEW"
    issues = ok(client.get(f"/v2/processing-runs/{run['id']}/issues", headers=auth(settings)))[
        "items"
    ]
    assert any(i["partition_key"] == "2027-01-11" for i in issues)
    body = {
        "expected_versions": current["expected_versions"],
        "expected_run_version": current["version"],
        "selected_dates": ["2027-01-10", "2027-01-11", "2027-01-12"],
    }
    response = client.post(
        f"/v2/processing-runs/{run['id']}/activate",
        headers={**auth(settings), "Idempotency-Key": str(uuid4())},
        json=body,
    )
    assert response.status_code == 409
    gen = activate(client, settings, run, selected=["2027-01-10", "2027-01-12"])
    with client.app.state.db.transaction(uid("tenant-A")) as c:
        cells = c.execute(
            "SELECT data FROM generation_fact WHERE generation_id=%s AND kind='inventory'",
            (gen["id"],),
        ).fetchall()
        assert {r["data"]["stay_date"] for r in cells} == {"2027-01-10", "2027-01-12"}


def test_workflow_decisions_resume_and_events_deduplicate(client, settings):
    _, run = start_run(client, settings)

    def current():
        return ok(client.get("/v2/processing-runs/" + run["id"], headers=auth(settings)))

    drain(client, settings, lambda: current()["status"] == "READY_TO_ACTIVATE")
    gen = activate(client, settings, run)
    drain(
        client,
        settings,
        lambda: current().get("workflow") and current()["workflow"]["state"] == "WAIT_MAPPING",
    )
    approve_mappings(client, settings, gen["activation_id"])
    drain(client, settings, lambda: current()["workflow"]["state"] == "WAIT_APPROVAL")
    wf = current()["workflow"]
    sid = wf["snapshot_id"]
    snapshot = ok(client.get("/v1/snapshots/" + sid, headers=auth(settings)))
    response = client.post(
        f"/v1/snapshots/{sid}/decisions",
        headers=auth(settings, "reviewer"),
        json={
            "expected_version": snapshot["version"],
            "decision": "APPROVED",
            "reason": "G2 independent reviewer",
        },
    )
    ok(response)
    drain(client, settings, lambda: current()["workflow"]["state"] == "COMPLETED")
    with client.app.state.db.transaction(uid("tenant-A")) as c:
        event = c.execute(
            "SELECT * FROM outbox_event WHERE aggregate_id=%s AND event_type='approval.decided'",
            (sid,),
        ).fetchone()
        assert workflow.consume(c, event) == "DUPLICATE"
        count = c.execute(
            "SELECT count(*) AS n FROM publish_task WHERE snapshot_id=%s", (sid,)
        ).fetchone()["n"]
        assert count == 1
        steps = c.execute("SELECT * FROM workflow_step WHERE run_id=%s", (wf["id"],)).fetchall()
        assert {s["step_key"] for s in steps} == {"PARSE", "ACTIVATE", "COMPILE", "PUBLISH"}


def test_leases_heartbeat_and_fencing(client, settings):
    db = client.app.state.db
    tenant = uid("tenant-B")
    with db.transaction(tenant) as c:
        item = jobs.enqueue(c, tenant, "TEST", uuid4(), str(uuid4()))
        first = jobs.claim(c, "owner-A", kinds=["TEST"])
        assert first["id"] == item["id"]
        assert jobs.claim(c, "owner-B", kinds=["TEST"]) is None
        jobs.heartbeat(c, first)
        c.execute(
            "UPDATE execution_job SET lease_until=clock_timestamp()-interval '1 second' WHERE id=%s",
            (item["id"],),
        )
    with db.transaction(tenant) as c:
        second = jobs.claim(c, "owner-B", kinds=["TEST"])
    assert second["fencing_token"] > first["fencing_token"]
    with pytest.raises(HTTPException):
        with db.transaction(tenant) as c:
            jobs.finish(c, first)
    with pytest.raises(HTTPException):
        with db.transaction(tenant) as c:
            jobs.heartbeat(c, first)
    with db.transaction(tenant) as c:
        jobs.finish(c, second)


def test_v2_tenant_isolation(client, settings):
    bid, run = start_run(client, settings)
    parse(client, settings, run)
    for path in [
        f"/v2/imports/{bid}/preview",
        f"/v2/processing-runs/{run['id']}",
        f"/v2/processing-runs/{run['id']}/issues",
        f"/v2/processing-runs/{run['id']}/issues.csv",
    ]:
        assert client.get(path, headers=auth(settings, tenant="B")).status_code == 404
    assert (
        client.post(
            f"/v2/imports/{bid}/runs", headers=auth(settings, "auditor"), json={}
        ).status_code
        == 403
    )
