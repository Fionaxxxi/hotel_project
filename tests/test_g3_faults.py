import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
import pytest

from infra.manage import ROOT, uid
from services.core.g1.common import digest, get
from services.core.g3 import engine, transport
from tests.g1_helpers import auth, ok
from tests.g3_helpers import drain, fault, listing, prepared, publish, remote, verified


def child(window, resource, *args):
    return subprocess.run(
        [sys.executable, "-s", "-m", "infra.g3_fault_child", window, str(resource), *args],
        capture_output=True,
        text=True,
        timeout=25,
    )


@pytest.mark.parametrize("window,code,count", [("before", 81, 0), ("after", 82, 1)])
def test_g3_real_intent_commit_windows(client, settings, window, code, count):
    b = prepared(client, settings)
    key = str(uuid4())
    path = ROOT / ".runtime" / ("g3-command-" + key + ".json")
    path.write_text(
        json.dumps(
            {
                "key": key,
                "snapshots": [s["id"] for s in b["snapshots"]],
                "expected": {s["account_id"]: 0 for s in b["snapshots"]},
            }
        )
    )
    try:
        assert child(window, "unused", "--body", str(path)).returncode == code
    finally:
        path.unlink()
    with client.app.state.db.transaction(uid("tenant-A")) as c:
        p = c.execute("SELECT * FROM g3_plan WHERE command_key=%s", (key,)).fetchall()
        assert len(p) == count
        if count:
            intents = c.execute(
                "SELECT * FROM g3_intent WHERE plan_id=%s", (p[0]["id"],)
            ).fetchall()
            assert len(intents) == 2
            for i in intents:
                assert (
                    c.execute(
                        "SELECT count(*) AS n FROM outbox_event WHERE aggregate_id=%s AND event_type='g3.intent.created'",
                        (i["id"],),
                    ).fetchone()["n"]
                    == 1
                )


def test_g3_remote_commit_then_real_worker_death(client, settings):
    b = prepared(client, settings)
    p = publish(client, settings, b)
    db = client.app.state.db
    tenant = uid("tenant-A")
    with db.transaction(tenant) as c:
        task = c.execute(
            "SELECT t.* FROM g3_task t JOIN g3_intent i ON i.id=t.intent_id JOIN g3_listing l ON l.id=i.listing_id JOIN g3_account a ON a.id=l.account_id WHERE i.plan_id=%s AND a.code='SIM-A' AND t.chunk_index=0",
            (p["id"],),
        ).fetchone()
        engine.schedule(c, tenant, task["id"])
    r = child("remote", task["id"])
    assert r.returncode == 83, r.stderr[-300:]
    with db.transaction(tenant) as c:
        assert get(c, "g3_task", task["id"])["state"] == "SENDING"
        c.execute(
            "UPDATE execution_job SET lease_until=clock_timestamp()-interval '1 second' WHERE resource_id=%s AND kind='G3_DELIVER'",
            (task["id"],),
        )
    r = child("recover", task["id"])
    assert r.returncode == 0, r.stderr[-300:]
    with db.transaction(tenant) as c:
        current = get(c, "g3_task", task["id"])
        assert (
            current["state"] == "APPLIED"
            and current["send_count"] == 1
            and current["query_count"] == 1
        )
    drain(
        client,
        settings,
        lambda: (
            ok(client.get("/v3/plans/" + p["id"], headers=auth(settings)))["state"] == "VERIFIED"
        ),
    )


def test_g3_relay_crash_preserves_jobs_exactly_once(client, settings):
    b = prepared(client, settings)
    p = publish(client, settings, b)
    db = client.app.state.db
    tenant = uid("tenant-A")
    with db.transaction(tenant) as c:
        event = c.execute(
            "SELECT e.* FROM outbox_event e JOIN g3_intent i ON i.id=e.aggregate_id WHERE i.plan_id=%s AND e.event_type='g3.intent.created' LIMIT 1",
            (p["id"],),
        ).fetchone()
    assert child("relay", event["id"]).returncode == 84
    engine.relay(db, tenant, event_id=event["id"])
    with db.transaction(tenant) as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM consumer_inbox WHERE event_id=%s AND consumer='g3-delivery-v1'",
                (event["id"],),
            ).fetchone()["n"]
            == 1
        )
        assert get(c, "outbox_event", event["id"])["delivered_at"] is not None
        tasks = c.execute(
            "SELECT id FROM g3_task WHERE intent_id=%s", (event["aggregate_id"],)
        ).fetchall()
        for t in tasks:
            assert (
                c.execute(
                    "SELECT count(*) AS n FROM execution_job WHERE kind='G3_DELIVER' AND resource_id=%s",
                    (t["id"],),
                ).fetchone()["n"]
                == 1
            )


@pytest.mark.parametrize("mode", ["after_commit", "lost_operation", "429", "500"])
def test_g3_unknown_and_retry_classification(client, settings, mode):
    b = prepared(client, settings)
    key = b["snapshots"][1]["listing_key"]
    fault(settings, "SIM-B", key, mode, arg="1")
    p = publish(client, settings, b)
    drain(
        client,
        settings,
        lambda: (
            ok(client.get("/v3/plans/" + p["id"], headers=auth(settings)))["state"] == "VERIFIED"
        ),
        seconds=35,
    )
    result = ok(client.get("/v3/plans/" + p["id"], headers=auth(settings)))
    task = result["branches"][1]["tasks"][0]
    if mode in {"after_commit", "lost_operation"}:
        assert task["send_count"] == 1 and task["query_count"] >= 1
    else:
        assert task["send_count"] == 2


@pytest.mark.parametrize("mode", ["partial_temp", "partial_permanent"])
def test_g3_partial_cell_results(client, settings, mode):
    b = prepared(client, settings)
    p = publish(client, settings, b)

    def current():
        return ok(client.get("/v3/plans/" + p["id"], headers=auth(settings)))

    drain(client, settings, lambda: current()["branches"][1]["tasks"][0]["state"] == "APPLIED")
    fault(settings, "SIM-B", b["snapshots"][1]["listing_key"], mode)
    if mode == "partial_temp":
        drain(client, settings, lambda: current()["state"] == "VERIFIED", seconds=35)
    else:
        drain(client, settings, lambda: current()["branches"][1]["state"] == "BLOCKED")
        assert current()["state"] == "PARTIAL"
    t = current()["branches"][1]["tasks"][1]
    assert t["state"] == "PARTIAL" and t["send_count"] == 1
    assert sum(r["state"] == "APPLIED" for r in t["cell_results"]) == 5
    assert sum(r["state"] == "REJECTED" for r in t["cell_results"]) == 1


def test_g3_old_http_actually_executes_after_new_close(client, settings):
    b, p = verified(client, settings)
    branch = p["branches"][0]
    key = b["snapshots"][0]["listing_key"]
    old = branch["tasks"][0]["body"]
    # This test sends only to the independent simulator; the core does not mutate remote tables.
    fault(settings, "SIM-A", key, "hold")

    def delayed():
        with httpx.Client(trust_env=False, timeout=20) as h:
            return h.put(
                "http://127.0.0.1:8001/g3/listings/" + key,
                headers={
                    **transport.headers(uid("tenant-A"), "SIM-A"),
                    "Idempotency-Key": "late-" + str(uuid4()),
                },
                json={**old, "revision": old["revision"] + 1},
            )

    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(delayed)
        # Wait until the server consumed the barrier fault, bounded by its persistent operation.
        deadline = time.monotonic() + 5
        with httpx.Client(trust_env=False) as observer:
            while time.monotonic() < deadline:
                barrier = ok(
                    observer.get(
                        "http://127.0.0.1:8001/g3/control/" + key,
                        headers={
                            **transport.headers(uid("tenant-A"), "SIM-A"),
                            "X-Control-Token": os.environ["G3_CONTROL_TOKEN"],
                        },
                    )
                )
                if barrier.get("remaining") == 0:
                    break
                time.sleep(0.02)
            else:
                raise AssertionError("Old HTTP did not reach execution barrier")
        from services.core.g3.contract import close_payload

        closed = close_payload(
            b["snapshots"][0]["payload"], ["2027-01-10", "2027-01-11", "2027-01-12"]
        )
        manifest = {
            **old["manifest"],
            "payload_hash": digest(closed),
            "chunks": {"1": digest(closed["cells"])},
        }
        high = {
            **old,
            "revision": old["revision"] + 10,
            "manifest": manifest,
            "manifest_hash": digest(manifest),
        }
        response = transport.request(
            uid("tenant-A"),
            "SIM-A",
            "PUT",
            transport.listing_path(key),
            high,
            "new-close-content-" + str(uuid4()),
        )
        assert response.status_code == 200
        close_body = {
            **branch["tasks"][1]["body"],
            "revision": high["revision"],
            "action": "STOP",
            "manifest_hash": digest(manifest),
            "cells": closed["cells"],
        }
        closed_response = transport.request(
            uid("tenant-A"),
            "SIM-A",
            "PUT",
            transport.listing_path(key),
            close_body,
            "new-close-cells-" + str(uuid4()),
        )
        assert closed_response.status_code == 200
        fault(settings, "SIM-A", key, "release")
        result = pending.result(timeout=10)
    assert result.status_code == 200 and result.json()["result"]["code"] == "STALE_REVISION"
    observed = remote("SIM-A", key).json()
    assert observed["revision"] == high["revision"] and all(
        not x["value"]["sellable"] for x in observed["cells"].values()
    )


@pytest.mark.parametrize("mode", ["read_failure", "incomplete"])
def test_g3_incomplete_read_is_unknown_not_missing(client, settings, mode):
    b, p = verified(client, settings)
    lid = p["branches"][1]["listing_id"]
    key = b["snapshots"][1]["listing_key"]
    fault(settings, "SIM-B", key, mode, remaining=100)
    try:
        r = engine.reconcile(client.app.state.db, uid("tenant-A"), lid)
        assert r["classification"] == "UNKNOWN"
    finally:
        fault(settings, "SIM-B", key, "clear")


def test_g3_remote_ahead_never_blindly_repaired(client, settings):
    b, p = verified(client, settings)
    lid = p["branches"][1]["listing_id"]
    fault(settings, "SIM-B", b["snapshots"][1]["listing_key"], "remote_ahead")
    r = engine.reconcile(client.app.state.db, uid("tenant-A"), lid)
    assert r["classification"] == "REMOTE_AHEAD"
    li = listing(client, settings, lid)
    assert (
        client.post(
            "/v3/diffs/" + r["diff_id"] + "/repair",
            headers=auth(settings),
            json={"expected_version": li["version"]},
        ).status_code
        == 409
    )
