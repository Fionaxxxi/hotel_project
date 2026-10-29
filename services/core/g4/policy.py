"""Deterministic preauthorization. The model never participates in approval."""

from datetime import timedelta
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid4

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
from services.core.g3 import domain
from services.core.g3.contract import cell_key


def decimal(value):
    if isinstance(value, (float, bool)):
        fail("DECIMAL_STRING_REQUIRED", 422)
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        fail("INVALID_DECIMAL", 422)
    if not result.is_finite() or result < 0:
        fail("INVALID_DECIMAL", 422)
    return result


def create(c, actor, ids, settings):
    authorize(actor, {"tenant_admin"})
    if set(settings) - {
        "auto_activate",
        "auto_publish",
        "relative_limit",
        "absolute_limit",
        "allow_repair",
        "inventory_limit",
    }:
        fail("POLICY_UNKNOWN_FIELD", 422)
    snapshots = [get(c, "g3_snapshot", sid) for sid in ids]
    if (
        len(snapshots) != 2
        or len({s["source_key"] for s in snapshots}) != 1
        or len({s["account_id"] for s in snapshots}) != 2
    ):
        fail("POLICY_BASELINES_REQUIRED", 422)
    for s in snapshots:
        domain.validate_snapshot(c, s)
        if not s["reviewer"] or s["reviewer"].startswith("system."):
            fail("HUMAN_BASELINE_REQUIRED")
    allocation = get(c, "g3_allocation", snapshots[0]["allocation_id"])
    if any(s["allocation_id"] != allocation["id"] for s in snapshots):
        fail("POLICY_ALLOCATION_MISMATCH")
    activation = get(c, "source_activation", allocation["activation_id"])
    manifest = get(c, "import_batch", activation["batch_id"])["manifest"]
    original = c.execute(
        "SELECT b.manifest FROM generation_activation l JOIN source_generation g ON g.id=l.generation_id JOIN processing_run r ON r.id=g.run_id JOIN import_batch b ON b.id=r.batch_id WHERE l.activation_id=%s",
        (activation["id"],),
    ).fetchone()
    if original:
        manifest = original["manifest"]
    relative = decimal(settings.get("relative_limit", "0.10"))
    absolute = decimal(settings.get("absolute_limit", "20.00"))
    if relative > Decimal("0.30") or absolute > 100:
        fail("POLICY_LIMIT_TOO_WIDE", 422)
    body = {
        "auto_activate": settings.get("auto_activate", False),
        "auto_publish": settings.get("auto_publish", False),
        "allow_repair": settings.get("allow_repair", False),
        "relative_limit": str(relative),
        "absolute_limit": str(absolute),
        "inventory_limit": int(settings.get("inventory_limit", 0)),
        "buffer": allocation["buffer"],
        "amounts": allocation["amounts"],
        "scope": manifest["scope"],
        "mode": manifest["mode"],
        "contract_version": manifest.get("contract_version", "g2-demo-v1"),
        "sequence_stream": manifest.get("sequence_stream", "demo-v1"),
        "supplier_id": str(activation["supplier_id"]),
        "property_code": activation["property_code"],
        "activation_id": str(activation["id"]),
        "plan_codes": ["NONREF"],
        "rules": "g4-policy-v1",
    }
    if body["inventory_limit"] < 0 or body["inventory_limit"] > 100:
        fail("POLICY_INVENTORY_LIMIT", 422)
    for s in snapshots:
        rate = s["payload"]["cells"]
        if any(x["kind"] == "rate" and x.get("sellable") is not True for x in rate):
            fail("POLICY_SELLABLE_BASELINE_REQUIRED")
        content = s["payload"]["content"]
        if any(
            p["cancellation"].get("kind") != "NONREF"
            for p in content.get("plans", [])
            if isinstance(p.get("cancellation"), dict)
        ):
            fail("POLICY_NONREF_ONLY")
    pid = uuid4()
    c.execute(
        "INSERT INTO g4_policy(id,tenant_id,source_key,body,body_hash,baseline_ids,created_by,expires_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            pid,
            actor["tenant_id"],
            allocation["source_key"],
            j(body),
            digest(body),
            j([str(s["id"]) for s in snapshots]),
            actor["subject"],
            now() + timedelta(hours=8),
        ),
    )
    audit(
        c,
        actor,
        "g4.policy.created",
        pid,
        "Frozen human baselines and explicit independent activation/publish grants",
    )
    return get(c, "g4_policy", pid)


def command(c, actor, pid, expected, action, reason):
    p = get(c, "g4_policy", pid)
    domain.lock_source(c, actor["tenant_id"], p["source_key"])
    p = get(c, "g4_policy", pid, True)
    check_version(p, expected)
    authorize(actor, REVIEW if action in {"approve", "reject"} else {"tenant_admin"})
    transitions = {
        "submit": ({"DRAFT"}, "SUBMITTED"),
        "approve": ({"SUBMITTED"}, "APPROVED"),
        "reject": ({"SUBMITTED"}, "REJECTED"),
        "enable": ({"APPROVED", "PAUSED"}, "ENABLED"),
        "pause": ({"ENABLED"}, "PAUSED"),
        "revoke": ({"APPROVED", "ENABLED", "PAUSED"}, "REVOKED"),
    }
    if action not in transitions or p["state"] not in transitions[action][0]:
        fail("POLICY_INVALID_TRANSITION")
    if action in {"approve", "reject"} and p["created_by"] == actor["subject"]:
        fail("SELF_APPROVAL_FORBIDDEN", 403)
    if action == "enable":
        if p["expires_at"] <= now() or not p["reviewer"] or p["body_hash"] != digest(p["body"]):
            fail("POLICY_EXPIRED_OR_UNREVIEWED")
        if c.execute(
            "SELECT 1 FROM g4_policy WHERE source_key=%s AND state='ENABLED' AND id<>%s",
            (p["source_key"], pid),
        ).fetchone():
            fail("POLICY_OVERLAP")
    c.execute(
        "UPDATE g4_policy SET state=%s,reviewer=CASE WHEN %s THEN %s ELSE reviewer END,reason=%s,version=version+1,epoch=epoch+1 WHERE id=%s",
        (transitions[action][1], action in {"approve", "reject"}, actor["subject"], reason, pid),
    )
    audit(c, actor, "g4.policy." + action, pid, reason)
    return get(c, "g4_policy", pid)


def differences(baseline, previous, current, body):
    """Whole target comparison; fixed anchor AND previous target, no rolling anchor reset."""
    reasons = []
    if baseline["content"] != current["content"] or previous["content"] != current["content"]:
        reasons.append("CONTENT_OR_TERMS_CHANGED")

    def indexed(p):
        return {cell_key(x): x for x in p["cells"]}

    target = indexed(current)
    for label, payload in [("BASELINE", baseline), ("PREVIOUS", previous)]:
        old = indexed(payload)
        if set(old) != set(target):
            reasons.append(label + "_SCOPE_CHANGED")
            continue
        for key, cell in target.items():
            before = old[key]
            changing = {"sell_amount", "net_amount"} if cell["kind"] == "rate" else {"quantity"}
            if {k: v for k, v in cell.items() if k not in changing} != {
                k: v for k, v in before.items() if k not in changing
            }:
                reasons.append("SEMANTIC_OR_SELLABLE_CHANGED:" + key)
            if cell["kind"] == "rate":
                for field in ["sell_amount", "net_amount"]:
                    if field not in cell or field not in before:
                        # Canonical G3 rate currently exposes sell_amount; net checked via base snapshot separately.
                        continue
                    a, b = decimal(before[field]), decimal(cell[field])
                    if (
                        a <= 0
                        or b <= 0
                        or abs(a - b) > decimal(body["absolute_limit"])
                        or abs(a - b) / a > decimal(body["relative_limit"])
                    ):
                        reasons.append(label + "_PRICE_LIMIT:" + key)
            elif abs(cell["quantity"] - before["quantity"]) > body["inventory_limit"]:
                reasons.append(label + "_INVENTORY_LIMIT:" + key)
    return sorted(set(reasons))


def evaluate(c, pid, sid):
    p = get(c, "g4_policy", pid)
    domain.lock_source(c, p["tenant_id"], p["source_key"])
    p = get(c, "g4_policy", pid, True)
    s = get(c, "g3_snapshot", sid, True)
    domain.validate_snapshot(c, s, False)
    bases = [get(c, "g3_snapshot", UUID(x)) for x in p["baseline_ids"]]
    base = next((b for b in bases if b["account_id"] == s["account_id"]), None)
    reasons = []
    if p["state"] != "ENABLED" or p["expires_at"] <= now() or not p["body"]["auto_publish"]:
        reasons.append("POLICY_NOT_ENABLED")
    if base is None or p["source_key"] != s["source_key"]:
        fail("POLICY_SCOPE_MISMATCH")
    li = c.execute(
        "SELECT * FROM g3_listing WHERE account_id=%s AND listing_key=%s",
        (s["account_id"], s["listing_key"]),
    ).fetchone()
    previous = (
        get(c, "g3_intent", li["intent_id"])["payload"]
        if li and li["intent_id"]
        else base["payload"]
    )
    if li and li["stop_epoch"]:
        reasons.append("STOP_FENCE_REQUIRES_NEW_APPROVAL")
    allocation = get(c, "g3_allocation", s["allocation_id"])
    if allocation["amounts"] != p["body"]["amounts"] or allocation["buffer"] != p["body"]["buffer"]:
        reasons.append("ALLOCATION_CONFIG_CHANGED")
    if (
        s["inputs"]["capability"] != base["inputs"]["capability"]
        or s["inputs"]["channel_codes"] != base["inputs"]["channel_codes"]
    ):
        reasons.append("CAPABILITY_OR_CODES_CHANGED")
    reasons += differences(base["payload"], previous, s["payload"], p["body"])

    def source_inventory(snapshot):
        a = get(c, "g3_allocation", snapshot["allocation_id"])
        return {
            r["pool_code"] + "/" + r["room_code"] + "/" + r["stay_date"].isoformat(): (
                r["quantity"],
                r["stop_sell"],
            )
            for r in c.execute(
                "SELECT p.pool_code,p.room_code,i.* FROM inventory_pool p JOIN inventory_cell i ON i.pool_id=p.id WHERE p.activation_id=%s",
                (a["activation_id"],),
            ).fetchall()
        }

    latest_inventory = source_inventory(s)
    prior_snapshot = (
        get(c, "g3_snapshot", get(c, "g3_intent", li["intent_id"])["snapshot_id"])
        if li and li["intent_id"]
        else base
    )
    for label, anchor in [("BASELINE", base), ("PREVIOUS", prior_snapshot)]:
        old_inventory = source_inventory(anchor)
        if set(old_inventory) != set(latest_inventory):
            reasons.append(label + "_SOURCE_INVENTORY_SCOPE_CHANGED")
        elif any(
            old_inventory[k][1] != latest_inventory[k][1]
            or abs(old_inventory[k][0] - latest_inventory[k][0]) > p["body"]["inventory_limit"]
            for k in latest_inventory
        ):
            reasons.append(label + "_SOURCE_INVENTORY_LIMIT")
    # Base compiler retains net/markup/policies; compare immutable business configuration too.
    for name in ["pricing_rule"]:
        if s["inputs"]["base_inputs"].get(name) != base["inputs"]["base_inputs"].get(name):
            reasons.append("PRICING_RULE_CHANGED")
    input_hash = digest(
        {
            "snapshot": s["payload_hash"],
            "inputs": s["inputs"],
            "policy": p["body_hash"],
            "epoch": p["epoch"],
            "previous": digest(previous),
            "baselines": p["baseline_ids"],
        }
    )
    decision = "REVIEW" if reasons else "ALLOW"
    did = uuid4()
    c.execute(
        "INSERT INTO g4_decision(id,tenant_id,policy_id,snapshot_id,epoch,payload_hash,input_hash,result,reasons,expires_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
        (
            did,
            s["tenant_id"],
            pid,
            sid,
            p["epoch"],
            s["payload_hash"],
            input_hash,
            decision,
            j(sorted(set(reasons))),
            min(p["expires_at"], s["expires_at"]),
        ),
    )
    if decision == "ALLOW" and s["state"] == "SUBMITTED":
        c.execute(
            "UPDATE g3_snapshot SET state='APPROVED',reviewer='system.policy',reason='Independent preauthorized policy decision',allow_repair=%s,version=version+1 WHERE id=%s",
            (p["body"]["allow_repair"], sid),
        )
    return c.execute(
        "SELECT * FROM g4_decision WHERE snapshot_id=%s AND policy_id=%s AND epoch=%s AND input_hash=%s",
        (sid, pid, p["epoch"], input_hash),
    ).fetchone()


def guard(c, snapshot):
    if snapshot["reviewer"] != "system.policy":
        return
    decision = c.execute(
        "SELECT * FROM g4_decision WHERE snapshot_id=%s AND payload_hash=%s AND result='ALLOW' ORDER BY created_at DESC LIMIT 1",
        (snapshot["id"], snapshot["payload_hash"]),
    ).fetchone()
    if not decision:
        fail("POLICY_DECISION_REQUIRED")
    p = get(c, "g4_policy", decision["policy_id"], True)
    if (
        p["state"] != "ENABLED"
        or p["epoch"] != decision["epoch"]
        or min(p["expires_at"], decision["expires_at"]) <= now()
    ):
        fail("POLICY_AUTHORIZATION_REVOKED")
