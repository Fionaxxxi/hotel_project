import json
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from services.core.domain import usd
from services.core.g1.common import (
    REVIEW,
    audit,
    authorize,
    business_now,
    check_version,
    digest,
    fail,
    get,
    j,
    now,
)
from services.core.g1.matching import conflicts, mapping, targets


def dependencies(conn, activation_id):
    activation = get(conn, "source_activation", activation_id)
    head = conn.execute(
        "SELECT h.generation_id,l.activation_id FROM source_head h LEFT JOIN generation_activation l ON(h.tenant_id,h.generation_id)=(l.tenant_id,l.generation_id) WHERE h.supplier_id=%s AND h.property_code=%s",
        (activation["supplier_id"], activation["property_code"]),
    ).fetchone()
    if head and head["activation_id"] != activation_id:
        fail("SOURCE_NOT_CURRENT")
    if not conn.execute(
        "SELECT 1 FROM source_partition WHERE activation_id=%s", (activation_id,)
    ).fetchone():
        fail("SOURCE_NOT_CURRENT")
    sources = conn.execute(
        "SELECT * FROM supplier_entity_version WHERE activation_id=%s ORDER BY kind,natural_key",
        (activation_id,),
    ).fetchall()
    decisions = {}
    for source in sources:
        # Unused rooms need not be mapped, but all declared sellable plans do.
        if (
            source["kind"] == "room"
            and not conn.execute(
                "SELECT 1 FROM supplier_entity_version WHERE activation_id=%s AND kind='rate' AND parent_key=%s",
                (activation_id, source["natural_key"]),
            ).fetchone()
        ):
            continue
        decision = mapping(conn, source)
        if not decision or decision["status"] != "APPROVED":
            fail("MAPPING_REQUIRED")
        target = next(
            (t for t in targets(conn, source) if t["target_id"] == decision["target_id"]), None
        )
        if (
            not target
            or target["target_version_id"] != decision["target_version_id"]
            or conflicts(conn, source, target)
        ):
            fail("MAPPING_STALE")
        decisions[str(source["id"])] = decision
    observed = conn.execute(
        "SELECT c.observed_at FROM inventory_cell c JOIN inventory_pool p ON(c.tenant_id,c.pool_id)=(p.tenant_id,p.id) WHERE p.activation_id=%s",
        (activation_id,),
    ).fetchall()
    if not observed:
        fail("INVENTORY_MISSING")
    clock = business_now()
    if any(
        clock - r["observed_at"] > timedelta(hours=24)
        or r["observed_at"] - clock > timedelta(minutes=5)
        for r in observed
    ):
        fail("SOURCE_EXPIRED_OR_FUTURE")
    versions = {
        "activation_id": str(activation_id),
        "mappings": {
            k: {
                "id": str(v["id"]),
                "version": v["version"],
                "target_id": str(v["target_id"]),
                "target_version_id": str(v["target_version_id"]),
            }
            for k, v in sorted(decisions.items())
        },
        "pricing_rule": "markup-10pct-v1",
        "capability": "sim-a-v1",
        "canonical": "json-v1",
    }
    if head:
        versions["generation_id"] = str(head["generation_id"])
    return activation, sources, decisions, versions


def compile_payload(scope, sources, decisions, rates, pools, channel_codes):
    """Frozen inputs only: no HTTP, database or global clock in this function."""
    root = next(s for s in sources if s["kind"] == "property")
    hotel = channel_codes[str(decisions[str(root["id"])]["target_id"])]
    pool_payload = []
    for pool in pools:
        calendar = []
        for cell in pool["cells"]:
            if cell["stop_sell"] or cell["quantity"] <= 0:
                fail("NOT_SELLABLE")
            calendar.append({"date": cell["stay_date"].isoformat(), "quantity": cell["quantity"]})
        pool_payload.append(
            {"key": pool["key"], "calendar": sorted(calendar, key=lambda c: c["date"])}
        )
    plans = []
    from datetime import date

    from services.core.domain import stay_dates

    required_dates = {
        d.isoformat()
        for d in stay_dates(
            date.fromisoformat(scope["stay_date_from"]),
            date.fromisoformat(scope["stay_date_to_exclusive"]),
        )
    }
    if any({d["date"] for d in p["calendar"]} != required_dates for p in pool_payload):
        fail("INCOMPLETE_POOL_CALENDAR")
    for source in [s for s in sources if s["kind"] == "rate"]:
        plan_rates = [r for r in rates if r["source_plan_id"] == source["id"]]
        if not plan_rates:
            fail("PRICE_MISSING")
        if {r["stay_date"].isoformat() for r in plan_rates} != required_dates:
            fail("INCOMPLETE_RATE_CALENDAR")
        policy = source["data"]
        if any(policy[k] == "UNKNOWN" for k in ["meal", "payment", "tax_basis"]):
            fail("POLICY_UNKNOWN")
        room = next(
            s for s in sources if s["kind"] == "room" and s["natural_key"] == source["parent_key"]
        )
        plans.append(
            {
                "key": channel_codes[str(decisions[str(source["id"])]["target_id"])],
                "room_key": channel_codes[str(decisions[str(room["id"])]["target_id"])],
                "pool_key": plan_rates[0]["pool_key"],
                "adults": 2,
                "currency": "USD",
                "meal": policy["meal"],
                "payment": policy["payment"],
                "tax_basis": policy["tax_basis"],
                "cancellation": json.loads(policy["cancellation"]),
                "calendar": [
                    {
                        "date": r["stay_date"].isoformat(),
                        "net_amount": format(r["net_amount"], ".4f"),
                        "sell_amount": format(usd(r["net_amount"] * Decimal("1.10")), ".2f"),
                    }
                    for r in sorted(plan_rates, key=lambda r: r["stay_date"])
                ],
            }
        )
    if len({p["key"] for p in plans}) != len(plans):
        fail("DUPLICATE_TARGET_PLAN")
    return {
        "capability": "sim-a-v1",
        "hotel_key": str(hotel),
        "scope": scope,
        "pools": sorted(pool_payload, key=lambda p: p["key"]),
        "plans": sorted(plans, key=lambda p: p["key"]),
    }


def compile_offer(conn, actor, activation_id):
    authorize(actor)
    activation, sources, decisions, versions = dependencies(conn, activation_id)
    channel = conn.execute("SELECT * FROM channel_account WHERE code='SIM-A'").fetchone()
    if not channel:
        fail("CHANNEL_NOT_CONFIGURED")
    if channel["capability_version"] != "sim-a-v1":
        fail("CHANNEL_CAPABILITY_UNSUPPORTED")
    codes = channel_codes(conn, channel["id"], decisions)
    versions["channel_codes"] = codes
    pools = []
    rates = conn.execute(
        "SELECT r.*,l.pool_id FROM rate_cell r JOIN plan_pool_link l ON(r.tenant_id,r.source_plan_id)=(l.tenant_id,l.source_plan_id) JOIN supplier_entity_version s ON(r.tenant_id,r.source_plan_id)=(s.tenant_id,s.id) WHERE s.activation_id=%s",
        (activation_id,),
    ).fetchall()
    for pool in conn.execute(
        "SELECT * FROM inventory_pool WHERE activation_id=%s", (activation_id,)
    ).fetchall():
        pool["key"] = pool["property_code"] + "/" + pool["room_code"] + "/" + pool["pool_code"]
        pool["cells"] = conn.execute(
            "SELECT * FROM inventory_cell WHERE pool_id=%s ORDER BY stay_date", (pool["id"],)
        ).fetchall()
        pools.append(pool)
    for rate in rates:
        rate["pool_key"] = next(p["key"] for p in pools if p["id"] == rate["pool_id"])
    payload = compile_payload(activation["scope"], sources, decisions, rates, pools, codes)
    payload_hash = digest(payload)
    key = payload["hotel_key"]
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        (str(actor["tenant_id"]) + str(channel["id"]) + key,),
    )
    latest = conn.execute(
        "SELECT * FROM offer_snapshot WHERE channel_id=%s AND listing_key=%s ORDER BY revision DESC LIMIT 1",
        (channel["id"], key),
    ).fetchone()
    # A G3 migration close is a real legacy revision even though it is not a sale snapshot.
    legacy_floor = conn.execute(
        "SELECT coalesce(max(greatest(coalesce((proof->>'reserved_revision')::bigint,0),coalesce((proof->>'observed_revision')::bigint,0))),0) AS n FROM g3_source_control WHERE proof->>'listing_key'=%s",
        (key,),
    ).fetchone()["n"]
    if (
        latest
        and latest["revision"] >= legacy_floor
        and latest["input_versions"] == versions
        and latest["payload_hash"] == payload_hash
        and latest["expires_at"] > now()
        and latest["status"] != "REJECTED"
    ):
        return latest
    sid = uuid4()
    conn.execute(
        "INSERT INTO offer_snapshot(id,tenant_id,activation_id,channel_id,listing_key,revision,input_versions,payload,payload_hash,expires_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            sid,
            actor["tenant_id"],
            activation_id,
            channel["id"],
            key,
            max(latest["revision"] if latest else 0, legacy_floor) + 1,
            j(versions),
            j(payload),
            payload_hash,
            now() + timedelta(minutes=30),
        ),
    )
    audit(conn, actor, "snapshot.compiled", sid, payload_hash)
    return get(conn, "offer_snapshot", sid)


def valid_snapshot(conn, snapshot, approved=False):
    channel = get(conn, "channel_account", snapshot["channel_id"])
    if channel["capability_version"] != "sim-a-v1":
        fail("CHANNEL_CAPABILITY_CHANGED")
    if snapshot["expires_at"] <= now():
        fail("SNAPSHOT_EXPIRED")
    _, _, decisions, versions = dependencies(conn, snapshot["activation_id"])
    versions["channel_codes"] = channel_codes(conn, snapshot["channel_id"], decisions)
    if (
        versions != snapshot["input_versions"]
        or digest(snapshot["payload"]) != snapshot["payload_hash"]
    ):
        fail("SNAPSHOT_STALE")
    if approved:
        approval = conn.execute(
            "SELECT * FROM approval WHERE snapshot_id=%s AND decision='APPROVED'", (snapshot["id"],)
        ).fetchone()
        if (
            snapshot["status"] != "APPROVED"
            or not approval
            or approval["expires_at"] <= now()
            or approval["payload_hash"] != snapshot["payload_hash"]
        ):
            fail("APPROVAL_REQUIRED_OR_EXPIRED")


def channel_codes(conn, channel_id, decisions):
    result = {}
    rows = conn.execute(
        "SELECT * FROM channel_mapping WHERE channel_id=%s", (channel_id,)
    ).fetchall()
    for decision in decisions.values():
        target = decision["target_id"]
        row = next(
            (r for r in rows if target in [r["property_id"], r["room_id"], r["plan_id"]]), None
        )
        if not row:
            fail("CHANNEL_MAPPING_REQUIRED")
        result[str(target)] = row["external_code"]
    return result


def submit(conn, actor, sid, version):
    authorize(actor)
    snapshot = get(conn, "offer_snapshot", sid, True)
    check_version(snapshot, version)
    valid_snapshot(conn, snapshot)
    if snapshot["status"] != "COMPILED":
        fail("INVALID_SNAPSHOT_STATE")
    conn.execute(
        "UPDATE offer_snapshot SET status='SUBMITTED',submitted_by=%s,version=version+1 WHERE id=%s",
        (actor["subject"], sid),
    )
    audit(conn, actor, "snapshot.submitted", sid, "Submitted for independent review")
    return get(conn, "offer_snapshot", sid)


def decide(conn, actor, sid, body):
    authorize(actor, REVIEW)
    snapshot = get(conn, "offer_snapshot", sid, True)
    check_version(snapshot, body.expected_version)
    valid_snapshot(conn, snapshot)
    if snapshot["submitted_by"] == actor["subject"]:
        fail("SELF_APPROVAL_FORBIDDEN", 403)
    if snapshot["status"] != "SUBMITTED":
        fail("INVALID_SNAPSHOT_STATE")
    conn.execute(
        "INSERT INTO approval VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            uuid4(),
            actor["tenant_id"],
            sid,
            snapshot["payload_hash"],
            actor["subject"],
            body.decision,
            body.reason,
            snapshot["expires_at"],
        ),
    )
    conn.execute(
        "UPDATE offer_snapshot SET status=%s,version=version+1 WHERE id=%s", (body.decision, sid)
    )
    audit(conn, actor, "snapshot.decided", sid, body.reason)
    from services.core.g2.jobs import emit

    emit(
        conn,
        actor["tenant_id"],
        "approval.decided",
        sid,
        snapshot["version"] + 1,
        {"decision": body.decision},
    )
    return get(conn, "offer_snapshot", sid)


def provenance(conn, sid):
    snapshot = get(conn, "offer_snapshot", sid)
    activation = get(conn, "source_activation", snapshot["activation_id"])
    rows = conn.execute(
        "SELECT s.kind,s.record_number,s.data,a.id AS artifact_id,a.name,a.sha256 FROM staging_record s JOIN artifact a ON(s.tenant_id,s.artifact_id)=(a.tenant_id,a.id) WHERE s.batch_id=%s ORDER BY a.name,s.record_number",
        (activation["batch_id"],),
    ).fetchall()
    result = {
        "snapshot_id": sid,
        "payload_hash": snapshot["payload_hash"],
        "input_versions": snapshot["input_versions"],
        "activation": activation,
        "source_records": rows,
    }
    from services.core.g2.views import provenance as generation_provenance

    extra = generation_provenance(conn, snapshot["activation_id"])
    if extra:
        result.update(extra)
    return result
