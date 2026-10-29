import os
from uuid import uuid4

import httpx
import psycopg
import pytest

from infra.manage import uid
from services.core.g1.distribution import dispatch_one, query_remote
from tests.g1_helpers import approve_mappings, approved_snapshot, auth, import_active, ok, package


def test_upload_limits_and_delta(client, settings):
    files = package(sequence=1)
    files[-1] = ("files", (files[-1][1][0], b"a" * (5 * 1024 * 1024 + 1), "text/csv"))
    assert client.post("/v1/imports", headers=auth(settings), files=files).status_code == 413
    delta = package(sequence=1, edit=lambda c, m: m.update(mode="delta"))
    assert client.post("/v1/imports", headers=auth(settings), files=delta).status_code == 422
    assert client.post(
        "/v1/imports", headers=auth(settings), files=package() + package()[:1]
    ).status_code in {400, 422}


def test_incomplete_calendar(client, settings):
    def edit(content, manifest):
        lines = content["rates.csv"].decode().splitlines()
        content["rates.csv"] = ("\n".join(lines[:-1]) + "\n").encode()

    bid = ok(
        client.post("/v1/imports", headers=auth(settings), files=package(sequence=1, edit=edit))
    )["id"]
    result = ok(
        client.post(
            f"/v1/imports/{bid}/confirm", headers=auth(settings), json={"expected_version": 1}
        )
    )
    assert result["status"] == "INVALID"
    assert "INCOMPLETE_RATE_CALENDAR" in {i["code"] for i in result["issues"]}


def test_same_sequence_conflict_and_overlap(client, settings):
    code = "S" + uuid4().hex[:8]
    import_active(client, settings, sequence=5, property_code=code)
    for change in ["content", "overlap"]:

        def edit(c, m):
            if change == "content":
                c["rates.csv"] = c["rates.csv"].replace(b"100.00", b"101.00")
            else:
                m["scope"]["stay_date_from"] = "2027-01-09"
                # Shift all three days to keep this overlapping package complete.
                m["scope"]["stay_date_to_exclusive"] = "2027-01-12"
                for key in ["rates.csv", "inventory.csv"]:
                    c[key] = (
                        c[key]
                        .replace(b"2027-01-10", b"2027-01-09")
                        .replace(b"2027-01-11", b"2027-01-10")
                        .replace(b"2027-01-12", b"2027-01-11")
                    )

        bid = ok(
            client.post(
                "/v1/imports",
                headers=auth(settings),
                files=package(
                    sequence=5 if change == "content" else 6, property_code=code, edit=edit
                ),
            )
        )["id"]
        r = ok(
            client.post(
                f"/v1/imports/{bid}/confirm", headers=auth(settings), json={"expected_version": 1}
            )
        )
        assert r["status"] == "READY"
        assert (
            client.post(
                f"/v1/imports/{bid}/activate",
                headers=auth(settings),
                json={"expected_version": r["version"]},
            ).status_code
            == 409
        )


def test_mapping_hard_conflicts_and_optimistic_version(client, settings):
    _, aid = import_active(client, settings, sequence=1)
    approve_mappings(client, settings, aid)
    sources = ok(
        client.get("/v1/match-proposals", headers=auth(settings), params={"activation_id": aid})
    )["items"]
    for kind in ["room", "rate"]:
        source = next(s for s in sources if s["kind"] == kind)
        wrong = next(c for c in source["candidates"] if c["hard_conflicts"])
        data = {
            "target_id": wrong["target_id"],
            "decision": "APPROVED",
            "reason": "Must reject conflict",
            "expected_version": source["mapping"]["version"],
        }
        response = client.post(
            f"/v1/mappings/{kind}/{source['id']}/decisions",
            headers=auth(settings, "reviewer"),
            json=data,
        )
        assert response.status_code == 422
        data["expected_version"] = 0
        assert (
            client.post(
                f"/v1/mappings/{kind}/{source['id']}/decisions",
                headers=auth(settings, "reviewer"),
                json=data,
            ).status_code
            == 409
        )
    assert (
        client.post(
            f"/v1/mappings/property/{sources[0]['id']}/decisions",
            headers=auth(settings, tenant="B", role="reviewer"),
            json={
                "target_id": str(uid("property-B")),
                "decision": "APPROVED",
                "reason": "Cross tenant",
                "expected_version": 0,
            },
        ).status_code
        == 404
    )


@pytest.mark.parametrize("condition", ["stop_sell", "unknown", "real_clock"])
def test_unsellable_sources(client, settings, monkeypatch, condition):
    def edit(c, m):
        if condition == "stop_sell":
            c["inventory.csv"] = c["inventory.csv"].replace(b",false,", b",true,", 1)
        if condition == "unknown":
            c["rate_plans.csv"] = c["rate_plans.csv"].replace(b"NET_EXCLUSIVE", b"UNKNOWN")

    _, aid = import_active(client, settings, sequence=1, edit=edit)
    if condition != "unknown":
        approve_mappings(client, settings, aid)
    if condition == "real_clock":
        monkeypatch.setenv("DEMO_CLOCK_ENABLED", "false")
    response = client.post(
        "/v1/offers/compile", headers=auth(settings), json={"activation_id": aid}
    )
    assert response.status_code == 409


def test_self_approval_and_stale_source(client, settings):
    code = "A" + uuid4().hex[:9]
    _, aid = import_active(client, settings, sequence=1, property_code=code)
    approve_mappings(client, settings, aid)
    snapshot = ok(
        client.post(
            "/v1/offers/compile",
            headers=auth(settings, "tenant_admin"),
            json={"activation_id": aid},
        )
    )
    duplicate = ok(
        client.post("/v1/offers/compile", headers=auth(settings), json={"activation_id": aid})
    )
    assert duplicate["id"] == snapshot["id"]
    snapshot = ok(
        client.post(
            f"/v1/snapshots/{snapshot['id']}/submit",
            headers=auth(settings, "tenant_admin"),
            json={"expected_version": snapshot["version"]},
        )
    )
    decision = {
        "expected_version": snapshot["version"],
        "decision": "APPROVED",
        "reason": "Independent approval",
    }
    assert (
        client.post(
            f"/v1/snapshots/{snapshot['id']}/decisions",
            headers=auth(settings, "tenant_admin"),
            json=decision,
        ).status_code
        == 403
    )
    snapshot = ok(
        client.post(
            f"/v1/snapshots/{snapshot['id']}/decisions",
            headers=auth(settings, "reviewer"),
            json=decision,
        )
    )
    _, newaid = import_active(client, settings, sequence=2, property_code=code)
    # Exact source facts reuse prior approvals; the old snapshot still cannot publish.
    source = ok(
        client.get("/v1/match-proposals", headers=auth(settings), params={"activation_id": newaid})
    )["items"]
    assert all(s["mapping"] and s["mapping"]["actor"] == "system.mapping-reuse" for s in source)
    response = client.post(
        "/v1/publish-plans",
        headers={**auth(settings), "Idempotency-Key": str(uuid4())},
        json={"snapshot_id": snapshot["id"]},
    )
    assert response.status_code == 409


def test_expired_snapshot_and_immutable_payload(client, settings):
    _, aid = import_active(client, settings, sequence=1)
    snapshot = approved_snapshot(client, settings, aid)
    with client.app.state.db.transaction(uid("tenant-A")) as c:
        with pytest.raises(psycopg.errors.RaiseException):
            with c.transaction():
                c.execute(
                    "UPDATE offer_snapshot SET payload_hash=%s WHERE id=%s",
                    ("0" * 64, snapshot["id"]),
                )
        c.execute(
            "UPDATE offer_snapshot SET expires_at=now()-interval '1 minute' WHERE id=%s",
            (snapshot["id"],),
        )
    assert (
        client.post(
            "/v1/publish-plans",
            headers={**auth(settings), "Idempotency-Key": str(uuid4())},
            json={"snapshot_id": snapshot["id"]},
        ).status_code
        == 409
    )
    fresh = ok(
        client.post("/v1/offers/compile", headers=auth(settings), json={"activation_id": aid})
    )
    assert fresh["revision"] > snapshot["revision"]
    assert fresh["status"] == "COMPILED"


def test_simulator_idempotency_revision_and_account(client, settings):
    _, aid = import_active(client, settings, sequence=1)
    snapshot = approved_snapshot(client, settings, aid)
    key = str(uuid4())
    payload = {
        "revision": snapshot["revision"],
        "payload_hash": snapshot["payload_hash"],
        "payload": snapshot["payload"],
    }
    with httpx.Client(base_url="http://127.0.0.1:8001", trust_env=False) as remote:
        h = {"Authorization": "Bearer " + os.environ["SIM_A_TOKEN"], "Idempotency-Key": key}
        path = "/v1/listings/" + snapshot["listing_key"]
        first = remote.put(path, json=payload, headers=h)
        assert first.status_code == 200, first.text
        second = remote.put(path, json=payload, headers=h)
        assert first.json()["id"] == second.json()["id"]
        assert (
            remote.put(
                path, json={**payload, "revision": payload["revision"] + 1}, headers=h
            ).status_code
            == 409
        )
        assert remote.put(
            path,
            json={**payload, "revision": payload["revision"] - 1},
            headers={**h, "Idempotency-Key": str(uuid4())},
        ).status_code in {409, 422}
        assert (
            remote.get(
                path, headers={"Authorization": "Bearer " + os.environ["SIM_B_TOKEN"]}
            ).status_code
            == 404
        )


def test_unknown_after_remote_commit(client, settings):
    _, aid = import_active(client, settings, sequence=1)
    snapshot = approved_snapshot(client, settings, aid)
    task = ok(
        client.post(
            "/v1/publish-plans",
            headers={**auth(settings), "Idempotency-Key": str(uuid4())},
            json={"snapshot_id": snapshot["id"]},
        )
    )
    result = dispatch_one(client.app.state.db, uid("tenant-A"), task["id"], delay=True)
    assert result["status"] == "UNKNOWN", (
        "Run simulator with SIM_FAULTS_ENABLED=true for this fault test"
    )
    result = query_remote(client.app.state.db, uid("tenant-A"), task["id"])
    assert result["status"] == "VERIFIED"
    assert result["attempt"] == 1


def test_new_table_rls_and_database_separation(settings):
    with psycopg.connect(settings.database_url) as c:
        tables = c.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename IN ('import_batch','artifact','staging_record','source_activation','source_partition','supplier_entity_version','inventory_pool','inventory_cell','plan_pool_link','rate_cell','property_mapping','room_mapping','rate_mapping','mapping_decision','channel_account','channel_mapping','offer_snapshot','approval','publish_task','task_attempt','channel_receipt','command_idempotency')"
        ).fetchall()
        from psycopg import sql

        assert len(tables) == 22
        for (table,) in tables:
            assert (
                c.execute(
                    sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
                ).fetchone()[0]
                == 0
            )
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(
            settings.database_url.replace("/hotel_project", "/hotel_project_sim"), connect_timeout=3
        )


def test_two_suppliers_independent_sources(client, settings):
    ids = []
    for supplier in ["S-A", "S-B"]:
        _, aid = import_active(
            client, settings, sequence=1, edit=lambda c, m: m.update(supplier_code=supplier)
        )
        approve_mappings(client, settings, aid)
        sources = ok(
            client.get("/v1/match-proposals", headers=auth(settings), params={"activation_id": aid})
        )["items"]
        hotel = next(s for s in sources if s["kind"] == "property")
        ids.append(hotel["mapping"]["target_id"])
    assert ids[0] == ids[1]


def test_dispatcher_lock_and_interrupted_send(client, settings):
    import subprocess
    import sys

    with psycopg.connect(settings.database_url, autocommit=True) as lock:
        assert lock.execute("SELECT pg_try_advisory_lock(7140027)").fetchone()[0]
        process = subprocess.run(
            [sys.executable, "-s", "-m", "services.core.workers.dispatch", "--once"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert process.returncode != 0
        assert "already owns" in process.stderr
    _, aid = import_active(client, settings, sequence=1)
    snapshot = approved_snapshot(client, settings, aid)
    task = ok(
        client.post(
            "/v1/publish-plans",
            headers={**auth(settings), "Idempotency-Key": str(uuid4())},
            json={"snapshot_id": snapshot["id"]},
        )
    )
    with client.app.state.db.transaction(uid("tenant-A")) as c:
        c.execute("UPDATE publish_task SET status='SENDING',attempt=1 WHERE id=%s", (task["id"],))
    process = subprocess.run(
        [sys.executable, "-s", "-m", "services.core.workers.dispatch", "--once"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert process.returncode == 0
    current = ok(client.get("/v1/publish-tasks/" + task["id"], headers=auth(settings)))
    assert current["status"] == "UNKNOWN"
    assert current["attempt"] == 1
