"""Tenant-scoped frozen evidence and leased advisory jobs. No mapping decisions."""

import math
import unicodedata
from difflib import SequenceMatcher
from uuid import uuid4

from services.core.g1 import matching
from services.core.g1.common import authorize, digest, fail, get, j
from services.core.g4.local_model import REVISION

FIELDS = {
    "name",
    "country_code",
    "city",
    "address",
    "bed_type",
    "max_adults",
    "meal",
    "payment",
    "tax_basis",
    "cancellation",
}


def text(data):
    return " | ".join(str(data[k]) for k in ["name", "city", "address"] if data.get(k))[:2000]


def normalized(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def package(c, source_id):
    source = get(c, "supplier_entity_version", source_id)
    staging = get(c, "staging_record", source["staging_id"])
    candidates = []
    for target in matching.targets(c, source):
        feedback = c.execute(
            "SELECT decision FROM g4_matching_feedback WHERE kind=%s AND source_hash=%s AND target_id=%s AND target_version_id=%s ORDER BY created_at DESC LIMIT 1",
            (
                source["kind"],
                source["facts_hash"],
                target["target_id"],
                target["target_version_id"],
            ),
        ).fetchone()
        if feedback and feedback["decision"] == "REJECTED":
            continue
        facts = {k: v for k, v in target.items() if k in FIELDS}
        if source["kind"] == "property" and (
            facts.get("country_code") != source["data"].get("country_code")
            or facts.get("city") != source["data"].get("city")
        ):
            continue
        hard = matching.conflicts(c, source, target)
        score = SequenceMatcher(
            None, normalized(text(source["data"])), normalized(text(facts))
        ).ratio()
        candidates.append(
            {
                "id": str(target["target_id"]),
                "version": str(target["target_version_id"]),
                "facts": facts,
                "hard_conflicts": hard,
                "rule_score": score,
            }
        )
    candidates.sort(key=lambda x: (bool(x["hard_conflicts"]), -x["rule_score"], x["id"]))
    candidates = candidates[:10]
    return {
        "source_id": str(source_id),
        "source_hash": source["facts_hash"],
        "source_record": {
            "staging_id": str(staging["id"]),
            "artifact_id": str(staging["artifact_id"]),
            "record_number": staging["record_number"],
            "record_hash": digest(staging["data"]),
        },
        "activation_id": str(source["activation_id"]),
        "entity_type": source["kind"],
        "source": {k: v for k, v in source["data"].items() if k in FIELDS},
        "candidates": candidates,
        "candidate_set_hash": digest(candidates),
        "rules": "g4-match-v1",
        "normalizer": "nfkc-v1",
        "model_revision": REVISION,
        "adapter": "onnx-mean-pool-v1",
        "prompt_version": None,
    }


def create(c, actor, source_id):
    authorize(actor)
    inputs = package(c, source_id)
    hashed = digest({"tenant": str(actor["tenant_id"]), "input": inputs})
    cached = c.execute("SELECT * FROM g4_ai_task WHERE input_hash=%s", (hashed,)).fetchone()
    if cached:
        return cached
    c.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        (str(actor["tenant_id"]) + ":g4-ai-budget",),
    )
    if (
        c.execute(
            "SELECT count(*) AS n FROM g4_ai_task WHERE state IN ('READY','RUNNING')"
        ).fetchone()["n"]
        >= 100
    ):
        fail("AI_QUEUE_BUDGET_EXHAUSTED", 429)
    tid = uuid4()
    c.execute(
        "INSERT INTO g4_ai_task(id,tenant_id,source_id,input_hash,input) VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
        (tid, actor["tenant_id"], source_id, hashed, j(inputs)),
    )
    return c.execute("SELECT * FROM g4_ai_task WHERE input_hash=%s", (hashed,)).fetchone()


def validate(inputs, scores):
    if len(scores) != len(inputs["candidates"]) or any(
        type(s) not in {int, float} or not math.isfinite(s) or abs(s) > 1.00001 for s in scores
    ):
        fail("AI_OUTPUT_SCHEMA", 422)
    ranked = []
    for candidate, score in zip(inputs["candidates"], scores, strict=True):
        evidence = [
            {
                "field": k,
                "source_path": "data." + k,
                "quote": str(v),
                "source_hash": inputs["source_hash"],
            }
            for k, v in inputs["source"].items()
        ]
        ranked.append(
            {
                **candidate,
                "rank_score": score,
                "score_kind": "COSINE_SIMILARITY",
                "decision_hint": "CONFLICT" if candidate["hard_conflicts"] else "REVIEW",
                "evidence": evidence,
                "unknown_fields": [
                    k for k in ["name", "city", "address"] if not inputs["source"].get(k)
                ],
            }
        )
    ranked.sort(key=lambda x: (bool(x["hard_conflicts"]), -x["rank_score"], x["id"]))
    return ranked


def tick(db, tenant, owner, model, task_ids=None):
    with db.pool.connection() as mutex:
        acquired = mutex.execute(
            "SELECT pg_try_advisory_lock(hashtextextended('g4-ai-global-runtime',0)) AS ok"
        ).fetchone()["ok"]
        mutex.commit()
        if not acquired:
            return False
        try:
            return _tick(db, tenant, owner, model, task_ids)
        finally:
            mutex.execute("SELECT pg_advisory_unlock(hashtextextended('g4-ai-global-runtime',0))")
            mutex.commit()


def _tick(db, tenant, owner, model, task_ids=None):
    with db.transaction(tenant) as c:
        row = c.execute(
            "SELECT * FROM g4_ai_task WHERE (state='READY' OR state='RUNNING' AND lease_until<clock_timestamp()) AND (%s::uuid[] IS NULL OR id=ANY(%s::uuid[])) ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED",
            (task_ids, task_ids),
        ).fetchone()
        if not row:
            return False
        c.execute(
            "UPDATE g4_ai_task SET state='RUNNING',owner=%s,fence=fence+1,lease_until=clock_timestamp()+interval '180 seconds',attempts=attempts+1 WHERE id=%s",
            (owner, row["id"]),
        )
        row = get(c, "g4_ai_task", row["id"])
    detail = {}
    try:
        if row["attempts"] > 2:
            raise RuntimeError("AI_BUDGET_EXHAUSTED")
        inputs = row["input"]
        if not inputs["candidates"]:
            result = {
                "status": "RULE_FALLBACK",
                "fallback_reason": "NO_CANDIDATES",
                "ranked_candidates": [],
            }
        elif inputs["entity_type"] != "property":
            result = {
                "status": "RULE_FALLBACK",
                "fallback_reason": "ENTITY_RULE_ONLY",
                "ranked_candidates": validate(
                    inputs, [x["rule_score"] for x in inputs["candidates"]]
                ),
            }
        else:
            detail = model.rank(
                [text(inputs["source"]), *[text(x["facts"]) for x in inputs["candidates"]]]
            )
            result = {
                "status": "AI_AVAILABLE",
                "ranked_candidates": validate(inputs, detail["scores"]),
                "runtime": detail,
            }
    except Exception as exc:
        result = {
            "status": "RULE_FALLBACK",
            "fallback_reason": type(exc).__name__ + ":" + str(exc)[:80],
            "ranked_candidates": validate(
                row["input"], [x["rule_score"] for x in row["input"]["candidates"]]
            ),
        }
    with db.transaction(tenant) as c:
        current = get(c, "g4_ai_task", row["id"], True)
        clock = c.execute("SELECT clock_timestamp() AS clock").fetchone()["clock"]
        if (
            current["owner"] != owner
            or current["fence"] != row["fence"]
            or current["lease_until"] <= clock
        ):
            return True
        fresh = package(c, row["source_id"])
        head = c.execute(
            "SELECT l.activation_id FROM source_head h JOIN generation_activation l ON l.generation_id=h.generation_id JOIN source_activation a ON a.supplier_id=h.supplier_id AND a.property_code=h.property_code WHERE a.id=%s",
            (row["input"]["activation_id"],),
        ).fetchone()
        if (
            digest(fresh) != digest(row["input"])
            or head
            and str(head["activation_id"]) != row["input"]["activation_id"]
        ):
            result["status"] = "STALE"
        c.execute(
            "INSERT INTO g4_ai_attempt(id,tenant_id,task_id,fence,status,detail) VALUES(%s,%s,%s,%s,%s,%s)",
            (uuid4(), tenant, row["id"], row["fence"], result["status"], j(result)),
        )
        c.execute(
            "UPDATE g4_ai_task SET state=%s,result=%s WHERE id=%s",
            ("STALE" if result["status"] == "STALE" else "COMPLETED", j(result), row["id"]),
        )
    return True
