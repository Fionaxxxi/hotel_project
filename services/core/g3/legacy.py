"""Explicit read/close migration for the old SIM-A bundle protocol."""

import copy

import httpx

from services.core.g1 import distribution, offers
from services.core.g1.common import audit, authorize, digest, j
from services.core.g3.domain import adopt, lock_source


def inspect(db, actor, aid, close=False):
    if close:
        authorize(actor, {"tenant_admin"})
    else:
        authorize(actor)
    with db.transaction(actor["tenant_id"]) as c:
        key = adopt(c, actor, aid)
        base = offers.compile_offer(c, actor, aid)
        control = c.execute(
            "SELECT * FROM g3_source_control WHERE source_key=%s", (key,)
        ).fetchone()
        maxrev = c.execute(
            "SELECT coalesce(max(revision),0) AS n FROM offer_snapshot WHERE channel_id=%s AND listing_key=%s",
            (base["channel_id"], base["listing_key"]),
        ).fetchone()["n"]
        pending = c.execute(
            "SELECT 1 FROM publish_task t JOIN offer_snapshot s ON s.id=t.snapshot_id WHERE s.activation_id=%s AND t.status IN ('SENDING','UNKNOWN','ACKNOWLEDGED') LIMIT 1",
            (aid,),
        ).fetchone()
    auth = distribution.credentials(actor["tenant_id"])
    payload = None
    revision = 0
    state = "UNKNOWN"
    with httpx.Client(
        base_url=distribution.SIM_URL, trust_env=False, follow_redirects=False, timeout=3
    ) as h:
        try:
            r = h.get("/v1/listings/" + base["listing_key"], headers=auth)
            if r.status_code == 200:
                remote = r.json()
                payload = remote["payload"]
                revision = remote["revision"]
                keys = {p["key"] for p in base["payload"]["pools"]}
                exposed = any(
                    d["quantity"] > 0
                    for p in payload["pools"]
                    if p["key"] in keys
                    for d in p["calendar"]
                )
                state = "EXPOSED" if exposed or pending else "CLEAR"
            elif r.status_code == 404:
                state = "EXPOSED" if pending else "CLEAR"
            if close and state == "EXPOSED":
                target = copy.deepcopy(payload or base["payload"])
                for p in target["pools"]:
                    if p["key"] in {p["key"] for p in base["payload"]["pools"]}:
                        for d in p["calendar"]:
                            d["quantity"] = 0
                with db.transaction(actor["tenant_id"]) as c:
                    # Share the legacy compiler's exact lock and reserve the revision before HTTP.
                    c.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                        (str(actor["tenant_id"]) + str(base["channel_id"]) + base["listing_key"],),
                    )
                    latest = c.execute(
                        "SELECT coalesce(max(revision),0) AS n FROM offer_snapshot WHERE channel_id=%s AND listing_key=%s",
                        (base["channel_id"], base["listing_key"]),
                    ).fetchone()["n"]
                    floor = c.execute(
                        "SELECT coalesce(max(greatest(coalesce((proof->>'reserved_revision')::bigint,0),coalesce((proof->>'observed_revision')::bigint,0))),0) AS n FROM g3_source_control WHERE proof->>'listing_key'=%s",
                        (base["listing_key"],),
                    ).fetchone()["n"]
                    body = {
                        "revision": max(latest, maxrev, revision, floor) + 1,
                        "payload_hash": digest(target),
                        "payload": target,
                    }
                    control["proof"] = {
                        **control["proof"],
                        "listing_key": base["listing_key"],
                        "reserved_revision": body["revision"],
                        "close_body": body,
                    }
                    c.execute(
                        "UPDATE g3_source_control SET legacy_state='CLOSING',proof=%s WHERE id=%s",
                        (j(control["proof"]), control["id"]),
                    )
                    audit(
                        c,
                        actor,
                        "g3.legacy.close.intent",
                        control["id"],
                        "Revision and immutable request hash reserved before external close: "
                        + digest(body),
                    )
                result = h.put(
                    "/v1/listings/" + base["listing_key"],
                    headers={**auth, "Idempotency-Key": "g3-legacy-close-" + digest(body)},
                    json=body,
                )
                check = h.get("/v1/listings/" + base["listing_key"], headers=auth)
                if (
                    result.status_code == 200
                    and check.status_code == 200
                    and check.json()["payload_hash"] == digest(target)
                ):
                    state = "CLOSED"
                    revision = body["revision"]
                else:
                    state = "UNKNOWN"
        except (httpx.HTTPError, ValueError, KeyError):
            state = "UNKNOWN"
    with db.transaction(actor["tenant_id"]) as c:
        lock_source(c, actor["tenant_id"], key)
        c.execute(
            "UPDATE g3_source_control SET legacy_state=%s,proof=%s WHERE id=%s",
            (
                state,
                j(
                    {
                        **control["proof"],
                        "listing_key": base["listing_key"],
                        "observed_revision": revision,
                        "inflight_seen": bool(pending),
                        "close_requested": close,
                    }
                ),
                control["id"],
            ),
        )
        audit(c, actor, "g3.legacy.close" if close else "g3.legacy.observe", control["id"], state)
    return {"source_key": key, "legacy_state": state, "control_id": control["id"]}
