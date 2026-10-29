"""A real HTTP request delayed after its durable dispatch permit, then policy pause."""

import threading
import time
from uuid import UUID

from infra.manage import uid
from services.core.g3 import engine, transport
from tests.g1_helpers import auth, ok
from tests.test_g4 import configured, tick, update


def test_pause_after_dispatch_permit_cannot_retract_inflight_http(client, settings, monkeypatch):
    bundle, p, sub = configured(client, settings)
    update(client, settings, bundle)
    run = tick(client, sub)
    plan = ok(client.get("/v3/plans/" + run["plan_id"], headers=auth(settings)))
    lids = [UUID(b["listing_id"]) for b in plan["branches"]]
    entered = threading.Event()
    release = threading.Event()
    errors = []
    sent = []
    tick_durations = []
    original = transport.request

    def held(tenant, channel, method, path, body=None, key=None):
        if channel == "SIM-A" and method == "PUT" and body and body["action"] == "PUBLISH":
            entered.set()
            if not release.wait(12):
                raise RuntimeError("Race test did not release permitted HTTP")
            response = original(tenant, channel, method, path, body, key)
            sent.append((response.status_code, key))
            return response
        return original(tenant, channel, method, path, body, key)

    monkeypatch.setattr(transport, "request", held)

    def work():
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                started = time.monotonic()
                engine.tick(client.app.state.db, "g4-permit-race", listing_ids=lids)
                tick_durations.append(round(time.monotonic() - started, 3))
                if entered.is_set():
                    break
                time.sleep(0.03)
        except Exception as exc:
            errors.append(type(exc).__name__)

    thread = threading.Thread(target=work)
    thread.start()
    try:
        reached = entered.wait(15)
        if not reached:
            with client.app.state.db.transaction(uid("tenant-A")) as c:
                diagnostics = c.execute("SELECT t.state,t.error,j.kind,j.state AS job_state,j.error AS job_error FROM g3_task t JOIN g3_intent i ON i.id=t.intent_id LEFT JOIN execution_job j ON j.resource_id=t.id WHERE i.listing_id=ANY(%s)", (lids,)).fetchall()
            raise AssertionError(f"No actual permitted publish reached the barrier; ticks={tick_durations}; errors={errors}; tasks={diagnostics}")
        paused = ok(
            client.post(
                "/v4/policies/" + p["id"] + "/commands",
                headers=auth(settings, "tenant_admin"),
                json={
                    "action": "pause",
                    "expected_version": p["version"],
                    "reason": "Pause while real HTTP already has a committed dispatch permit",
                },
            )
        )
        assert paused["state"] == "PAUSED"
        release.set()
        thread.join(timeout=15)
        assert not thread.is_alive() and not errors
        assert sent and sent[0][0] == 200
        with client.app.state.db.transaction(uid("tenant-A")) as c:
            task = c.execute(
                "SELECT state,send_count FROM g3_task WHERE request_key=%s", (sent[0][1],)
            ).fetchone()
            assert task["state"] == "APPLIED" and task["send_count"] == 1
        # This request was permitted before pause. Pausing cannot be presented as remote cancellation.
        key = bundle["snapshots"][0]["listing_key"]
        remote = original(uid("tenant-A"), "SIM-A", "GET", transport.listing_path(key))
        assert remote.status_code == 200 and remote.json()["revision"] >= 1
    finally:
        release.set()
        thread.join(timeout=15)
