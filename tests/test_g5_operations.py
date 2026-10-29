from uuid import UUID, uuid4

from infra.manage import uid
from services.core.g5 import operations
from tests.g1_helpers import auth, ok


def test_alert_discover_acknowledge_resolve_and_tenant_boundary(client, settings):
    db = client.app.state.db
    tenant = uid("tenant-A")
    eid = uuid4()
    with db.transaction(tenant) as c:
        c.execute(
            "INSERT INTO g5_worker_heartbeat(tenant_id,worker_id,lane,slot,last_seen) VALUES(%s,%s,'test','g5-test',clock_timestamp()-interval '2 minutes')",
            (tenant, eid),
        )
    assert client.post("/v5/operations/collect", headers=auth(settings)).status_code == 200
    rows = ok(client.get("/v5/operations/alerts?limit=100", headers=auth(settings)))["items"]
    row = next(x for x in rows if x["fingerprint"] == "WORKER_STALE" and x["state"] == "OPEN")
    assert str(eid) in row["detail"]["sample_ids"]
    assert row["state"] == "OPEN"
    assert client.post("/v5/operations/collect", headers=auth(settings)).status_code == 200
    assert client.post(
        f"/v5/operations/alerts/{row['id']}/acknowledge",
        headers=auth(settings, "operator", "B"),
        json={"expected_version": 1, "reason": "wrong tenant"},
    ).status_code in {403, 404}
    ack = ok(
        client.post(
            f"/v5/operations/alerts/{row['id']}/acknowledge",
            headers=auth(settings),
            json={"expected_version": row["version"], "reason": "Investigating exact outbox"},
        )
    )
    assert ack["state"] == "ACKNOWLEDGED"
    assert client.post("/v5/operations/collect", headers=auth(settings)).status_code == 200
    with db.transaction(tenant) as c:
        assert (
            c.execute("SELECT state FROM g5_alert WHERE id=%s", (UUID(row["id"]),)).fetchone()[
                "state"
            ]
            == "ACKNOWLEDGED"
        )
        c.execute(
            "UPDATE g5_worker_heartbeat SET last_seen=clock_timestamp(),slot='' WHERE worker_id=%s",
            (eid,),
        )
    assert client.post("/v5/operations/collect", headers=auth(settings)).status_code == 200
    with db.transaction(tenant) as c:
        assert (
            c.execute("SELECT state FROM g5_alert WHERE id=%s", (UUID(row["id"]),)).fetchone()[
                "state"
            ]
            == "RESOLVED"
        )
        events = c.execute(
            "SELECT action FROM g5_alert_event WHERE alert_id=%s ORDER BY created_at",
            (UUID(row["id"]),),
        ).fetchall()
        assert [x["action"] for x in events] == ["OPEN", "ACKNOWLEDGED", "RESOLVED"]


def test_metrics_are_tenant_scoped_and_read_only(client, settings):
    with client.app.state.db.transaction(uid("tenant-A")) as c:
        before = c.execute("SELECT count(*) AS n FROM audit_event").fetchone()["n"]
        data = operations.metrics(c)
        assert data["status"] == "CURRENT"
        assert data["queue"]["oldest_due_seconds"] >= 0
        actual = c.execute("SELECT state,count(*) AS count FROM execution_job GROUP BY state ORDER BY state").fetchall()
        assert data["groups"]["execution_job"] == actual
        early_due = c.execute("SELECT count(*) AS n FROM execution_job WHERE state IN ('READY','RETRY_WAIT') AND next_run_at<=%s", (data["collected_at"],)).fetchone()["n"]
        later_due = c.execute("SELECT count(*) AS n FROM execution_job WHERE state IN ('READY','RETRY_WAIT') AND next_run_at<=clock_timestamp()").fetchone()["n"]
        assert early_due <= data["queue"]["due"] <= later_due
    with client.app.state.db.transaction(uid("tenant-A")) as c:
        assert c.execute("SELECT count(*) AS n FROM audit_event").fetchone()["n"] == before
