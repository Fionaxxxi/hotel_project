from datetime import date, timedelta
from uuid import uuid4

from services.core.file_contract import KEYS, REQUIRED
from services.core.g1 import ingestion
from services.core.g1.common import audit, authorize, digest, fail, get, j
from services.core.g2 import formats, jobs


def dates(scope):
    a, b = map(date.fromisoformat, [scope["stay_date_from"], scope["stay_date_to_exclusive"]])
    return [(a + timedelta(days=i)).isoformat() for i in range((b - a).days)]


def versions(c, supplier, prop):
    rows = c.execute(
        "SELECT * FROM stable_partition WHERE supplier_id=%s AND property_code=%s ORDER BY partition_key",
        (supplier, prop),
    ).fetchall()
    if rows:
        return {
            r["partition_key"]: {"version": r["version"], "sequence": r["source_sequence"]}
            for r in rows
        }
    legacy = c.execute(
        "SELECT a.* FROM source_partition p JOIN source_activation a ON(p.tenant_id,p.activation_id)=(a.tenant_id,a.id) WHERE p.supplier_id=%s AND p.property_code=%s",
        (supplier, prop),
    ).fetchall()
    result = {}
    for a in legacy:
        for key in ["MASTER", *dates(a["scope"])]:
            if key in result and result[key]["sequence"] != a["source_sequence"]:
                fail("LEGACY_PARTITION_AMBIGUOUS")
            result[key] = {"version": 1, "sequence": a["source_sequence"]}
    return result


def bootstrap(c, actor, supplier, prop):
    head = c.execute(
        "SELECT * FROM source_head WHERE supplier_id=%s AND property_code=%s", (supplier, prop)
    ).fetchone()
    if head:
        return get(c, "source_generation", head["generation_id"])
    legacy = c.execute(
        "SELECT a.* FROM source_partition p JOIN source_activation a ON(p.tenant_id,p.activation_id)=(a.tenant_id,a.id) WHERE p.supplier_id=%s AND p.property_code=%s ORDER BY a.created_at",
        (supplier, prop),
    ).fetchall()
    if not legacy:
        return None
    oldfacts = {}
    v = versions(c, supplier, prop)
    for activation in legacy:
        for row in c.execute(
            "SELECT * FROM staging_record WHERE batch_id=%s", (activation["batch_id"],)
        ).fetchall():
            data = formats.normalize(row["kind"], row["data"], "csv", "snapshot")
            key = (row["kind"], formats.natural_key(row["kind"], data))
            if key in oldfacts and oldfacts[key]["data"] != data:
                fail("LEGACY_FACT_CONFLICT")
            oldfacts[key] = {
                "data": data,
                "legacy_staging_id": row["id"],
                "origin_record_id": None,
                "tombstone": False,
            }
    scope = {
        **legacy[0]["scope"],
        "stay_date_from": min(a["scope"]["stay_date_from"] for a in legacy),
        "stay_date_to_exclusive": max(a["scope"]["stay_date_to_exclusive"] for a in legacy),
    }
    gid = uuid4()
    c.execute(
        "INSERT INTO source_generation(id,tenant_id,supplier_id,property_code,scope,view_hash) VALUES(%s,%s,%s,%s,%s,%s)",
        (
            gid,
            actor["tenant_id"],
            supplier,
            prop,
            j(scope),
            digest({str(k): f["data"] for k, f in sorted(oldfacts.items())}),
        ),
    )
    insert_facts(c, actor["tenant_id"], gid, oldfacts)
    c.execute(
        "INSERT INTO source_head(id,tenant_id,supplier_id,property_code,generation_id) VALUES(%s,%s,%s,%s,%s)",
        (uuid4(), actor["tenant_id"], supplier, prop, gid),
    )
    for key, value in v.items():
        c.execute(
            "INSERT INTO stable_partition VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                uuid4(),
                actor["tenant_id"],
                supplier,
                prop,
                "demo-v1",
                key,
                value["version"],
                value["sequence"],
                gid,
            ),
        )
    audit(c, actor, "source.legacy.adopted", gid, "Historical snapshots unchanged")
    return get(c, "source_generation", gid)


def insert_facts(c, tenant, gid, facts):
    with c.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO generation_fact VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            [
                (
                    uuid4(),
                    tenant,
                    gid,
                    kind,
                    key,
                    j(f["data"]),
                    f["origin_record_id"],
                    f["legacy_staging_id"],
                    f["tombstone"],
                )
                for (kind, key), f in facts.items()
            ],
        )


def authority_prefix(kind, data):
    return [data[k] for k in KEYS[kind] if k != "stay_date"]


def merge(c, run, manifest, parent, selected):
    facts = {}
    if parent:
        for f in c.execute(
            "SELECT * FROM generation_fact WHERE generation_id=%s", (parent["id"],)
        ).fetchall():
            facts[(f["kind"], f["natural_key"])] = f
    entities = manifest["scope"]["entities"]
    authority = manifest["scope"]["authority"]
    changed = {}
    for row in c.execute(
        "SELECT * FROM processing_record WHERE run_id=%s AND is_valid ORDER BY kind,position",
        (run["id"],),
    ).fetchall():
        d = row["data"]
        kind = row["kind"]
        day = formats.partition(d)
        if day == "MASTER" and run["status"] == "ACTIVE":
            previous = facts.get((kind, row["natural_key"]))
            if not previous or previous["data"] != d:
                fail("MASTER_BASE_CHANGED")
            continue
        if day != "MASTER" and day not in selected:
            continue
        if (
            manifest["mode"] == "snapshot"
            and kind in {"inventory", "rates"}
            and authority_prefix(kind, d) not in authority[kind]
        ):
            fail("RECORD_OUTSIDE_AUTHORITY", 422)
        changed[(kind, row["natural_key"])] = {
            "data": d,
            "origin_record_id": row["id"],
            "legacy_staging_id": None,
            "tombstone": d["operation"] == "delete",
        }
    if manifest["mode"] == "snapshot":
        for key, f in facts.items():
            kind = key[0]
            d = f["data"]
            if (
                kind in {"inventory", "rates"}
                and kind in entities
                and d["stay_date"] in selected
                and authority_prefix(kind, d) in authority[kind]
                and key not in changed
            ):
                f = {**f, "tombstone": True}
                facts[key] = f
    facts.update(changed)
    active = {kind: [] for kind in REQUIRED}
    for (kind, _), f in facts.items():
        if not f["tombstone"]:
            active[kind].append(f["data"])
    prop = {r["property_code"] for r in active["properties"]}
    rooms = {(r["property_code"], r["room_code"]) for r in active["rooms"]}
    plans = {(r["property_code"], r["room_code"], r["plan_code"]) for r in active["rate_plans"]}
    pools = {
        (r["property_code"], r["room_code"], r["pool_code"], r["stay_date"])
        for r in active["inventory"]
    }
    for r in active["rooms"]:
        if r["property_code"] not in prop:
            fail("PARENT_NOT_FOUND", 422)
    for r in active["rate_plans"]:
        if (r["property_code"], r["room_code"]) not in rooms:
            fail("PARENT_NOT_FOUND", 422)
    for r in active["inventory"]:
        if (r["property_code"], r["room_code"]) not in rooms:
            fail("PARENT_NOT_FOUND", 422)
    per_plan = {}
    for r in active["rates"]:
        p = (r["property_code"], r["room_code"], r["plan_code"])
        if (
            p not in plans
            or (r["property_code"], r["room_code"], r["pool_code"], r["stay_date"]) not in pools
        ):
            fail("RATE_REFERENCE_INVALID", 422)
        if p in per_plan and per_plan[p] != r["pool_code"]:
            fail("UNSTABLE_PLAN_POOL", 422)
        per_plan[p] = r["pool_code"]
    if not prop or not rooms or not plans:
        fail("MASTER_BASELINE_REQUIRED", 422)
    if len(facts) > 300000:
        fail("MATERIALIZED_VIEW_LIMIT", 422)
    return facts


def activate(c, actor, rid, body, key):
    authorize(actor)
    run = get(c, "processing_run", rid, True)
    batch = get(c, "import_batch", run["batch_id"])
    m = batch["manifest"]
    scope = m["scope"]
    if run["status"] not in {"READY_TO_ACTIVATE", "QUALITY_REVIEW", "ACTIVE"}:
        fail("PROCESSING_NOT_READY")
    request_hash = digest(body)
    prior = c.execute(
        "SELECT * FROM activation_command WHERE run_id=%s AND key=%s", (rid, key)
    ).fetchone()
    if prior:
        if prior["request_hash"] != request_hash:
            fail("IDEMPOTENCY_CONFLICT")
        previous = get(c, "source_generation", prior["generation_id"])
        link = c.execute(
            "SELECT activation_id FROM generation_activation WHERE generation_id=%s",
            (prior["generation_id"],),
        ).fetchone()
        return {**previous, "activation_id": link["activation_id"]}
    if run["version"] != body["expected_run_version"]:
        fail("VERSION_CONFLICT")
    selected = body["selected_dates"]
    if (
        not selected
        or len(selected) != len(set(selected))
        or not set(selected) <= set(dates(scope))
    ):
        fail("INVALID_EFFECTIVE_SCOPE", 422)
    if set(selected) != set(dates(scope)) and body.get("partial_confirmed") is not True:
        fail("PARTIAL_SCOPE_CONFIRMATION_REQUIRED", 422)
    bad = c.execute(
        "SELECT DISTINCT partition_key FROM dq_issue WHERE run_id=%s", (rid,)
    ).fetchall()
    if any(r["partition_key"] in {"*", "MASTER", *selected} for r in bad):
        fail("QUALITY_PARTITION_BLOCKED")
    supplier = batch["supplier_id"]
    prop = scope["property_codes"][0]
    c.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        (str(actor["tenant_id"]) + str(supplier) + prop,),
    )
    parent = bootstrap(c, actor, supplier, prop)
    touched = sorted(
        [
            *selected,
            *(
                ["MASTER"]
                if any(e in scope["entities"] for e in ["properties", "rooms", "rate_plans"])
                else []
            ),
        ]
    )
    if run["status"] == "ACTIVE" and "MASTER" in touched:
        # Further explicitly selected dates from this same parsed run do not re-apply MASTER.
        touched.remove("MASTER")
    current = versions(c, supplier, prop)
    for part in touched:
        expected = body["expected_versions"].get(part)
        if expected is None or expected != current.get(part, {}).get("version", 0):
            fail("ACTIVE_VERSION_CONFLICT")
        if m["source_sequence"] <= current.get(part, {}).get("sequence", 0):
            fail("SOURCE_SEQUENCE_CONFLICT")
    facts = merge(c, run, m, parent, selected)
    merged_scope = {
        "property_codes": [prop],
        "entities": list(REQUIRED),
        "stay_date_from": min(scope["stay_date_from"], parent["scope"]["stay_date_from"])
        if parent
        else scope["stay_date_from"],
        "stay_date_to_exclusive": max(
            scope["stay_date_to_exclusive"], parent["scope"]["stay_date_to_exclusive"]
        )
        if parent
        else scope["stay_date_to_exclusive"],
    }
    gid = uuid4()
    view_hash = digest(
        {str(k): {"data": f["data"], "deleted": f["tombstone"]} for k, f in sorted(facts.items())}
    )
    c.execute(
        "INSERT INTO source_generation(id,tenant_id,supplier_id,property_code,parent_id,run_id,scope,view_hash) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            gid,
            actor["tenant_id"],
            supplier,
            prop,
            parent["id"] if parent else None,
            rid,
            j(merged_scope),
            view_hash,
        ),
    )
    insert_facts(c, actor["tenant_id"], gid, facts)
    aid = materialize(c, actor, gid, m, supplier, merged_scope, facts)
    # Activation ID is assigned at INSERT, so immutable generation metadata is not updated.
    c.execute("SELECT set_config('app.generation_id',%s,true)", (str(gid),))
    c.execute("INSERT INTO generation_activation VALUES(%s,%s,%s)", (actor["tenant_id"], gid, aid))
    c.execute(
        "INSERT INTO source_head(id,tenant_id,supplier_id,property_code,generation_id) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(tenant_id,supplier_id,property_code) DO UPDATE SET generation_id=excluded.generation_id,version=source_head.version+1",
        (uuid4(), actor["tenant_id"], supplier, prop, gid),
    )
    for part in touched:
        c.execute(
            "INSERT INTO stable_partition VALUES(%s,%s,%s,%s,%s,%s,1,%s,%s) ON CONFLICT(tenant_id,supplier_id,property_code,stream,partition_key) DO UPDATE SET version=stable_partition.version+1,source_sequence=excluded.source_sequence,generation_id=excluded.generation_id",
            (
                uuid4(),
                actor["tenant_id"],
                supplier,
                prop,
                m["sequence_stream"],
                part,
                m["source_sequence"],
                gid,
            ),
        )
    c.execute(
        "INSERT INTO activation_command VALUES(%s,%s,%s,%s,%s,%s)",
        (uuid4(), actor["tenant_id"], rid, key, request_hash, gid),
    )
    c.execute("UPDATE processing_run SET status='ACTIVE',version=version+1 WHERE id=%s", (rid,))
    c.execute(
        "UPDATE import_batch SET status='ACTIVE',version=version+1 WHERE id=%s", (batch["id"],)
    )
    audit(
        c,
        actor,
        "g2.source.activated",
        gid,
        "Selected partitions + generation + outbox committed together",
    )
    jobs.emit(
        c,
        actor["tenant_id"],
        "source.activated",
        rid,
        run["version"] + 1,
        {"activation_id": str(aid), "generation_id": str(gid)},
    )
    return {**get(c, "source_generation", gid), "activation_id": aid}


def materialize(c, actor, gid, manifest, supplier, scope, facts):
    bid = uuid4()
    m = {**manifest, "schema_version": "1.0", "scope": scope, "mode": "snapshot"}
    c.execute(
        "INSERT INTO import_batch(id,tenant_id,supplier_id,package_hash,manifest,status,created_by) VALUES(%s,%s,%s,%s,%s,'READY','system.g2-projection')",
        (bid, actor["tenant_id"], supplier, str(gid), j(m)),
    )
    rows = []
    for number, ((kind, _), fact) in enumerate(sorted(facts.items()), 2):
        if fact["tombstone"]:
            continue
        if fact["origin_record_id"]:
            origin = get(c, "processing_record", fact["origin_record_id"])
        else:
            origin = get(c, "staging_record", fact["legacy_staging_id"])
        data = {k: v for k, v in fact["data"].items() if k != "operation"}
        rows.append(
            (uuid4(), actor["tenant_id"], bid, origin["artifact_id"], kind, number, j(data))
        )
    with c.cursor() as cursor:
        cursor.executemany("INSERT INTO staging_record VALUES(%s,%s,%s,%s,%s,%s,%s)", rows)
    return ingestion.activate(c, actor, bid, 1, g2_projection=True)["id"]


def provenance(c, aid):
    link = c.execute(
        "SELECT generation_id FROM generation_activation WHERE activation_id=%s", (aid,)
    ).fetchone()
    if not link:
        return None
    gen = get(c, "source_generation", link["generation_id"])
    result = []
    for f in c.execute(
        "SELECT * FROM generation_fact WHERE generation_id=%s ORDER BY kind,natural_key",
        (gen["id"],),
    ).fetchall():
        if f["origin_record_id"]:
            r = get(c, "processing_record", f["origin_record_id"])
            loc = r["locator"]
            data = r["raw"]
        else:
            r = get(c, "staging_record", f["legacy_staging_id"])
            loc = (
                {"record": r["source_row"]} if "source_row" in r else {"record": r["record_number"]}
            )
            data = r["data"]
        artifact = get(c, "artifact", r["artifact_id"])
        result.append(
            {
                "kind": f["kind"],
                "artifact_id": artifact["id"],
                "name": artifact["name"],
                "sha256": artifact["sha256"],
                "location": loc,
                "record_number": r.get("position", r.get("record_number")),
                "data": data,
                "tombstone": f["tombstone"],
            }
        )
    return {"generation": gen, "source_records": result}
