"""Bounded version-edge traversal. Never execute business commands during replay."""

import hashlib
import json
import time
from collections import deque

from fastapi.encoders import jsonable_encoder
from psycopg import sql

from services.core.g1.common import digest, fail
from services.core.g5.security import redact

ROOTS = {
    "listing": "g3_listing",
    "snapshot": "g3_snapshot",
    "run": "g4_run",
    "batch": "import_batch",
}
TABLES = [
    "property_version",
    "room_version",
    "policy_version",
    "g3_listing",
    "g3_intent",
    "g3_task",
    "g3_receipt",
    "g3_observation",
    "g3_diff",
    "g3_stop",
    "g3_dead_letter",
    "g3_plan",
    "g3_snapshot",
    "g3_allocation",
    "offer_snapshot",
    "approval",
    "source_activation",
    "import_batch",
    "artifact",
    "supplier_entity_version",
    "property_mapping",
    "room_mapping",
    "rate_mapping",
    "mapping_decision",
    "g4_policy",
    "g4_subscription",
    "g4_run",
    "g4_step",
    "g4_decision",
    "g4_mapping_reuse",
    "g4_ai_task",
    "g4_ai_attempt",
    "g4_matching_feedback",
    "processing_run",
    "processing_record",
    "source_generation",
    "generation_fact",
    "staging_record",
    "outbox_event",
    "consumer_inbox",
    "execution_job",
    "job_attempt",
    "workflow_run",
    "workflow_step",
    "workflow_wait",
    "task_attempt",
    "channel_receipt",
    "publish_task",
]
# Reverse only meaningful evidence edges, not tenant/supplier/channel shared roots.
REVERSE = {
    "source_generation": [("generation_fact", "generation_id")],
    "g3_plan": [("g4_run", "plan_id"), ("g3_intent", "plan_id")],
    "g3_listing": [
        ("g3_intent", "listing_id"),
        ("g3_observation", "listing_id"),
        ("g3_diff", "listing_id"),
        ("g3_stop", "listing_id"),
    ],
    "g3_intent": [("g3_task", "intent_id")],
    "g3_task": [("g3_receipt", "task_id"), ("g3_dead_letter", "task_id")],
    "g3_snapshot": [("g4_decision", "snapshot_id"), ("g3_intent", "snapshot_id")],
    "g4_run": [("g4_step", "run_id"), ("g4_decision", "run_id"), ("g4_mapping_reuse", "run_id")],
    "g4_ai_task": [("g4_ai_attempt", "task_id")],
    "import_batch": [
        ("artifact", "batch_id"),
        ("processing_run", "batch_id"),
        ("source_activation", "batch_id"),
    ],
    "source_activation": [
        ("supplier_entity_version", "activation_id"),
    ],
    "supplier_entity_version": [
        ("property_mapping", "source_id"),
        ("room_mapping", "source_id"),
        ("rate_mapping", "source_id"),
        ("g4_matching_feedback", "source_id"),
        ("g4_ai_task", "source_id"),
        ("mapping_decision", "source_id"),
    ],
    "processing_run": [("workflow_run", "processing_id")],
    "execution_job": [("job_attempt", "job_id")],
    "outbox_event": [("consumer_inbox", "event_id")],
}


def replay(c, kind, resource_id, limit=400):
    if kind not in ROOTS:
        fail("INVALID_AUDIT_KIND", 422)
    c.execute("SET LOCAL statement_timeout='3000ms'")
    root = ROOTS[kind]
    if (
        c.execute(
            sql.SQL("SELECT 1 FROM {} WHERE id=%s").format(sql.Identifier(root)), (resource_id,)
        ).fetchone()
        is None
    ):
        fail("RESOURCE_NOT_FOUND", 404)
    columns = {}
    for row in c.execute(
        "SELECT table_name,column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=ANY(%s)",
        (TABLES,),
    ).fetchall():
        columns.setdefault(row["table_name"], set()).add(row["column_name"])
    edges = c.execute(
        "SELECT child.relname AS child,parent.relname AS parent,a.attname AS field FROM pg_constraint con JOIN pg_class child ON child.oid=con.conrelid JOIN pg_class parent ON parent.oid=con.confrelid CROSS JOIN LATERAL unnest(con.conkey,con.confkey) AS k(src,dst) JOIN pg_attribute a ON a.attrelid=child.oid AND a.attnum=k.src JOIN pg_attribute b ON b.attrelid=parent.oid AND b.attnum=k.dst WHERE con.contype='f' AND child.relname=ANY(%s) AND parent.relname=ANY(%s) AND b.attname='id' AND a.attname<>'tenant_id'",
        (TABLES, TABLES),
    ).fetchall()
    queue = deque([(root, resource_id)])
    nodes, links, missing, seen = [], [], [], set()
    deadline = time.monotonic() + 5
    byte_budget = 5 * 1024 * 1024
    used_bytes = 0
    budget_truncated = False
    while queue and len(nodes) < limit:
        if time.monotonic() > deadline:
            budget_truncated = True
            break
        table, rid = queue.popleft()
        key = (table, str(rid))
        if key in seen:
            continue
        seen.add(key)
        row = c.execute(
            sql.SQL("SELECT * FROM {} WHERE id=%s").format(sql.Identifier(table)), (rid,)
        ).fetchone()
        if row is None:
            missing.append({"table": table, "id": str(rid)})
            continue
        encoded = jsonable_encoder(row)
        used_bytes += len(json.dumps(encoded, ensure_ascii=False).encode())
        if used_bytes > byte_budget:
            budget_truncated = True
            break
        status = "NOT_APPLICABLE"
        if "payload" in row and "payload_hash" in row:
            status = "VALID" if digest(row["payload"]) == row["payload_hash"] else "MISMATCH"
        nodes.append(
            {
                "table": table,
                "id": str(rid),
                "hash_status": status,
                "data": redact(encoded),
                "correlation": "VERSION_EDGES; historical trace where recorded",
            }
        )
        if table == "source_activation":
            for linked in c.execute(
                "SELECT generation_id FROM generation_activation WHERE activation_id=%s", (rid,)
            ).fetchall():
                queue.append(("source_generation", linked["generation_id"]))
                links.append(
                    {
                        "from": f"source_activation/{rid}",
                        "to": f"source_generation/{linked['generation_id']}",
                        "field": "generation_activation",
                    }
                )
        for edge in edges:
            if edge["child"] == table and row.get(edge["field"]):
                target = (edge["parent"], row[edge["field"]])
                links.append(
                    {
                        "from": f"{table}/{rid}",
                        "to": f"{target[0]}/{target[1]}",
                        "field": edge["field"],
                    }
                )
                queue.append(target)
        for child, field in REVERSE.get(table, []):
            if field not in columns.get(child, set()):
                continue
            rows = c.execute(
                sql.SQL("SELECT id FROM {} WHERE {}=%s ORDER BY id LIMIT %s").format(
                    sql.Identifier(child), sql.Identifier(field)
                ),
                (rid, limit + 1),
            ).fetchall()
            for other in rows:
                links.append(
                    {"from": f"{child}/{other['id']}", "to": f"{table}/{rid}", "field": field}
                )
                queue.append((child, other["id"]))
    truncated = budget_truncated or any((t, str(i)) not in seen for t, i in queue)
    return {
        "schema": "g5-audit-v1",
        "root": {"kind": kind, "id": str(resource_id)},
        "complete": not truncated and not missing,
        "truncated": truncated,
        "limit": limit,
        "byte_budget": byte_budget,
        "time_budget_seconds": 5,
        "missing": missing,
        "nodes": nodes,
        "edges": links,
        "provenance_note": "staging record_number is a projection/logical record, never a physical CSV line. processing_record.locator retains original JSON path / XLSX sheet,row / CSV logical record.",
    }


def evidence_package(data):
    body = json.dumps(
        data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    if len(body) > 5 * 1024 * 1024:
        fail("AUDIT_EXPORT_TOO_LARGE", 413)
    return {
        "manifest": {
            "schema": "g5-export-v1",
            "complete": data["complete"],
            "sha256": hashlib.sha256(body).hexdigest(),
            "bytes": len(body),
            "trust": "content integrity only; not third-party signed",
            "redacted_keys": ["secrets", "storage_key"],
        },
        "evidence": data,
    }


def verify_package(package):
    body = json.dumps(
        package["evidence"],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return (
        len(body) == package["manifest"]["bytes"]
        and hashlib.sha256(body).hexdigest() == package["manifest"]["sha256"]
    )
