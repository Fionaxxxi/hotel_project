import copy
import os
from datetime import timedelta
from uuid import uuid4

import httpx

from infra.manage import uid
from services.core.g1.common import get, now
from services.core.g3 import engine, transport
from tests.g1_helpers import auth, ok
from tests.g3_helpers import drain, fault, listing, prepared, publish, remote, verified


def test_real_multi_chunk_manifest_and_cell_limits(client, settings):
    def six_days(data, m):
        for kind in ["inventory", "rates"]:
            original = list(data[kind])
            for r in original:
                day = int(r["stay_date"][-2:]) + 3
                data[kind].append({**r, "stay_date": f"2027-01-{day:02}"})
        m["scope"]["stay_date_to_exclusive"] = "2027-01-16"

    days = [f"2027-01-{d:02}" for d in range(10, 16)]
    b, p = verified(
        client, settings, prepared(client, settings, edit=six_days, selected_dates=days)
    )
    assert len(p["branches"][0]["tasks"]) == 2 and len(p["branches"][1]["tasks"]) == 3
    assert [len(t["body"]["cells"]) for t in p["branches"][1]["tasks"]] == [0, 10, 2]
    observed = remote("SIM-B", b["snapshots"][1]["listing_key"]).json()
    assert len(observed["cells"]) == 12 and observed["complete"]


def test_auth_failure_is_account_scoped_and_recoverable(client, settings):
    b = prepared(client, settings)
    p = publish(client, settings, b)
    key = b["snapshots"][1]["listing_key"]
    fault(settings, "SIM-B", key, "auth")

    def current():
        return ok(client.get("/v3/plans/" + p["id"], headers=auth(settings)))

    try:
        drain(
            client,
            settings,
            lambda: (
                current()["branches"][1]["state"] == "MANUAL"
                and current()["branches"][0]["state"] == "VERIFIED"
            ),
        )
        overview = ok(client.get("/v3/overview", headers=auth(settings)))
        account = next(a for a in overview["accounts"] if a["code"] == "SIM-B")
        assert account["state"] == "PAUSED"
        assert (
            client.post(
                "/v3/accounts/" + account["id"] + "/resume",
                headers=auth(settings),
                json={
                    "action": "resume",
                    "expected_version": account["version"],
                    "reason": "Fixed credentials",
                },
            ).status_code
            == 403
        )
        fault(settings, "SIM-B", key, "clear")
        ok(
            client.post(
                "/v3/accounts/" + account["id"] + "/resume",
                headers=auth(settings, "tenant_admin"),
                json={
                    "action": "resume",
                    "expected_version": account["version"],
                    "reason": "Fixed test-only credential fault",
                },
            )
        )
        t = current()["branches"][1]["tasks"][0]
        d = next(d for d in overview["dead_letters"] if d["task_id"] == t["id"])
        ok(
            client.post(
                "/v3/dead-letters/" + d["id"] + "/retry",
                headers=auth(settings),
                json={
                    "action": "retry",
                    "expected_version": t["version"],
                    "reason": "Explicit resume after credential repair",
                },
            )
        )
        drain(client, settings, lambda: current()["state"] == "VERIFIED")
    finally:
        fault(settings, "SIM-B", key, "clear")
        with client.app.state.db.transaction(uid("tenant-A")) as c:
            c.execute("UPDATE g3_account SET state='ACTIVE' WHERE code='SIM-B'")


def test_missing_is_not_unknown_and_ungranted_repair_denied(client, settings):
    b = prepared(client, settings, approve=False)
    for idx, s in enumerate(b["snapshots"]):
        b["snapshots"][idx] = ok(
            client.post(
                "/v3/snapshots/" + s["id"] + "/decisions",
                headers=auth(settings, "reviewer"),
                json={
                    "expected_version": s["version"],
                    "decision": "APPROVED",
                    "reason": "Publish only, no drift replay grant",
                    "allow_repair": False,
                },
            )
        )
    b, p = verified(client, settings, b)
    lid = p["branches"][1]["listing_id"]
    fault(settings, "SIM-B", b["snapshots"][1]["listing_key"], "missing")
    d = engine.reconcile(client.app.state.db, uid("tenant-A"), lid)
    assert d["classification"] == "MISSING"
    li = listing(client, settings, lid)
    assert (
        client.post(
            "/v3/diffs/" + d["diff_id"] + "/repair",
            headers=auth(settings),
            json={"expected_version": li["version"]},
        ).status_code
        == 403
    )


def test_stale_diff_cannot_resurrect_new_stop(client, settings):
    b, p = verified(client, settings)
    lid = p["branches"][1]["listing_id"]
    fault(settings, "SIM-B", b["snapshots"][1]["listing_key"], "drift")
    d = engine.reconcile(client.app.state.db, uid("tenant-A"), lid)
    li = listing(client, settings, lid)
    ok(
        client.post(
            "/v3/listings/" + lid + "/stop",
            headers={**auth(settings, "tenant_admin"), "Idempotency-Key": str(uuid4())},
            json={
                "expected_version": li["version"],
                "dates": ["2027-01-10", "2027-01-11", "2027-01-12"],
                "reason": "New desired supersedes diff",
            },
        )
    )
    li = listing(client, settings, lid)
    assert (
        client.post(
            "/v3/diffs/" + d["diff_id"] + "/repair",
            headers=auth(settings),
            json={"expected_version": li["version"]},
        ).status_code
        == 409
    )
    drain(client, settings, lambda: listing(client, settings, lid)["state"] == "STOPPED")


def test_expired_base_can_still_be_safely_closed(client, settings):
    b, p = verified(client, settings)
    db = client.app.state.db
    with db.transaction(uid("tenant-A")) as c:
        for s in b["snapshots"]:
            c.execute(
                "UPDATE offer_snapshot SET expires_at=clock_timestamp()-interval '1 second' WHERE id=%s",
                (s["base_snapshot_id"],),
            )
    engine.safety_scan(db, uid("tenant-A"), db.g3_assigned_listings)
    drain(
        client,
        settings,
        lambda: all(
            listing(client, settings, x["listing_id"])["state"] == "STOPPED" for x in p["branches"]
        ),
    )
    with db.transaction(uid("tenant-A")) as c:
        assert c.execute(
            "SELECT 1 FROM audit_event WHERE actor='system.safety' AND action='g3.intent.stop' LIMIT 1"
        ).fetchone()


def test_overdue_stop_records_alert_without_false_confirmation(client, settings, monkeypatch):
    b, p = verified(client, settings)
    lid = p["branches"][1]["listing_id"]
    li = listing(client, settings, lid)
    intent = ok(
        client.post(
            "/v3/listings/" + lid + "/stop",
            headers={**auth(settings, "tenant_admin"), "Idempotency-Key": str(uuid4())},
            json={
                "expected_version": li["version"],
                "dates": ["2027-01-10", "2027-01-11", "2027-01-12"],
                "reason": "Controlled clock alert test",
            },
        )
    )
    monkeypatch.setattr(engine, "now", lambda: now() + timedelta(minutes=6))
    engine.safety_scan(
        client.app.state.db, uid("tenant-A"), client.app.state.db.g3_assigned_listings
    )
    with client.app.state.db.transaction(uid("tenant-A")) as c:
        assert c.execute(
            "SELECT 1 FROM g3_dead_letter d JOIN g3_task t ON t.id=d.task_id WHERE t.intent_id=%s AND d.code='STOP_UNCONFIRMED_OVER_5_MINUTES'",
            (intent["id"],),
        ).fetchone()
        assert get(c, "g3_listing", lid)["state"] == "STOP_SELL_REQUESTED"


def test_channel_identity_control_and_rls_are_distinct(client, settings):
    b, p = verified(client, settings)
    key = b["snapshots"][1]["listing_key"]
    with httpx.Client(base_url="http://127.0.0.1:8002", trust_env=False) as c:
        assert (
            c.get(
                "/g3/listings/" + key,
                headers={"Authorization": "Bearer " + os.environ["SIM_B_TOKEN"]},
            ).status_code
            == 401
        )
        assert (
            c.get(
                "/g3/listings/" + key, headers=transport.headers(uid("tenant-B"), "SIM-B")
            ).status_code
            == 404
        )
        assert (
            c.post(
                "/g3/control/" + key,
                headers=transport.headers(uid("tenant-A"), "SIM-B"),
                json={"mode": "drift"},
            ).status_code
            == 403
        )


def test_manifest_hash_tamper_is_rejected_independently(client, settings):
    b, p = verified(client, settings)
    body = copy.deepcopy(p["branches"][0]["tasks"][1]["body"])
    body["cells"][0]["quantity"] = 99
    response = transport.request(
        uid("tenant-A"),
        "SIM-A",
        "PUT",
        transport.listing_path(b["snapshots"][0]["listing_key"]),
        body,
        "tamper-" + str(uuid4()),
    )
    assert response.status_code == 422


def test_observed_inventory_overallocation_retains_exposure_and_safely_closes(client, settings):
    b, p = verified(client, settings, prepared(client, settings, amounts={"SIM-A": 1, "SIM-B": 1}))
    first = p["branches"][0]
    key = b["snapshots"][0]["listing_key"]
    cell = next(c for c in b["snapshots"][0]["payload"]["cells"] if c["kind"] == "pool")
    from services.core.g3.contract import cell_key

    fault(settings, "SIM-A", key, "drift", cell_key=cell_key(cell), value={"quantity": 9})
    observed = engine.reconcile(client.app.state.db, uid("tenant-A"), first["listing_id"])
    assert observed["classification"] == "CAPACITY_DEFICIT"
    body = {
        "activation_id": b["gen"]["activation_id"],
        "amounts": {"SIM-A": 1, "SIM-B": 2},
        "buffer": 1,
        "expected_version": 1,
    }
    assert client.post("/v3/allocations", headers=auth(settings), json=body).status_code == 409
    engine.safety_scan(
        client.app.state.db, uid("tenant-A"), client.app.state.db.g3_assigned_listings
    )
    drain(
        client,
        settings,
        lambda: all(
            listing(client, settings, x["listing_id"])["intent"]["state"] == "VERIFIED"
            for x in p["branches"]
        ),
    )
    for channel in ["SIM-A", "SIM-B"]:
        actual = remote(channel, key).json()
        assert all(
            not entry["value"]["sellable"]
            for entry in actual["cells"].values()
            if entry["value"]["date"] == cell["date"]
        )


def test_emergency_close_overtakes_ordinary_reconciliation_backlog(client, settings):
    from services.core.g2 import jobs

    b, p = verified(client, settings)
    lid = p["branches"][1]["listing_id"]
    db = client.app.state.db
    queued = []
    with db.transaction(uid("tenant-A")) as c:
        for _ in range(100):
            item = jobs.enqueue(c, uid("tenant-A"), "G3_RECON", lid, "backlog:" + str(uuid4()))
            queued.append(item["id"])
        c.execute(
            "UPDATE execution_job SET next_run_at=clock_timestamp()-interval '10 minutes' WHERE id=ANY(%s)",
            (queued,),
        )
    li = listing(client, settings, lid)
    ok(
        client.post(
            "/v3/listings/" + lid + "/stop",
            headers={**auth(settings, "tenant_admin"), "Idempotency-Key": str(uuid4())},
            json={
                "expected_version": li["version"],
                "dates": ["2027-01-10", "2027-01-11", "2027-01-12"],
                "reason": "Close must overtake100 ordinary checks",
            },
        )
    )
    try:
        drain(
            client,
            settings,
            lambda: listing(client, settings, lid)["state"] == "STOPPED",
            seconds=15,
        )
        with db.transaction(uid("tenant-A")) as c:
            done = c.execute(
                "SELECT count(*) AS n FROM execution_job WHERE id=ANY(%s) AND state='SUCCEEDED'",
                (queued,),
            ).fetchone()["n"]
            assert done < 100
    finally:
        with db.transaction(uid("tenant-A")) as c:
            c.execute(
                "UPDATE execution_job SET state='CANCELLED' WHERE id=ANY(%s) AND state IN ('READY','RETRY_WAIT')",
                (queued,),
            )
