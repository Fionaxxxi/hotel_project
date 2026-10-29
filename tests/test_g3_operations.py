from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest

from infra.manage import uid
from services.core.g1 import distribution
from services.core.g1.common import get
from services.core.g2 import jobs
from services.core.g3 import engine
from tests.g1_helpers import approved_snapshot, auth, import_active, ok
from tests.g3_helpers import drain, fault, listing, prepared, publish, remote, verified


def approve_new(client, settings, allocation, channels=["SIM-A", "SIM-B"]):
    result = []
    for channel in channels:
        s = ok(
            client.post(
                "/v3/snapshots",
                headers=auth(settings),
                json={
                    "allocation_id": allocation["id"],
                    "channel": channel,
                    "plan_codes": ["NONREF"],
                },
            )
        )
        result.append(
            ok(
                client.post(
                    "/v3/snapshots/" + s["id"] + "/decisions",
                    headers=auth(settings, "reviewer"),
                    json={
                        "expected_version": s["version"],
                        "decision": "APPROVED",
                        "reason": "New explicit review",
                        "allow_repair": True,
                    },
                )
            )
        )
    return result


def test_g3_safe_allocation_transfer_waits_for_remote_reduction(client, settings):
    b, p = verified(client, settings, prepared(client, settings, amounts={"SIM-A": 3, "SIM-B": 0}))
    body = {
        "activation_id": b["gen"]["activation_id"],
        "amounts": {"SIM-A": 0, "SIM-B": 3},
        "buffer": 1,
        "expected_version": 1,
    }
    assert client.post("/v3/allocations", headers=auth(settings), json=body).status_code == 409
    body["amounts"] = {"SIM-A": 0, "SIM-B": 0}
    a = ok(client.post("/v3/allocations", headers=auth(settings), json=body))
    snapshots = approve_new(client, settings, a, ["SIM-A"])
    plan = publish(
        client, settings, {"snapshots": snapshots}, expected={snapshots[0]["account_id"]: 1}
    )
    drain(
        client,
        settings,
        lambda: (
            ok(client.get("/v3/plans/" + plan["id"], headers=auth(settings)))["state"] == "VERIFIED"
        ),
    )
    body.update(amounts={"SIM-A": 0, "SIM-B": 3}, expected_version=2)
    assert ok(client.post("/v3/allocations", headers=auth(settings), json=body))["version"] == 3


def test_g3_new_approval_can_reopen_after_confirmed_stop(client, settings):
    b, p = verified(client, settings)
    for branch in p["branches"]:
        li = listing(client, settings, branch["listing_id"])
        ok(
            client.post(
                "/v3/listings/" + li["id"] + "/stop",
                headers={**auth(settings, "tenant_admin"), "Idempotency-Key": str(uuid4())},
                json={
                    "expected_version": li["version"],
                    "dates": ["2027-01-10", "2027-01-11", "2027-01-12"],
                    "reason": "Close then explicitly reopen",
                },
            )
        )
    drain(
        client,
        settings,
        lambda: all(
            listing(client, settings, x["listing_id"])["state"] == "STOPPED" for x in p["branches"]
        ),
    )
    new = approve_new(client, settings, b["allocation"])
    plan = publish(
        client,
        settings,
        {"snapshots": new},
        expected={s["account_id"]: 2 for s in new},
        reopen=True,
    )
    drain(
        client,
        settings,
        lambda: (
            ok(client.get("/v3/plans/" + plan["id"], headers=auth(settings)))["state"] == "VERIFIED"
        ),
    )


def test_g3_initial_cancel_has_no_remote_side_effect(client, settings):
    b = prepared(client, settings)
    p = publish(client, settings, b)
    result = ok(
        client.post(
            "/v3/plans/" + p["id"] + "/cancel",
            headers=auth(settings),
            json={
                "action": "cancel",
                "expected_version": 1,
                "reason": "Cancel before first dispatch",
            },
        )
    )
    assert result["state"] == "CANCELLED"
    for s in b["snapshots"]:
        assert (
            remote("SIM-A" if s == b["snapshots"][0] else "SIM-B", s["listing_key"]).status_code
            == 404
        )
    for _ in range(6):
        engine.tick(
            client.app.state.db,
            "cancel-check",
            listing_ids=client.app.state.db.g3_assigned_listings,
        )
    for s in b["snapshots"]:
        assert (
            remote("SIM-A" if s == b["snapshots"][0] else "SIM-B", s["listing_key"]).status_code
            == 404
        )


def test_g3_shared_quota_with_independent_db_connections(client, settings):
    db = client.app.state.db
    tenant = uid("tenant-A")
    with db.transaction(tenant) as c:
        a = c.execute("SELECT * FROM g3_account WHERE code='SIM-B'").fetchone()
        c.execute(
            "UPDATE g3_account SET next_write_at=clock_timestamp(),cooldown_until=clock_timestamp() WHERE id=%s",
            (a["id"],),
        )

    def take():
        with db.transaction(tenant) as c:
            return engine.quota(c, a)

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: take(), range(2)))
    assert sum(v == 0 for v in results) == 1 and max(results) > 0.3


def test_g3_stop_dominates_paused_sale_and_old_repair(client, settings):
    b, p = verified(client, settings)
    lid = p["branches"][1]["listing_id"]
    key = b["snapshots"][1]["listing_key"]
    fault(settings, "SIM-B", key, "drift")
    d = engine.reconcile(client.app.state.db, uid("tenant-A"), lid)
    li = listing(client, settings, lid)
    ok(
        client.post(
            "/v3/listings/" + lid + "/commands",
            headers=auth(settings),
            json={
                "action": "pause",
                "expected_version": li["version"],
                "reason": "Pause before old repair",
            },
        )
    )
    li = listing(client, settings, lid)
    repair = ok(
        client.post(
            "/v3/diffs/" + d["diff_id"] + "/repair",
            headers=auth(settings),
            json={"expected_version": li["version"]},
        )
    )
    li = listing(client, settings, lid)
    ok(
        client.post(
            "/v3/listings/" + lid + "/stop",
            headers={**auth(settings, "tenant_admin"), "Idempotency-Key": str(uuid4())},
            json={
                "expected_version": li["version"],
                "dates": ["2027-01-10", "2027-01-11", "2027-01-12"],
                "reason": "Close supersedes queued repair",
            },
        )
    )
    drain(client, settings, lambda: listing(client, settings, lid)["state"] == "STOPPED")
    with client.app.state.db.transaction(uid("tenant-A")) as c:
        rows = c.execute(
            "SELECT state,send_count FROM g3_task WHERE intent_id=%s", (repair["id"],)
        ).fetchall()
        assert all(r["state"] == "SUPERSEDED" and r["send_count"] == 0 for r in rows)


def test_g3_budget_exhaustion_and_audited_resume(client, settings):
    b = prepared(client, settings)
    p = publish(client, settings, b)
    tenant = uid("tenant-A")
    db = client.app.state.db
    with db.transaction(tenant) as c:
        task = c.execute(
            "SELECT * FROM g3_task WHERE intent_id=%s AND chunk_index=0", (p["branches"][0]["id"],)
        ).fetchone()
        c.execute(
            "UPDATE g3_task SET deadline=clock_timestamp()-interval '1 second' WHERE id=%s",
            (task["id"],),
        )
        engine.schedule(c, tenant, task["id"])
        job = jobs.claim(c, "budget-expire", kinds=["G3_DELIVER"], resource=task["id"])
    engine.execute(db, job)
    with db.transaction(tenant) as c:
        t = get(c, "g3_task", task["id"])
        d = c.execute("SELECT * FROM g3_dead_letter WHERE task_id=%s", (t["id"],)).fetchone()
        assert t["state"] == "MANUAL"
    body = {
        "action": "retry",
        "expected_version": t["version"],
        "reason": "Explicit new finite budget after investigation",
    }
    assert (
        client.post(
            "/v3/dead-letters/" + str(d["id"]) + "/retry",
            headers=auth(settings, "auditor"),
            json=body,
        ).status_code
        == 403
    )
    ok(
        client.post(
            "/v3/dead-letters/" + str(d["id"]) + "/retry", headers=auth(settings), json=body
        )
    )
    with db.transaction(tenant) as c:
        renewed = get(c, "g3_task", t["id"])
        assert renewed["budget_cycle"] == 2 and renewed["request_key"] == t["request_key"]


def test_g3_tables_force_rls_and_immutable_payload(client, settings):
    b = prepared(client, settings)
    tenant = uid("tenant-A")
    db = client.app.state.db
    s = b["snapshots"][0]
    tables = [
        "g3_account",
        "g3_source_control",
        "g3_allocation",
        "g3_allocation_head",
        "g3_snapshot",
        "g3_listing",
        "g3_reservation",
        "g3_plan",
        "g3_intent",
        "g3_task",
        "g3_receipt",
        "g3_cell_result",
        "g3_observation",
        "g3_diff",
        "g3_stop",
        "g3_dead_letter",
    ]
    with db.transaction(uid("tenant-B")) as c:
        for table in tables:
            flags = c.execute(
                "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE relname=%s", (table,)
            ).fetchone()
            assert flags["relrowsecurity"] and flags["relforcerowsecurity"]
            assert (
                c.execute(
                    f"SELECT count(*) AS n FROM {table} WHERE tenant_id=%s", (tenant,)
                ).fetchone()["n"]
                == 0
            )
    with pytest.raises(psycopg.Error):
        with db.transaction(tenant) as c:
            c.execute("UPDATE g3_snapshot SET payload='{}' WHERE id=%s", (s["id"],))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with db.transaction(uid("tenant-B")) as c:
            c.execute(
                "INSERT INTO g3_plan(id,tenant_id,command_key,request_hash,created_by) VALUES(%s,%s,'attack','x','b.operator')",
                (uuid4(), tenant),
            )
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with db.transaction(uid("tenant-B")) as c:
            c.execute(
                "INSERT INTO g3_observation(id,tenant_id,listing_id,desired_revision,classification,complete,data) VALUES(%s,%s,%s,1,'attack',true,'{}')",
                (uuid4(), uid("tenant-B"), uuid4()),
            )


def test_g3_legacy_exposure_requires_explicit_close(client, settings):
    bid, aid = import_active(
        client, settings, sequence=1, property_code="G3LEGACY" + uuid4().hex[:8]
    )
    snap = approved_snapshot(client, settings, aid)
    task = ok(
        client.post(
            "/v1/publish-plans",
            headers={**auth(settings), "Idempotency-Key": str(uuid4())},
            json={"snapshot_id": snap["id"]},
        )
    )
    result = distribution.dispatch_one(client.app.state.db, uid("tenant-A"), task["id"])
    assert result["status"] == "VERIFIED"
    adopted = ok(client.post(f"/v3/sources/{aid}/adopt", headers=auth(settings)))
    assert adopted["legacy_state"] == "EXPOSED"
    body = {
        "activation_id": aid,
        "amounts": {"SIM-A": 2, "SIM-B": 1},
        "buffer": 1,
        "expected_version": 0,
    }
    assert client.post("/v3/allocations", headers=auth(settings), json=body).status_code == 409
    assert client.post(f"/v3/sources/{aid}/close-legacy", headers=auth(settings)).status_code == 403
    assert (
        ok(client.post(f"/v3/sources/{aid}/close-legacy", headers=auth(settings, "tenant_admin")))[
            "legacy_state"
        ]
        == "CLOSED"
    )
    assert client.post("/v3/allocations", headers=auth(settings), json=body).status_code == 201
