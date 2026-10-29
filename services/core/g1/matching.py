import json
from uuid import uuid4

from psycopg import sql

from services.core.g1.common import REVIEW, audit, authorize, fail, get

TARGETS = {
    "property": ("property", "property_version", "property_id"),
    "room": ("room_type", "room_version", "room_id"),
    "rate": ("rate_plan", "policy_version", "plan_id"),
}


def mapping(conn, source):
    if source["kind"] not in TARGETS:
        fail("INVALID_ENTITY_KIND", 422)
    return conn.execute(
        sql.SQL("SELECT * FROM {} WHERE source_id=%s").format(
            sql.Identifier(source["kind"] + "_mapping")
        ),
        (source["id"],),
    ).fetchone()


def parent_target(conn, source):
    if source["kind"] == "property":
        return None
    parent_kind = "property" if source["kind"] == "room" else "room"
    parent = conn.execute(
        "SELECT * FROM supplier_entity_version WHERE activation_id=%s AND kind=%s AND natural_key=%s",
        (source["activation_id"], parent_kind, source["parent_key"]),
    ).fetchone()
    decision = mapping(conn, parent) if parent else None
    return decision["target_id"] if decision and decision["status"] == "APPROVED" else None


def targets(conn, source):
    entity, versions, parent_id = TARGETS[source["kind"]]
    return conn.execute(
        sql.SQL(
            "SELECT e.*,e.id AS target_id,e.current_version_id AS target_version_id,v.* FROM {} e JOIN {} v ON(e.tenant_id,e.id,e.current_version_id)=(v.tenant_id,v.{},v.id) ORDER BY e.id"
        ).format(sql.Identifier(entity), sql.Identifier(versions), sql.Identifier(parent_id))
    ).fetchall()


def conflicts(conn, source, target):
    d = source["data"]
    kind = source["kind"]
    result = []
    parent = parent_target(conn, source)
    if kind != "property" and parent is None:
        result.append("PARENT_MAPPING_REQUIRED")
    if kind == "property":
        if d["country_code"] != target["country_code"] or d["city"] != target["city"]:
            result.append("LOCATION_CONFLICT")
    elif kind == "room":
        if target["property_id"] != parent:
            result.append("PARENT_CONFLICT")
        if d["bed_type"] == "UNKNOWN" or d["bed_type"] != target["bed_type"]:
            result.append("BED_CONFLICT")
        if int(d["max_adults"]) != target["max_adults"]:
            result.append("OCCUPANCY_CONFLICT")
    else:
        if target["plan_id"]:
            room = get(conn, "rate_plan", target["target_id"])["room_id"]
            if room != parent:
                result.append("PARENT_CONFLICT")
        for field in ["meal", "payment", "tax_basis"]:
            if d[field] == "UNKNOWN" or d[field] != target[field]:
                result.append(field.upper() + "_CONFLICT")
        try:
            if json.loads(d["cancellation"]) != target["cancellation"]:
                result.append("CANCELLATION_CONFLICT")
        except ValueError:
            result.append("CANCELLATION_UNKNOWN")
    return result


def proposals(conn, activation_id):
    get(conn, "source_activation", activation_id)
    sources = conn.execute(
        "SELECT * FROM supplier_entity_version WHERE activation_id=%s ORDER BY CASE kind WHEN 'property' THEN 0 WHEN 'room' THEN 1 ELSE 2 END,natural_key",
        (activation_id,),
    ).fetchall()
    result = []
    for source in sources:
        candidates = []
        for target in targets(conn, source):
            hard = conflicts(conn, source, target)
            candidates.append(
                {
                    "target_id": target["target_id"],
                    "target_version_id": target["target_version_id"],
                    "name": target.get("name", target.get("raw_terms", "")),
                    "hard_conflicts": hard,
                    "score": 0 if hard else 1,
                    "facts": target,
                }
            )
        candidates.sort(key=lambda c: len(c["hard_conflicts"]))
        result.append(
            {
                **source,
                "mapping": mapping(conn, source),
                "candidates": candidates,
                "rule_version": "g1-rules-v1",
            }
        )
    return result


def decide(conn, actor, kind, source_id, body):
    authorize(actor, REVIEW)
    if kind not in TARGETS:
        fail("INVALID_ENTITY_KIND", 422)
    source = get(conn, "supplier_entity_version", source_id)
    if source["kind"] != kind:
        fail("ENTITY_KIND_MISMATCH", 422)
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (str(source_id),))
    current = mapping(conn, source)
    if body.expected_version != (current["version"] if current else 0):
        fail("VERSION_CONFLICT")
    target = next((t for t in targets(conn, source) if t["target_id"] == body.target_id), None)
    if not target:
        fail("TARGET_NOT_FOUND", 404)
    if body.decision == "APPROVED" and conflicts(conn, source, target):
        fail("HARD_CONFLICT:" + ",".join(conflicts(conn, source, target)), 422)
    tid = target["target_version_id"]
    if current:
        conn.execute(
            sql.SQL(
                "UPDATE {} SET target_id=%s,target_version_id=%s,status=%s,version=version+1,actor=%s,reason=%s WHERE id=%s"
            ).format(sql.Identifier(kind + "_mapping")),
            (body.target_id, tid, body.decision, actor["subject"], body.reason, current["id"]),
        )
    else:
        conn.execute(
            sql.SQL(
                "INSERT INTO {}(id,tenant_id,source_id,target_id,target_version_id,status,actor,reason) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)"
            ).format(sql.Identifier(kind + "_mapping")),
            (
                uuid4(),
                actor["tenant_id"],
                source_id,
                body.target_id,
                tid,
                body.decision,
                actor["subject"],
                body.reason,
            ),
        )
    conn.execute(
        "INSERT INTO mapping_decision(id,tenant_id,source_id,kind,decision,target_id,target_version_id,actor,reason) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            uuid4(),
            actor["tenant_id"],
            source_id,
            kind,
            body.decision,
            body.target_id,
            tid,
            actor["subject"],
            body.reason,
        ),
    )
    audit(conn, actor, "mapping.decided", source_id, body.reason)
    conn.execute(
        "INSERT INTO g4_matching_feedback(id,tenant_id,source_id,source_hash,kind,target_id,target_version_id,decision,actor,reason) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            uuid4(),
            actor["tenant_id"],
            source_id,
            source["facts_hash"],
            kind,
            target["target_id"],
            tid,
            body.decision,
            actor["subject"],
            body.reason,
        ),
    )
    result = mapping(conn, source)
    from services.core.g2.jobs import emit

    emit(
        conn,
        actor["tenant_id"],
        "mapping.decided",
        source_id,
        result["version"],
        {"decision": body.decision, "kind": kind},
    )
    return result
