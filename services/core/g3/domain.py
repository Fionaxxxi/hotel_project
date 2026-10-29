"""G3 business commands: freeze targets, retain exposure, never infer remote success."""

import copy
from uuid import uuid4

from services.core.g1 import offers
from services.core.g1.common import (
    REVIEW,
    audit,
    authorize,
    check_version,
    digest,
    fail,
    get,
    j,
    now,
)
from services.core.g2 import jobs
from services.core.g3.contract import (
    cell_key,
    chunks,
    close_payload,
    validate_content,
    wire_content,
)


def lock_source(c, tenant, key):
    c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (str(tenant) + ":g3:" + key,))


def source_key(c, aid):
    a = get(c, "source_activation", aid)
    return str(a["supplier_id"]) + "/" + a["property_code"]


def adopt(c, actor, aid):
    authorize(actor)
    key = source_key(c, aid)
    lock_source(c, actor["tenant_id"], key)
    c.execute(
        "INSERT INTO g3_source_control(id,tenant_id,source_key,activation_id) VALUES(%s,%s,%s,%s) ON CONFLICT(tenant_id,source_key) DO NOTHING",
        (uuid4(), actor["tenant_id"], key, aid),
    )
    audit(
        c, actor, "g3.source.managed", aid, "G1/G2 new sales fenced; legacy exposure must be read"
    )
    return key


def allocate(c, actor, aid, amounts, buffer, expected):
    authorize(actor)
    key = source_key(c, aid)
    activation = get(c, "source_activation", aid)
    if get(c, "supplier", activation["supplier_id"])["code"] != "S-A":
        fail("G3_SOURCE_CONTRACT_NOT_CONFIGURED", 422)
    lock_source(c, actor["tenant_id"], key)
    control = c.execute("SELECT * FROM g3_source_control WHERE source_key=%s", (key,)).fetchone()
    if not control or control["legacy_state"] not in {"CLEAR", "CLOSED"}:
        fail("LEGACY_EXPOSURE_NOT_CONFIRMED")
    _, _, _, _ = offers.dependencies(c, aid)
    if (
        set(amounts) != {"SIM-A", "SIM-B"}
        or any(type(v) is not int or v < 0 for v in amounts.values())
        or type(buffer) is not int
        or buffer < 0
    ):
        fail("INVALID_ALLOCATION", 422)
    head = c.execute(
        "SELECT * FROM g3_allocation_head WHERE source_key=%s FOR UPDATE", (key,)
    ).fetchone()
    if (head["version"] if head else 0) != expected:
        fail("ALLOCATION_VERSION_CONFLICT")
    capacity = {}
    rows = c.execute(
        "SELECT p.property_code,p.room_code,p.pool_code,i.* FROM inventory_pool p JOIN inventory_cell i ON(p.tenant_id,p.id)=(i.tenant_id,i.pool_id) WHERE p.activation_id=%s",
        (aid,),
    ).fetchall()
    accounts = c.execute("SELECT * FROM g3_account ORDER BY code").fetchall()
    for r in rows:
        pd = "/".join(
            [r["property_code"], r["room_code"], r["pool_code"], r["stay_date"].isoformat()]
        )
        cap = max(0, r["quantity"] - buffer) if not r["stop_sell"] else 0
        capacity[pd] = cap
        if sum(amounts.values()) > cap:
            fail("ALLOCATION_EXCEEDS_CAPACITY")
        reservations = c.execute(
            "SELECT * FROM g3_reservation WHERE source_key=%s AND pool_day=%s", (key, pd)
        ).fetchall()
        exposure = {
            a["code"]: max(
                (x["exposure"] for x in reservations if x["account_id"] == a["id"]), default=0
            )
            for a in accounts
        }
        # Reductions may be prepared while exposure is still reserved. Increases cannot borrow it.
        for code, value in amounts.items():
            if (
                value > exposure.get(code, 0)
                and value
                + sum(
                    max(amounts.get(other, 0), exp)
                    for other, exp in exposure.items()
                    if other != code
                )
                > cap
            ):
                fail("EXPOSURE_RESERVED_UNTIL_REMOTE_CONFIRMED")
    allocation = uuid4()
    version = expected + 1
    c.execute(
        "INSERT INTO g3_allocation VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,clock_timestamp())",
        (
            allocation,
            actor["tenant_id"],
            key,
            aid,
            version,
            buffer,
            j(amounts),
            j(capacity),
            actor["subject"],
        ),
    )
    c.execute(
        "INSERT INTO g3_allocation_head VALUES(%s,%s,%s,%s,%s) ON CONFLICT(tenant_id,source_key) DO UPDATE SET allocation_id=excluded.allocation_id,version=excluded.version",
        (uuid4(), actor["tenant_id"], key, allocation, version),
    )
    audit(
        c,
        actor,
        "g3.allocation.frozen",
        allocation,
        "Static per-pool/day quota; exposure not silently released",
    )
    return get(c, "g3_allocation", allocation)


def compile_snapshot(c, actor, allocation_id, channel, plan_codes):
    authorize(actor)
    a = get(c, "g3_allocation", allocation_id)
    h = c.execute(
        "SELECT * FROM g3_allocation_head WHERE source_key=%s", (a["source_key"],)
    ).fetchone()
    if h["allocation_id"] != a["id"]:
        fail("ALLOCATION_STALE")
    account = c.execute("SELECT * FROM g3_account WHERE code=%s", (channel,)).fetchone()
    if not account:
        fail("CHANNEL_NOT_CONFIGURED", 422)
    base = offers.compile_offer(c, actor, a["activation_id"])
    _, sources, decisions, _ = offers.dependencies(c, a["activation_id"])
    selected = [s for s in sources if s["kind"] == "rate" and s["data"]["plan_code"] in plan_codes]
    if not plan_codes or set(plan_codes) != {s["data"]["plan_code"] for s in selected}:
        fail("PLAN_SELECTION_INVALID", 422)
    selected_keys = {
        base["input_versions"]["channel_codes"][str(decisions[str(s["id"])]["target_id"])]
        for s in selected
    }
    codes = offers.channel_codes(c, account["channel_id"], decisions)
    translate = {
        old: codes[target] for target, old in base["input_versions"]["channel_codes"].items()
    }
    b = base["payload"]
    plans = [p for p in b["plans"] if p["key"] in selected_keys]
    used = {p["pool_key"] for p in plans}
    cells = []
    content_plans = []
    for pool in b["pools"]:
        if pool["key"] not in used:
            continue
        for day in pool["calendar"]:
            cells.append(
                {
                    "kind": "pool",
                    "key": pool["key"],
                    "date": day["date"],
                    "quantity": a["amounts"][channel],
                    "sellable": a["amounts"][channel] > 0,
                }
            )
    for p in plans:
        meta = {k: v for k, v in p.items() if k != "calendar"}
        meta["key"] = translate[p["key"]]
        meta["room_key"] = translate[p["room_key"]]
        content_plans.append(meta)
        for d in p["calendar"]:
            cells.append(
                {"kind": "rate", "key": meta["key"], **d, "sellable": a["amounts"][channel] > 0}
            )
    content = {
        "hotel_key": translate[b["hotel_key"]],
        "scope": b["scope"],
        "pools": sorted(used),
        "plans": content_plans,
    }
    validate_content(content, channel)
    payload = {"content": content, "cells": sorted(cells, key=cell_key)}
    inputs = {
        "definition": "g3-flow-v1",
        "compiler": "channel-compile-v1",
        "chunker": "canonical-cell-v1",
        "base_inputs": base["input_versions"],
        "allocation_id": str(a["id"]),
        "allocation_version": a["version"],
        "capability": account["capability"],
        "channel_codes": codes,
        "plan_codes": sorted(plan_codes),
    }
    sid = uuid4()
    activation = get(c, "source_activation", a["activation_id"])
    key = get(c, "supplier", activation["supplier_id"])["code"] + "-" + activation["property_code"]
    c.execute(
        "INSERT INTO g3_snapshot(id,tenant_id,account_id,allocation_id,base_snapshot_id,source_key,listing_key,payload,payload_hash,inputs,created_by,expires_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            sid,
            actor["tenant_id"],
            account["id"],
            a["id"],
            base["id"],
            a["source_key"],
            key,
            j(payload),
            digest(payload),
            j(inputs),
            actor["subject"],
            base["expires_at"],
        ),
    )
    audit(
        c,
        actor,
        "g3.snapshot.submitted",
        sid,
        "Explicit selected plans, channel capability and static allocation frozen",
    )
    return get(c, "g3_snapshot", sid)


def validate_snapshot(c, s, approved=True):
    if (
        s["inputs"].get("compiler") != "channel-compile-v1"
        or s["inputs"].get("chunker") != "canonical-cell-v1"
    ):
        fail("COMPILER_VERSION_CHANGED")
    if s["expires_at"] <= now():
        fail("SNAPSHOT_EXPIRED")
    if approved and s["state"] != "APPROVED":
        fail("APPROVAL_REQUIRED")
    if approved and s.get("reviewer") == "system.policy":
        from services.core.g4.policy import guard

        guard(c, s)
    account = get(c, "g3_account", s["account_id"])
    if account["capability"] != s["inputs"]["capability"]:
        fail("CAPABILITY_CHANGED")
    a = get(c, "g3_allocation", s["allocation_id"])
    h = c.execute(
        "SELECT * FROM g3_allocation_head WHERE source_key=%s", (s["source_key"],)
    ).fetchone()
    if h["allocation_id"] != a["id"]:
        fail("ALLOCATION_STALE")
    base = get(c, "offer_snapshot", s["base_snapshot_id"])
    offers.valid_snapshot(c, base)
    _, _, decisions, _ = offers.dependencies(c, a["activation_id"])
    if offers.channel_codes(c, account["channel_id"], decisions) != s["inputs"]["channel_codes"]:
        fail("CHANNEL_CODES_CHANGED")
    if digest(s["payload"]) != s["payload_hash"]:
        fail("SNAPSHOT_HASH")


def decide(c, actor, sid, expected, decision, reason, allow_repair=False):
    authorize(actor, REVIEW)
    s = get(c, "g3_snapshot", sid, True)
    check_version(s, expected)
    validate_snapshot(c, s, False)
    if s["created_by"] == actor["subject"]:
        fail("SELF_APPROVAL_FORBIDDEN", 403)
    if s["state"] != "SUBMITTED" or decision not in {"APPROVED", "REJECTED"}:
        fail("INVALID_DECISION")
    c.execute(
        "UPDATE g3_snapshot SET state=%s,reviewer=%s,reason=%s,allow_repair=%s,version=version+1 WHERE id=%s",
        (decision, actor["subject"], reason, allow_repair, sid),
    )
    audit(c, actor, "g3.snapshot.decided", sid, reason)
    jobs.emit(
        c, actor["tenant_id"], "g3.approval.decided", sid, s["version"] + 1, {"decision": decision}
    )
    return get(c, "g3_snapshot", sid)


def reserve(c, s, payload):
    account = get(c, "g3_account", s["account_id"])
    a = get(c, "g3_allocation", s["allocation_id"])
    for cell in payload["cells"]:
        if cell["kind"] != "pool":
            continue
        pd = cell["key"] + "/" + cell["date"]
        qty = cell["quantity"]
        others = c.execute(
            "SELECT coalesce(sum(exposure),0) AS n FROM g3_reservation WHERE source_key=%s AND pool_day=%s AND account_id!=%s",
            (s["source_key"], pd, account["id"]),
        ).fetchone()["n"]
        if qty + others > a["capacity"].get(pd, 0):
            fail("EXPOSURE_RESERVED_UNTIL_REMOTE_CONFIRMED")
        c.execute(
            "INSERT INTO g3_reservation VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(tenant_id,source_key,account_id,pool_day) DO UPDATE SET exposure=greatest(g3_reservation.exposure,excluded.exposure)",
            (uuid4(), s["tenant_id"], s["source_key"], account["id"], pd, qty),
        )


def capacity_deficits(c, key):
    h = c.execute(
        "SELECT a.capacity FROM g3_allocation_head h JOIN g3_allocation a ON a.id=h.allocation_id WHERE h.source_key=%s",
        (key,),
    ).fetchone()
    if not h:
        return []
    rows = c.execute(
        "SELECT pool_day,sum(exposure) AS n FROM g3_reservation WHERE source_key=%s GROUP BY pool_day",
        (key,),
    ).fetchall()
    return sorted(
        {r["pool_day"][-10:] for r in rows if r["n"] > h["capacity"].get(r["pool_day"], 0)}
    )


def make_intent(
    c, actor, listing, s, action, payload, plan_id=None, reason="Approved publish", only_keys=None
):
    if action != "STOP":
        from services.core.g5.recovery import guard

        guard(c, s["source_key"])
    lid = listing["id"]
    desired = listing["desired_revision"] + (action != "REPAIR")
    delivery = listing["delivery_revision"] + 1
    stop_epoch = listing["stop_epoch"] + (action == "STOP")
    account = get(c, "g3_account", s["account_id"])
    executable = copy.deepcopy(payload)
    if action == "STOP":
        executable["cells"] = [x for x in executable["cells"] if not x["sellable"]]
    if only_keys is not None:
        executable["cells"] = [x for x in executable["cells"] if cell_key(x) in only_keys]
    split = chunks(executable, account["code"])
    wire = wire_content(payload["content"], account["code"])
    manifest = {
        "content_hash": digest(wire),
        "payload_hash": digest(payload),
        "chunks": {str(i): digest(chunk) for i, chunk in enumerate(split, 1)},
    }
    iid = uuid4()
    c.execute(
        "INSERT INTO g3_intent(id,tenant_id,listing_id,snapshot_id,plan_id,action,desired_revision,delivery_revision,stop_epoch,payload,payload_hash,manifest,manifest_hash,created_by,reason) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            iid,
            actor["tenant_id"],
            lid,
            s["id"],
            plan_id,
            action,
            desired,
            delivery,
            stop_epoch,
            j(payload),
            digest(payload),
            j(manifest),
            digest(manifest),
            actor["subject"],
            reason,
        ),
    )
    bodies = [
        {
            "revision": delivery,
            "action": "CONTENT",
            "chunk_index": 0,
            "manifest_hash": digest(manifest),
            "content": wire,
            "manifest": manifest,
            "cells": [],
        }
    ]
    bodies += [
        {
            "revision": delivery,
            "action": action,
            "chunk_index": i,
            "manifest_hash": digest(manifest),
            "content": None,
            "manifest": None,
            "cells": chunk,
        }
        for i, chunk in enumerate(split, 1)
    ]
    for body in bodies:
        tid = uuid4()
        key = digest(
            {
                "tenant": str(actor["tenant_id"]),
                "account": str(s["account_id"]),
                "listing": str(lid),
                "delivery": delivery,
                "action": body["action"],
                "chunk": body["chunk_index"],
            }
        )
        c.execute(
            "INSERT INTO g3_task(id,tenant_id,intent_id,chunk_index,action,request_key,body,request_hash) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                tid,
                actor["tenant_id"],
                iid,
                body["chunk_index"],
                body["action"],
                key,
                j(body),
                digest(body),
            ),
        )
    c.execute(
        "UPDATE g3_task SET state='SUPERSEDED',version=version+1 WHERE intent_id IN (SELECT id FROM g3_intent WHERE listing_id=%s AND id!=%s) AND state IN ('READY','RETRY_WAIT')",
        (lid, iid),
    )
    c.execute(
        "UPDATE g3_listing SET intent_id=%s,desired_revision=%s,delivery_revision=%s,stop_epoch=%s,state=%s,version=version+1,next_due_at=clock_timestamp() WHERE id=%s",
        (
            iid,
            desired,
            delivery,
            stop_epoch,
            "STOP_SELL_REQUESTED" if action == "STOP" else "PENDING",
            lid,
        ),
    )
    jobs.emit(c, actor["tenant_id"], "g3.intent.created", iid, delivery, {"action": action})
    audit(c, actor, "g3.intent." + action.lower(), iid, reason)
    return get(c, "g3_intent", iid)


def publish(c, actor, snapshot_ids, key, expected, reopen=False):
    authorize(actor)
    req = digest(
        {"snapshots": sorted(str(x) for x in snapshot_ids), "expected": expected, "reopen": reopen}
    )
    lock_source(c, actor["tenant_id"], "plan:" + key)
    old = c.execute("SELECT * FROM g3_plan WHERE command_key=%s", (key,)).fetchone()
    if old:
        if old["request_hash"] != req:
            fail("IDEMPOTENCY_CONFLICT")
        return plan_detail(c, old["id"])
    snapshots = [get(c, "g3_snapshot", sid) for sid in snapshot_ids]
    if len({s["source_key"] for s in snapshots}) != 1:
        fail("PLAN_SINGLE_SOURCE_REQUIRED", 422)
    if (
        not snapshots
        or len(snapshots) > 10
        or len({(s["account_id"], s["listing_key"]) for s in snapshots}) != len(snapshots)
    ):
        fail("PLAN_TARGET_SET", 422)
    for source in sorted({s["source_key"] for s in snapshots}):
        lock_source(c, actor["tenant_id"], source)
    listings = []
    for s in snapshots:
        validate_snapshot(c, s)
        c.execute(
            "INSERT INTO g3_listing(id,tenant_id,account_id,source_key,listing_key) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(tenant_id,account_id,listing_key) DO NOTHING",
            (uuid4(), actor["tenant_id"], s["account_id"], s["source_key"], s["listing_key"]),
        )
        li = c.execute(
            "SELECT * FROM g3_listing WHERE account_id=%s AND listing_key=%s FOR UPDATE",
            (s["account_id"], s["listing_key"]),
        ).fetchone()
        if expected.get(str(s["account_id"])) != li["desired_revision"]:
            fail("DESIRED_VERSION_CONFLICT")
        if li["stop_epoch"]:
            if not reopen or li["state"] != "STOPPED":
                fail("STOP_FENCE_REQUIRES_NEW_APPROVAL")
            prior = get(c, "g3_intent", li["intent_id"])
            if s["id"] == prior["snapshot_id"] or s["created_at"] <= prior["created_at"]:
                fail("REOPEN_NEW_SNAPSHOT_REQUIRED")
        reserve(c, s, s["payload"])
        listings.append(li)
    pid = uuid4()
    c.execute(
        "INSERT INTO g3_plan(id,tenant_id,command_key,request_hash,created_by) VALUES(%s,%s,%s,%s,%s)",
        (pid, actor["tenant_id"], key, req, actor["subject"]),
    )
    for li, s in zip(listings, snapshots, strict=True):
        make_intent(c, actor, li, s, "PUBLISH", s["payload"], pid)
    audit(c, actor, "g3.plan.requested", pid, "Atomic target set and reservations")
    return plan_detail(c, pid)


def plan_detail(c, pid):
    p = get(c, "g3_plan", pid)
    p["branches"] = c.execute(
        "SELECT i.*,a.code,l.state AS listing_state FROM g3_intent i JOIN g3_listing l ON(l.tenant_id,l.id)=(i.tenant_id,i.listing_id) JOIN g3_account a ON(a.tenant_id,a.id)=(l.tenant_id,l.account_id) WHERE i.plan_id=%s ORDER BY a.code",
        (pid,),
    ).fetchall()
    states = [b["state"] for b in p["branches"]]
    p["state"] = (
        "CANCELLED"
        if states and all(s == "CANCELLED" for s in states)
        else (
            "VERIFIED"
            if states and all(s == "VERIFIED" for s in states)
            else "PARTIAL"
            if "VERIFIED" in states
            else "BLOCKED"
            if any(s in {"BLOCKED", "MANUAL"} for s in states)
            else "RUNNING"
        )
    )
    for b in p["branches"]:
        b["tasks"] = c.execute(
            "SELECT * FROM g3_task WHERE intent_id=%s ORDER BY chunk_index", (b["id"],)
        ).fetchall()
        for t in b["tasks"]:
            t["cell_results"] = c.execute(
                "SELECT cell_key,state,code FROM g3_cell_result WHERE task_id=%s ORDER BY cell_key",
                (t["id"],),
            ).fetchall()
            t["receipts"] = c.execute(
                "SELECT phase,status,data,created_at FROM g3_receipt WHERE task_id=%s ORDER BY created_at DESC LIMIT 5",
                (t["id"],),
            ).fetchall()
    return p


def cancel_initial(c, actor, pid, reason):
    authorize(actor)
    p = get(c, "g3_plan", pid, True)
    intents = c.execute("SELECT * FROM g3_intent WHERE plan_id=%s", (pid,)).fetchall()
    if p["state"] == "CANCELLED":
        return plan_detail(c, pid)
    for key in sorted({get(c, "g3_listing", i["listing_id"])["source_key"] for i in intents}):
        lock_source(c, actor["tenant_id"], key)
    for i in intents:
        li = get(c, "g3_listing", i["listing_id"], True)
        if li["intent_id"] != i["id"] or li["desired_revision"] != 1:
            fail("ONLY_UNSENT_INITIAL_PLAN_CAN_CANCEL")
        if c.execute(
            "SELECT 1 FROM g3_task WHERE intent_id=%s AND send_count>0", (i["id"],)
        ).fetchone():
            fail("EXTERNAL_EFFECT_MUST_BE_CONFIRMED")
    for i in intents:
        li = get(c, "g3_listing", i["listing_id"])
        c.execute(
            "UPDATE g3_task SET state='SUPERSEDED',version=version+1 WHERE intent_id=%s", (i["id"],)
        )
        c.execute("UPDATE g3_intent SET state='CANCELLED' WHERE id=%s", (i["id"],))
        c.execute(
            "UPDATE g3_listing SET intent_id=NULL,state='UNPUBLISHED',desired_revision=desired_revision+1,version=version+1 WHERE id=%s",
            (li["id"],),
        )
        c.execute(
            "UPDATE g3_reservation SET exposure=0 WHERE source_key=%s AND account_id=%s",
            (li["source_key"], li["account_id"]),
        )
    c.execute("UPDATE g3_plan SET state='CANCELLED' WHERE id=%s", (pid,))
    audit(c, actor, "g3.initial_plan.cancelled", pid, reason)
    return plan_detail(c, pid)


def stop(c, actor, lid, key, expected, dates, reason, system=False):
    if not system:
        authorize(actor, {"tenant_admin"})
    li = get(c, "g3_listing", lid)
    lock_source(c, actor["tenant_id"], li["source_key"])
    li = get(c, "g3_listing", lid, True)
    req = digest({"expected": expected, "dates": sorted(dates), "reason": reason})
    old = c.execute(
        "SELECT * FROM g3_stop WHERE listing_id=%s AND command_key=%s", (lid, key)
    ).fetchone()
    if old:
        if old["request_hash"] != req:
            fail("IDEMPOTENCY_CONFLICT")
        return get(c, "g3_intent", old["intent_id"])
    check_version(li, expected)
    prior = get(c, "g3_intent", li["intent_id"])
    s = get(c, "g3_snapshot", prior["snapshot_id"])
    if not dates or not set(dates) <= {x["date"] for x in prior["payload"]["cells"]}:
        fail("STOP_SCOPE", 422)
    payload = close_payload(prior["payload"], dates)
    intent = make_intent(c, actor, li, s, "STOP", payload, reason=reason)
    c.execute(
        "INSERT INTO g3_stop(id,tenant_id,listing_id,command_key,request_hash,intent_id,reason,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
        (uuid4(), actor["tenant_id"], lid, key, req, intent["id"], reason, actor["subject"]),
    )
    return intent


def repair(c, actor, did, expected, transient=False):
    authorize(actor)
    d = get(c, "g3_diff", did, True)
    li = get(c, "g3_listing", d["listing_id"])
    lock_source(c, actor["tenant_id"], li["source_key"])
    li = get(c, "g3_listing", li["id"], True)
    if d["repair_intent_id"]:
        return get(c, "g3_intent", d["repair_intent_id"])
    check_version(li, expected)
    if d["desired_revision"] != li["desired_revision"] or d["classification"] not in {
        "DRIFT",
        "MISSING",
    }:
        fail("DIFF_STALE_OR_NOT_REPAIRABLE")
    intent = get(c, "g3_intent", li["intent_id"])
    s = get(c, "g3_snapshot", intent["snapshot_id"])
    if intent["action"] == "STOP":
        fail("STOP_FENCE_ACTIVE")
    validate_snapshot(c, s)
    transient_allowed = (
        transient
        and c.execute(
            "SELECT 1 FROM g3_cell_result r JOIN g3_task t ON t.id=r.task_id WHERE t.intent_id=%s AND r.code='TEMPORARY_CELL'",
            (intent["id"],),
        ).fetchone()
    )
    if not s["allow_repair"] and not transient_allowed:
        fail("REPAIR_AUTHORIZATION_REQUIRED", 403)
    count = c.execute(
        "SELECT count(*) AS n FROM g3_intent WHERE listing_id=%s AND desired_revision=%s AND action='REPAIR'",
        (li["id"], li["desired_revision"]),
    ).fetchone()["n"]
    if count >= 3:
        fail("REPAIR_BUDGET_EXHAUSTED")
    reserve(c, s, intent["payload"])
    new = make_intent(
        c,
        actor,
        li,
        s,
        "REPAIR",
        intent["payload"],
        reason="Exact approved target replay; immutable original approval",
        only_keys=set(d["cells"]) if d["cells"] else None,
    )
    c.execute(
        "UPDATE g3_diff SET state='REPAIRING',repair_intent_id=%s WHERE id=%s", (new["id"], did)
    )
    return new
