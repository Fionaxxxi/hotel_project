import os
import time
from uuid import UUID, uuid4

import httpx

from infra.manage import uid
from services.core.g3 import engine, transport
from tests.g1_helpers import approve_mappings, auth, ok
from tests.g2_helpers import activate, ready


def prepared(client, settings, amounts=None, buffer=1, plan_codes=None, approve=True, **kwargs):
    selected = kwargs.pop("selected_dates", None)
    run = ready(client, settings, **kwargs)
    gen = activate(client, settings, run, selected=selected)
    approve_mappings(client, settings, gen["activation_id"])
    adopt = ok(client.post(f"/v3/sources/{gen['activation_id']}/adopt", headers=auth(settings)))
    assert adopt["legacy_state"] == "CLEAR"
    allocation = ok(
        client.post(
            "/v3/allocations",
            headers=auth(settings),
            json={
                "activation_id": gen["activation_id"],
                "expected_version": 0,
                "amounts": amounts or {"SIM-A": 2, "SIM-B": 1},
                "buffer": buffer,
            },
        )
    )
    snapshots = []
    for channel in ["SIM-A", "SIM-B"]:
        s = ok(
            client.post(
                "/v3/snapshots",
                headers=auth(settings),
                json={
                    "allocation_id": allocation["id"],
                    "channel": channel,
                    "plan_codes": plan_codes or ["NONREF"],
                },
            )
        )
        if approve:
            s = ok(
                client.post(
                    f"/v3/snapshots/{s['id']}/decisions",
                    headers=auth(settings, "reviewer"),
                    json={
                        "expected_version": s["version"],
                        "decision": "APPROVED",
                        "reason": "G3 independent exact target review",
                        "allow_repair": True,
                    },
                )
            )
        snapshots.append(s)
    return {"run": run, "gen": gen, "allocation": allocation, "snapshots": snapshots}


def publish(client, settings, bundle, expected=None, reopen=False):
    result = ok(
        client.post(
            "/v3/plans",
            headers={**auth(settings), "Idempotency-Key": str(uuid4())},
            json={
                "snapshot_ids": [s["id"] for s in bundle["snapshots"]],
                "expected_revisions": expected or {s["account_id"]: 0 for s in bundle["snapshots"]},
                "reopen": reopen,
            },
        )
    )
    client.app.state.db.g3_assigned_listings = list(
        set(getattr(client.app.state.db, "g3_assigned_listings", []))
        | {UUID(b["listing_id"]) for b in result["branches"]}
    )
    return result


def drain(client, settings, predicate, seconds=25):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        engine.tick(
            client.app.state.db,
            "g3-test-worker",
            listing_ids=getattr(client.app.state.db, "g3_assigned_listings", None),
        )
        time.sleep(0.03)
    raise AssertionError("G3 durable state did not converge")


def verified(client, settings, bundle=None):
    bundle = bundle or prepared(client, settings)
    plan = publish(client, settings, bundle)

    def current():
        return ok(client.get("/v3/plans/" + plan["id"], headers=auth(settings)))

    drain(client, settings, lambda: current()["state"] == "VERIFIED")
    return bundle, current()


def listing(client, settings, lid):
    return ok(client.get("/v3/listings/" + str(lid), headers=auth(settings)))


def fault(settings, channel, key, mode, **kwargs):
    with httpx.Client(
        base_url=f"http://127.0.0.1:{8001 if channel == 'SIM-A' else 8002}",
        trust_env=False,
        timeout=3,
    ) as h:
        return ok(
            h.post(
                "/g3/control/" + key,
                headers={
                    **transport.headers(uid("tenant-A"), channel),
                    "X-Control-Token": os.environ["G3_CONTROL_TOKEN"],
                },
                json={"mode": mode, **kwargs},
            )
        )


def remote(channel, key, tenant=None):
    return transport.request(tenant or uid("tenant-A"), channel, "GET", transport.listing_path(key))
