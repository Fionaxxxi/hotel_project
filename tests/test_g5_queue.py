from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

from infra.manage import uid
from services.core.g2 import jobs
from services.core.g3 import engine
from tests.g1_helpers import auth, ok
from tests.g3_helpers import drain, prepared, publish, verified


def test_two_delivery_relays_compete_without_crash_or_duplicate_jobs(client, settings):
    bundle = prepared(client, settings)
    plan = publish(client, settings, bundle)
    db = client.app.state.db
    tenant = uid("tenant-A")
    with db.transaction(tenant) as c:
        events = c.execute("SELECT e.id FROM outbox_event e JOIN g3_intent i ON i.id=e.aggregate_id WHERE i.plan_id=%s AND e.event_type='g3.intent.created'", (plan["id"],)).fetchall()
    for event in events:
        start = Barrier(2)

        def worker():
            start.wait(timeout=3)
            for _ in range(10):
                engine.relay(db, tenant, event_id=event["id"])

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: worker(), range(2)))
        with db.transaction(tenant) as c:
            assert c.execute("SELECT count(*) AS n FROM consumer_inbox WHERE event_id=%s AND consumer='g3-delivery-v1'", (event["id"],)).fetchone()["n"] == 1
    with db.transaction(tenant) as c:
        rows = c.execute("SELECT t.id,count(j.id) AS jobs FROM g3_task t JOIN g3_intent i ON i.id=t.intent_id LEFT JOIN execution_job j ON j.resource_id=t.id AND j.kind='G3_DELIVER' WHERE i.plan_id=%s GROUP BY t.id", (plan["id"],)).fetchall()
        assert rows and all(x["jobs"] == 1 for x in rows)


def test_delivery_relay_skips_unrelated_approval_notification_backlog(client, settings):
    bundle = prepared(client, settings)
    db = client.app.state.db
    tenant = uid("tenant-A")
    with db.transaction(tenant) as c:
        for _ in range(50):
            jobs.emit(c, tenant, "g3.approval.decided", bundle["snapshots"][0]["id"], 2, {"decision": "APPROVED"})
    publish(client, settings, bundle)
    with db.transaction(tenant) as c:
        event = c.execute("SELECT e.id FROM outbox_event e WHERE e.event_type='g3.intent.created' AND NOT EXISTS(SELECT 1 FROM consumer_inbox i WHERE i.event_id=e.id AND i.consumer='g3-delivery-v1') ORDER BY e.created_at LIMIT 1").fetchone()
    # A single delivery relay opportunity reaches an intent, regardless of unrelated fan-out.
    assert engine.relay(db, tenant)
    with db.transaction(tenant) as c:
        assert c.execute("SELECT 1 FROM consumer_inbox WHERE event_id=%s AND consumer='g3-delivery-v1'", (event["id"],)).fetchone()


def test_repeated_manual_reconciliation_coalesces_pending_reads(client, settings):
    _, plan = verified(client, settings)
    lid = plan["branches"][0]["listing_id"]
    ids = [
        ok(client.post("/v3/listings/" + lid + "/reconcile", headers=auth(settings)))["job_id"]
        for _ in range(20)
    ]
    assert len(set(ids)) == 1
    with client.app.state.db.transaction(uid("tenant-A")) as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM execution_job WHERE resource_id=%s AND kind IN ('G3_RECON','G3_VERIFY') AND state IN ('READY','RETRY_WAIT','RUNNING')",
                (lid,),
            ).fetchone()["n"]
            == 1
        )


def test_periodic_scan_does_not_duplicate_unfinished_reconciliation(client, settings, monkeypatch):
    _, plan = verified(client, settings)
    lid = plan["branches"][0]["listing_id"]
    db = client.app.state.db
    monkeypatch.setattr(engine, "execute", lambda *a, **kw: None)
    for _ in range(10):
        with db.transaction(uid("tenant-A")) as c:
            c.execute(
                "UPDATE g3_listing SET next_due_at=clock_timestamp()-interval '1 minute' WHERE id=%s",
                (lid,),
            )
        engine.tick(db, "g5-held-query", listing_ids=[lid])
    with db.transaction(uid("tenant-A")) as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM execution_job WHERE resource_id=%s AND kind IN ('G3_RECON','G3_VERIFY') AND state IN ('READY','RETRY_WAIT','RUNNING')",
                (lid,),
            ).fetchone()["n"]
            == 1
        )
        c.execute(
            "UPDATE execution_job SET state='CANCELLED',lease_until=NULL WHERE resource_id=%s AND kind IN ('G3_RECON','G3_VERIFY') AND state IN ('READY','RETRY_WAIT','RUNNING')",
            (lid,),
        )


def test_normal_delivery_overtakes_old_query_backlog(client, settings):
    _, old = verified(client, settings)
    old_lid = old["branches"][0]["listing_id"]
    db = client.app.state.db
    queued = []
    with db.transaction(uid("tenant-A")) as c:
        for _ in range(100):
            job = jobs.enqueue(
                c, uid("tenant-A"), "G3_RECON", old_lid, "g5-backlog:" + str(uuid4())
            )
            queued.append(job["id"])
        c.execute(
            "UPDATE execution_job SET next_run_at=clock_timestamp()-interval '10 minutes' WHERE id=ANY(%s)",
            (queued,),
        )
    bundle = prepared(client, settings)
    plan = publish(client, settings, bundle)
    try:
        drain(
            client,
            settings,
            lambda: (
                ok(client.get("/v3/plans/" + plan["id"], headers=auth(settings)))["state"]
                == "VERIFIED"
            ),
            # Use the frozen full-workflow budget, rather than an extra ad-hoc
            # 20-second probe. The completed-backlog assertion below still
            # proves that delivery overtakes the queued background reads.
            seconds=120,
        )
        with db.transaction(uid("tenant-A")) as c:
            count = c.execute(
                "SELECT count(*) AS n FROM execution_job WHERE id=ANY(%s) AND state='SUCCEEDED'",
                (queued,),
            ).fetchone()["n"]
            assert count < 100
    finally:
        with db.transaction(uid("tenant-A")) as c:
            c.execute(
                "UPDATE execution_job SET state='CANCELLED',lease_until=NULL WHERE id=ANY(%s) AND state IN ('READY','RUNNING','RETRY_WAIT')",
                (queued,),
            )
