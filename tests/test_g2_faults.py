import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
import pytest

from infra.manage import ROOT, uid
from services.core.g1.common import get
from services.core.g2 import jobs, views, workflow
from tests.g1_helpers import approved_snapshot, auth, ok
from tests.g2_helpers import activate, parse, ready, start_run


def child(operation, resource, *args):
    return subprocess.run(
        [sys.executable, "-s", "-m", "infra.g2_fault_child", operation, str(resource), *args],
        capture_output=True,
        text=True,
        timeout=25,
    )


def expire(c, resource):
    c.execute(
        "UPDATE execution_job SET lease_until=clock_timestamp()-interval '1 second' WHERE resource_id=%s AND state='RUNNING'",
        (resource,),
    )


def test_real_process_crash_checkpoint(client, settings):
    def big(data, m):
        data["rooms"] = [{**data["rooms"][0], "room_code": f"R{i:05}"} for i in range(1005)]

    _, run = start_run(client, settings, edit=big)
    assert child("parse-crash", run["id"]).returncode == 73
    with client.app.state.db.transaction(uid("tenant-A")) as c:
        count = c.execute(
            "SELECT count(*) AS n FROM processing_record WHERE run_id=%s", (run["id"],)
        ).fetchone()["n"]
        assert 0 < count < 1017
        assert (
            c.execute(
                "SELECT sum(position) AS n FROM parse_checkpoint WHERE run_id=%s", (run["id"],)
            ).fetchone()["n"]
            > 0
        )
        expire(c, run["id"])
    result = child("parse-recover", run["id"])
    assert result.returncode == 0, result.stderr[-500:]
    with client.app.state.db.transaction(uid("tenant-A")) as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM processing_record WHERE run_id=%s", (run["id"],)
            ).fetchone()["n"]
            == 1017
        )
        assert get(c, "processing_run", run["id"])["status"] == "READY_TO_ACTIVATE"


@pytest.mark.parametrize("window", ["before", "after"])
def test_real_activation_commit_windows(client, settings, window):
    run = ready(client, settings)
    current = ok(client.get("/v2/processing-runs/" + run["id"], headers=auth(settings)))
    body = {
        "expected_versions": current["expected_versions"],
        "expected_run_version": current["version"],
        "selected_dates": ["2027-01-10", "2027-01-11", "2027-01-12"],
        "partial_confirmed": False,
    }
    path = ROOT / ".runtime" / f"activation-fault-{uuid4()}.json"
    path.write_text(json.dumps({"key": str(uuid4()), "body": body}))
    try:
        result = child(
            "activate-before-commit" if window == "before" else "activate-after-commit",
            run["id"],
            "--body",
            str(path),
        )
        assert result.returncode == (75 if window == "before" else 76), result.stderr[-500:]
    finally:
        path.unlink()
    with client.app.state.db.transaction(uid("tenant-A")) as c:
        generations = c.execute(
            "SELECT count(*) AS n FROM source_generation WHERE run_id=%s", (run["id"],)
        ).fetchone()["n"]
        events = c.execute(
            "SELECT count(*) AS n FROM outbox_event WHERE aggregate_id=%s AND event_type='source.activated'",
            (run["id"],),
        ).fetchone()["n"]
        assert generations == events == (0 if window == "before" else 1)


def test_real_relay_crash_and_atomic_inbox(client, settings):
    _, run = start_run(client, settings)
    parse(client, settings, run)
    with client.app.state.db.transaction(uid("tenant-A")) as c:
        event = c.execute(
            "SELECT * FROM outbox_event WHERE aggregate_id=%s AND event_type='processing.completed'",
            (run["id"],),
        ).fetchone()
    result = child("relay-crash", event["id"])
    assert result.returncode == 74, result.stderr[-500:]
    with client.app.state.db.transaction(uid("tenant-A")) as c:
        assert get(c, "outbox_event", event["id"])["delivered_at"] is None
        assert (
            c.execute(
                "SELECT count(*) AS n FROM workflow_run WHERE processing_id=%s", (run["id"],)
            ).fetchone()["n"]
            == 1
        )
        c.execute(
            "UPDATE outbox_event SET next_run_at=clock_timestamp()-interval '1 second' WHERE id=%s",
            (event["id"],),
        )
    assert workflow.relay_one(client.app.state.db, uid("tenant-A"), event_id=event["id"])
    with client.app.state.db.transaction(uid("tenant-A")) as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM consumer_inbox WHERE event_id=%s", (event["id"],)
            ).fetchone()["n"]
            == 1
        )
        # Roll back a consumer transaction: no inbox and no workflow effect can escape.
        eid = jobs.emit(c, uid("tenant-A"), "processing.completed", run["id"], 2, {})
    with pytest.raises(RuntimeError):
        with client.app.state.db.transaction(uid("tenant-A")) as c:
            workflow.consume(c, get(c, "outbox_event", eid))
            raise RuntimeError("rollback before consumer commit")
    with client.app.state.db.transaction(uid("tenant-A")) as c:
        assert not c.execute("SELECT 1 FROM consumer_inbox WHERE event_id=%s", (eid,)).fetchone()


def test_real_remote_commit_then_worker_crash(client, settings):
    run = ready(client, settings)
    gen = activate(client, settings, run)
    snapshot = approved_snapshot(client, settings, gen["activation_id"])
    task = ok(
        client.post(
            "/v1/publish-plans",
            headers={**auth(settings), "Idempotency-Key": str(uuid4())},
            json={"snapshot_id": snapshot["id"]},
        )
    )
    result = child("publish-crash", task["id"])
    assert result.returncode == 77, result.stderr[-500:]
    with client.app.state.db.transaction(uid("tenant-A")) as c:
        assert get(c, "publish_task", task["id"])["status"] == "SENDING"
        expire(c, task["id"])
    result = child("publish-recover", task["id"])
    assert result.returncode == 0, result.stderr[-500:]
    state = ok(client.get("/v1/publish-tasks/" + task["id"], headers=auth(settings)))
    assert state["status"] == "VERIFIED" and state["attempt"] == 1
    with httpx.Client(base_url="http://127.0.0.1:8001", trust_env=False) as remote:
        assert (
            remote.get(
                "/v1/operations/" + task["idempotency_key"],
                headers={"Authorization": "Bearer " + os.environ["SIM_A_TOKEN"]},
            ).status_code
            == 200
        )


def test_two_process_fencing_barrier(client, settings):
    resource = uuid4()
    db = client.app.state.db
    tenant = uid("tenant-A")
    with db.transaction(tenant) as c:
        jobs.enqueue(c, tenant, "TEST_HOLD", resource, str(resource))
    marker = ROOT / ".runtime" / f"lease-{uuid4()}.json"
    process = subprocess.Popen(
        [
            sys.executable,
            "-s",
            "-m",
            "infra.g2_fault_child",
            "hold-lease",
            str(resource),
            "--marker",
            str(marker),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        deadline = time.monotonic() + 10
        while not marker.exists():
            assert process.poll() is None
            if time.monotonic() > deadline:
                raise AssertionError("claim barrier missing")
            time.sleep(0.02)
        with db.transaction(tenant) as c:
            assert jobs.claim(c, "parent-B", kinds=["TEST_HOLD"], resource=resource) is None
            expire(c, resource)
        with db.transaction(tenant) as c:
            newer = jobs.claim(c, "parent-B", kinds=["TEST_HOLD"], resource=resource)
            jobs.finish(c, newer)
        marker.with_suffix(".release").write_text("continue")
        assert process.wait(timeout=10) == 78
        with db.transaction(tenant) as c:
            row = c.execute(
                "SELECT * FROM execution_job WHERE resource_id=%s", (resource,)
            ).fetchone()
            assert row["state"] == "SUCCEEDED" and row["owner"] == "parent-B"
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        marker.unlink(missing_ok=True)
        marker.with_suffix(".release").unlink(missing_ok=True)


def test_concurrent_activation_and_disjoint_updates(client, settings):
    code = "CAS" + uuid4().hex[:8]
    base = ready(client, settings, code=code)
    activate(client, settings, base)
    first = ready(client, settings, code=code, sequence=2, mode="delta", only=["rates"])
    second = ready(client, settings, code=code, sequence=3, mode="delta", only=["rates"])
    barrier = threading.Barrier(2)
    db = client.app.state.db

    def submit(run):
        current = ok(client.get("/v2/processing-runs/" + run["id"], headers=auth(settings)))
        body = {
            "expected_versions": current["expected_versions"],
            "expected_run_version": current["version"],
            "selected_dates": ["2027-01-10"],
            "partial_confirmed": True,
        }
        barrier.wait()
        try:
            with db.transaction(uid("tenant-A"), "a.operator") as c:
                views.activate(
                    c,
                    {"tenant_id": uid("tenant-A"), "subject": "a.operator", "role": "operator"},
                    run["id"],
                    body,
                    str(uuid4()),
                )
            return 200
        except Exception as exc:
            from fastapi import HTTPException

            if isinstance(exc, HTTPException):
                return exc.status_code
            raise

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, [first, second]))
    assert sorted(results) == [200, 409]
    # Same old vector is valid for different untouched date partitions.
    a = ready(client, settings, code=code, sequence=4, mode="delta", only=["rates"])
    b = ready(client, settings, code=code, sequence=5, mode="delta", only=["rates"])
    for run, day in [(a, "2027-01-11"), (b, "2027-01-12")]:
        activate(client, settings, run, [day])
