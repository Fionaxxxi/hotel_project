from uuid import uuid4

import psycopg
import pytest

from infra.manage import uid
from services.core.g2 import formats, jobs, workflow
from tests.g1_helpers import auth, ok
from tests.g2_helpers import activate, files, parse, ready, start_run


@pytest.mark.parametrize(
    "fmt,kind,field,value,code",
    [
        ("xlsx", "rooms", "room_code", 123, "CODE_MUST_BE_TEXT"),
        ("xlsx", "rates", "net_amount", "=1+1", "FORMULA_FORBIDDEN"),
        ("json", "rates", "net_amount", 123.45, "JSON_AMOUNT_MUST_BE_TEXT"),
        ("json", "rooms", "room_code", 123, "CODE_MUST_BE_TEXT"),
        ("csv", "inventory", "quantity", "-1", "INVALID_INTEGER"),
        ("json", "rate_plans", "operation", "delete", "MASTER_DELETE_NOT_SUPPORTED"),
    ],
)
def test_bad_types_are_located_and_blocked(client, settings, fmt, kind, field, value, code):
    def edit(data, m):
        data[kind][0][field] = value

    run = ready(client, settings, fmt=fmt, mode="delta", edit=edit)
    result = ok(client.get(f"/v2/processing-runs/{run['id']}", headers=auth(settings)))
    issues = ok(client.get(f"/v2/processing-runs/{run['id']}/issues", headers=auth(settings)))[
        "items"
    ]
    assert result["status"] == "QUALITY_REVIEW"
    assert any(code in i["code"] and i["location"] for i in issues)


def test_code_leading_zero_is_preserved():
    row = {
        "property_code": "001",
        "room_code": "0002",
        "name": "Room",
        "bed_type": "KING",
        "max_adults": "2",
    }
    normalized = formats.normalize("rooms", row, "json", "snapshot")
    assert normalized["property_code"] == "001" and normalized["room_code"] == "0002"


def test_manifest_authority_and_unknown_stream(client, settings):
    for change in ["authority", "stream"]:

        def edit(data, m):
            if change == "authority":
                m["scope"]["authority"] = {}
            else:
                m["sequence_stream"] = "not-configured"

        response = client.post("/v2/imports", headers=auth(settings), files=files(edit=edit))
        assert response.status_code == 422


def test_sequence_idempotency_and_partial_remainder(client, settings):
    code = "ORDER" + uuid4().hex[:8]
    run = ready(client, settings, code=code, sequence=5)
    current = ok(client.get(f"/v2/processing-runs/{run['id']}", headers=auth(settings)))
    body = {
        "expected_versions": current["expected_versions"],
        "expected_run_version": current["version"],
        "selected_dates": ["2027-01-10"],
        "partial_confirmed": True,
    }
    headers = {**auth(settings), "Idempotency-Key": str(uuid4())}
    path = f"/v2/processing-runs/{run['id']}/activate"
    first = ok(client.post(path, headers=headers, json=body))
    assert ok(client.post(path, headers=headers, json=body))["id"] == first["id"]
    changed = {**body, "selected_dates": ["2027-01-11"]}
    assert client.post(path, headers=headers, json=changed).status_code == 409
    # Independent unactivated dates can follow without replaying MASTER.
    activate(client, settings, run, selected=["2027-01-11", "2027-01-12"])
    for seq in [4, 5]:
        other = ready(client, settings, code=code, sequence=seq, mode="delta", only=["rates"])
        current = ok(client.get(f"/v2/processing-runs/{other['id']}", headers=auth(settings)))
        response = client.post(
            f"/v2/processing-runs/{other['id']}/activate",
            headers={**auth(settings), "Idempotency-Key": str(uuid4())},
            json={
                **body,
                "expected_versions": current["expected_versions"],
                "expected_run_version": current["version"],
            },
        )
        assert response.status_code == 409


def test_snapshot_missing_authoritative_row_tombstones_only_it(client, settings):
    code = "SNAP" + uuid4().hex[:8]
    activate(client, settings, ready(client, settings, code=code))

    def edit(data, m):
        data["rates"] = data["rates"][1:]

    run = ready(client, settings, code=code, sequence=2, edit=edit)
    gen = activate(client, settings, run)
    with client.app.state.db.transaction(uid("tenant-A")) as c:
        rows = c.execute(
            "SELECT * FROM generation_fact WHERE generation_id=%s AND tombstone", (gen["id"],)
        ).fetchall()
        assert len(rows) == 1 and rows[0]["kind"] == "rates"


def test_workflow_pause_cancel_and_budget(client, settings):
    _, run = start_run(client, settings)
    db = client.app.state.db
    tenant = uid("tenant-A")
    with db.transaction(tenant) as c:
        from services.core.g1.common import get

        wf = workflow.ensure(c, tenant, get(c, "processing_run", run["id"]))
    path = f"/v2/workflows/{wf['id']}/commands"
    wf = ok(
        client.post(
            path,
            headers=auth(settings),
            json={"action": "pause", "expected_version": wf["version"]},
        )
    )
    with db.transaction(tenant) as c:
        assert workflow.advance(c, wf["id"])["paused"] is True
    wf = ok(
        client.post(
            path,
            headers=auth(settings),
            json={"action": "cancel", "expected_version": wf["version"]},
        )
    )
    assert wf["state"] == "CANCELLED"
    assert (
        client.post(
            path,
            headers=auth(settings),
            json={"action": "resume", "expected_version": wf["version"]},
        ).status_code
        == 409
    )
    # Complete this unrelated parse so it does not occupy the tenant quota.
    parse(client, settings, run)
    with db.transaction(tenant) as c:
        job = jobs.enqueue(c, tenant, "BUDGET", uuid4(), str(uuid4()))
        c.execute("UPDATE execution_job SET max_attempts=1 WHERE id=%s", (job["id"],))
        claimed = jobs.claim(c, "budget-owner", kinds=["BUDGET"], resource=job["resource_id"])
        jobs.finish(c, claimed, "RETRY_WAIT", "TEST_FAILURE")
        result = c.execute("SELECT * FROM execution_job WHERE id=%s", (job["id"],)).fetchone()
        assert result["state"] == "MANUAL"
    assert (
        client.post(
            f"/v2/jobs/{job['id']}/retry", headers=auth(settings, "auditor"), json={}
        ).status_code
        == 403
    )
    ok(client.post(f"/v2/jobs/{job['id']}/retry", headers=auth(settings), json={}))
    with db.transaction(tenant) as c:
        retried = c.execute("SELECT * FROM execution_job WHERE id=%s", (job["id"],)).fetchone()
        assert retried["logical_key"] == job["logical_key"] and retried["attempt"] == 0


def test_new_tables_force_rls_and_immutable_event(client, settings):
    bid, run = start_run(client, settings)
    parse(client, settings, run)
    activate(client, settings, run)
    db = client.app.state.db
    tables = [
        "processing_run",
        "processing_record",
        "parse_checkpoint",
        "dq_issue",
        "source_generation",
        "generation_fact",
        "source_head",
        "stable_partition",
        "activation_command",
        "outbox_event",
        "consumer_inbox",
        "workflow_run",
        "workflow_step",
        "workflow_wait",
        "execution_job",
        "job_attempt",
        "generation_activation",
    ]
    with db.transaction(uid("tenant-A")) as c:
        for name in tables:
            policy = c.execute(
                "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE relname=%s", (name,)
            ).fetchone()
            assert policy["relrowsecurity"] and policy["relforcerowsecurity"]
        eid = jobs.emit(c, uid("tenant-A"), "test.guard", uuid4(), 1, {})
    with db.transaction(uid("tenant-B")) as c:
        for name in tables:
            assert (
                c.execute(
                    f"SELECT count(*) AS n FROM {name} WHERE tenant_id=%s", (uid("tenant-A"),)
                ).fetchone()["n"]
                == 0
            )
    with pytest.raises(psycopg.Error):
        with db.transaction(uid("tenant-A")) as c:
            c.execute(
                "UPDATE outbox_event SET payload='{}',aggregate_version=2 WHERE id=%s", (eid,)
            )


def test_lane_filter_and_tenant_fairness(client, settings):
    from services.core.g2.worker import tick

    db = client.app.state.db
    identifiers = {}
    for label in ["A", "B"]:
        with db.transaction(uid("tenant-" + label)) as c:
            identifiers[label] = jobs.enqueue(
                c, uid("tenant-" + label), "FAIR_TEST", uuid4(), str(uuid4())
            )["id"]
    tick(db, "fair-worker", kinds=["FAIR_TEST"])
    for label in ["A", "B"]:
        with db.transaction(uid("tenant-" + label)) as c:
            assert (
                c.execute(
                    "SELECT state FROM execution_job WHERE id=%s", (identifiers[label],)
                ).fetchone()["state"]
                == "BLOCKED"
            )


def test_backpressure_limit_is_explained(client, settings):
    db = client.app.state.db
    tenant = uid("tenant-A")
    extra = []
    with db.transaction(tenant) as c:
        for _ in range(8):
            extra.append(jobs.enqueue(c, tenant, "PARSE", uuid4(), str(uuid4()))["id"])
    try:
        bid = ok(client.post("/v2/imports", headers=auth(settings), files=files()))["id"]
        response = client.post(f"/v2/imports/{bid}/runs", headers=auth(settings), json={})
        assert (
            response.status_code == 429
            and response.json()["code"] == "TENANT_PROCESSING_QUEUE_FULL"
        )
    finally:
        with db.transaction(tenant) as c:
            c.execute("UPDATE execution_job SET state='BLOCKED' WHERE id=ANY(%s)", (extra,))


def test_parse_budget_exhaustion_wakes_manual_workflow(client, settings, monkeypatch):
    from services.core.g1.common import get
    from services.core.g2 import processing
    from services.core.g2.worker import execute

    _, run = start_run(client, settings)
    db = client.app.state.db
    tenant = uid("tenant-A")
    with db.transaction(tenant) as c:
        c.execute(
            "UPDATE execution_job SET max_attempts=1 WHERE resource_id=%s AND kind='PARSE'",
            (run["id"],),
        )
        job = jobs.claim(c, "fault-budget", kinds=["PARSE"], resource=run["id"])

    def fail_parse(*args, **kwargs):
        raise RuntimeError("parser unavailable")

    monkeypatch.setattr(processing, "process", fail_parse)
    execute(db, job)
    with db.transaction(tenant) as c:
        assert get(c, "processing_run", run["id"])["status"] == "MANUAL"
        assert get(c, "execution_job", job["id"])["state"] == "MANUAL"
        event = c.execute(
            "SELECT * FROM outbox_event WHERE aggregate_id=%s AND event_type='processing.completed'",
            (run["id"],),
        ).fetchone()
        assert event["payload"]["status"] == "MANUAL"
        workflow.consume(c, event)
        wf = c.execute("SELECT * FROM workflow_run WHERE processing_id=%s", (run["id"],)).fetchone()
        assert workflow.advance(c, wf["id"])["state"] == "BLOCKED"
