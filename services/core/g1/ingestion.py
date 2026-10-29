import csv
import hashlib
import json
import os
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from services.core.file_contract import REQUIRED, validate_package
from services.core.g1.common import (
    audit,
    authorize,
    business_now,
    check_version,
    digest,
    fail,
    get,
    j,
)

ROOT = Path(__file__).resolve().parents[3]
STORE = Path(os.environ.get("HOTEL_ARTIFACT_ROOT", str(ROOT / ".runtime/artifacts")))


def batch_dir(batch_id):
    # batch_id comes from a UUID-validated route or server generation.
    return STORE / str(batch_id)


def upload(conn, actor, content):
    authorize(actor)
    expected = {"manifest.json", *[f"{k}.csv" for k in REQUIRED]}
    if set(content) != expected:
        fail("INVALID_FILE_SET", 422)
    try:
        manifest = json.loads(content["manifest.json"].decode("utf-8"))
    except (ValueError, UnicodeError):
        fail("MANIFEST_INVALID", 422)
    from jsonschema import Draft202012Validator, FormatChecker

    schema = json.loads(
        (ROOT / "contracts/files/v1/manifest.schema.json").read_text(encoding="utf-8")
    )
    if not Draft202012Validator(schema, format_checker=FormatChecker()).is_valid(manifest):
        fail("MANIFEST_SCHEMA", 422)
    if manifest["mode"] != "snapshot":
        fail("MODE_NOT_SUPPORTED_G1", 422)
    if len(manifest["scope"]["property_codes"]) != 1:
        fail("SINGLE_PROPERTY_REQUIRED_G1", 422)
    supplier = conn.execute(
        "SELECT id FROM supplier WHERE code=%s AND status='active'", (manifest["supplier_code"],)
    ).fetchone()
    if not supplier:
        fail("SUPPLIER_NOT_FOUND", 422)
    package_hash = digest(
        {name: hashlib.sha256(data).hexdigest() for name, data in content.items()}
    )
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        (str(actor["tenant_id"]) + str(supplier["id"]) + package_hash,),
    )
    existing = conn.execute(
        "SELECT id FROM import_batch WHERE supplier_id=%s AND package_hash=%s",
        (supplier["id"], package_hash),
    ).fetchone()
    if existing:
        return {"id": existing["id"], "reused": True}
    bid = uuid4()
    folder = batch_dir(bid)
    folder.mkdir(parents=True, exist_ok=False)
    try:
        for name, data in content.items():
            (folder / name).write_bytes(data)
        conn.execute(
            "INSERT INTO import_batch(id,tenant_id,supplier_id,package_hash,manifest,created_by) VALUES(%s,%s,%s,%s,%s,%s)",
            (bid, actor["tenant_id"], supplier["id"], package_hash, j(manifest), actor["subject"]),
        )
        for name, data in content.items():
            conn.execute(
                "INSERT INTO artifact VALUES(%s,%s,%s,%s,%s,%s,%s)",
                (
                    uuid4(),
                    actor["tenant_id"],
                    bid,
                    name,
                    f"{bid}/{name}",
                    hashlib.sha256(data).hexdigest(),
                    len(data),
                ),
            )
        audit(conn, actor, "import.uploaded", bid, "Immutable original files saved")
    except Exception:
        shutil.rmtree(folder)
        raise
    return {"id": bid, "reused": False}


def preview(conn, bid):
    batch = get(conn, "import_batch", bid)
    result = {**batch, "preview": {}}
    for artifact in conn.execute(
        "SELECT * FROM artifact WHERE batch_id=%s ORDER BY name", (bid,)
    ).fetchall():
        if artifact["name"].endswith(".csv"):
            try:
                with (STORE / artifact["storage_key"]).open(encoding="utf-8", newline="") as f:
                    rows = list(csv.DictReader(f))
                result["preview"][artifact["name"]] = {"total": len(rows), "rows": rows[:50]}
            except (ValueError, UnicodeError, csv.Error):
                result["preview"][artifact["name"]] = {"error": "FILE_INVALID"}
    return result


def confirm(conn, actor, bid, version):
    authorize(actor)
    batch = get(conn, "import_batch", bid, True)
    if batch["status"] in {"READY", "ACTIVE", "INVALID"}:
        return batch
    check_version(batch, version)
    issues = validate_package(batch_dir(bid))
    records = {}
    if not issues:
        for kind in REQUIRED:
            with (batch_dir(bid) / f"{kind}.csv").open(encoding="utf-8", newline="") as f:
                records[kind] = list(csv.DictReader(f))
            if len(records[kind]) > 5000:
                issues.append({"code": "RECORD_LIMIT", "file": f"{kind}.csv"})
        if not issues:
            issues = coverage_issues(batch["manifest"], records)
    if not issues:
        for kind, rows in records.items():
            artifact = conn.execute(
                "SELECT id FROM artifact WHERE batch_id=%s AND name=%s", (bid, f"{kind}.csv")
            ).fetchone()
            for number, row in enumerate(rows, 2):
                conn.execute(
                    "INSERT INTO staging_record VALUES(%s,%s,%s,%s,%s,%s,%s)",
                    (uuid4(), actor["tenant_id"], bid, artifact["id"], kind, number, j(row)),
                )
    status = "INVALID" if issues else "READY"
    conn.execute(
        "UPDATE import_batch SET status=%s,issues=%s,version=version+1 WHERE id=%s",
        (status, j(issues), bid),
    )
    audit(conn, actor, "import.validated", bid, status)
    return get(conn, "import_batch", bid)


def coverage_issues(manifest, records):
    scope = manifest["scope"]
    start, end = (
        date.fromisoformat(scope["stay_date_from"]),
        date.fromisoformat(scope["stay_date_to_exclusive"]),
    )
    if (end - start).days > 366 or end <= start:
        return [{"code": "INVALID_DATE_RANGE", "file": "manifest.json"}]
    dates = {(start + timedelta(days=i)).isoformat() for i in range((end - start).days)}
    issues = []
    for plan in records["rate_plans"]:
        rates = [
            r
            for r in records["rates"]
            if all(r[k] == plan[k] for k in ["property_code", "room_code", "plan_code"])
        ]
        if {r["stay_date"] for r in rates} != dates:
            issues.append(
                {"code": "INCOMPLETE_RATE_CALENDAR", "file": "rates.csv", "plan": plan["plan_code"]}
            )
        if len({r["pool_code"] for r in rates}) != 1:
            issues.append(
                {"code": "UNSTABLE_PLAN_POOL", "file": "rates.csv", "plan": plan["plan_code"]}
            )
    for key in {(r["property_code"], r["room_code"], r["pool_code"]) for r in records["inventory"]}:
        if {
            r["stay_date"]
            for r in records["inventory"]
            if tuple(r[k] for k in ["property_code", "room_code", "pool_code"]) == key
        } != dates:
            issues.append({"code": "INCOMPLETE_POOL_CALENDAR", "file": "inventory.csv"})
    return issues


def activate(conn, actor, bid, version, g2_projection=False):
    authorize(actor)
    batch = get(conn, "import_batch", bid, True)
    if batch["status"] == "ACTIVE":
        return conn.execute("SELECT * FROM source_activation WHERE batch_id=%s", (bid,)).fetchone()
    check_version(batch, version)
    if batch["status"] != "READY":
        fail("IMPORT_NOT_READY")
    manifest = batch["manifest"]
    scope = manifest["scope"]
    prop = scope["property_codes"][0]
    if (
        not g2_projection
        and conn.execute(
            "SELECT 1 FROM source_head WHERE supplier_id=%s AND property_code=%s",
            (batch["supplier_id"], prop),
        ).fetchone()
    ):
        fail("USE_V2_ACTIVATION_FOR_MANAGED_SOURCE")
    # Lock also covers first partition creation and overlapping-scope checks.
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        (str(actor["tenant_id"]) + str(batch["supplier_id"]) + prop,),
    )
    partitions = conn.execute(
        "SELECT p.*,a.scope,a.source_sequence,a.batch_id FROM source_partition p JOIN source_activation a ON(p.tenant_id,p.activation_id)=(a.tenant_id,a.id) WHERE p.supplier_id=%s AND p.property_code=%s FOR UPDATE OF p",
        (batch["supplier_id"], prop),
    ).fetchall()
    current = None
    scope_hash = digest({**scope, "entities": sorted(scope["entities"])})
    for p in partitions:
        if p["scope_hash"] == scope_hash:
            current = p
        elif not g2_projection and (
            scope["stay_date_from"] < p["scope"]["stay_date_to_exclusive"]
            and p["scope"]["stay_date_from"] < scope["stay_date_to_exclusive"]
        ):
            fail("OVERLAPPING_SCOPE_NOT_SUPPORTED_G1")
    if g2_projection and not current and partitions:
        current = partitions[0]
    if not g2_projection and current and manifest["source_sequence"] <= current["source_sequence"]:
        fail("SOURCE_SEQUENCE_CONFLICT")
    aid = uuid4()
    tenant = actor["tenant_id"]
    conn.execute(
        "INSERT INTO source_activation(id,tenant_id,batch_id,supplier_id,property_code,scope,source_sequence,activated_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            aid,
            tenant,
            bid,
            batch["supplier_id"],
            prop,
            j(scope),
            manifest["source_sequence"],
            actor["subject"],
        ),
    )
    rows = conn.execute(
        "SELECT * FROM staging_record WHERE batch_id=%s ORDER BY kind,record_number", (bid,)
    ).fetchall()
    clock = business_now()
    for record in (r for r in rows if r["kind"] == "inventory"):
        observed = datetime.fromisoformat(record["data"]["observed_at"])
        if clock - observed > timedelta(hours=24) or observed - clock > timedelta(minutes=5):
            fail("SOURCE_EXPIRED_OR_FUTURE")
    entities = {}
    for kind, source_kind, fields in [
        ("property", "properties", ["property_code"]),
        ("room", "rooms", ["property_code", "room_code"]),
        ("rate", "rate_plans", ["property_code", "room_code", "plan_code"]),
    ]:
        for row in [r for r in rows if r["kind"] == source_kind]:
            data = row["data"]
            key = json.dumps([data[k] for k in fields], separators=(",", ":"))
            parent = (
                json.dumps([data[k] for k in fields[:-1]], separators=(",", ":"))
                if kind != "property"
                else None
            )
            eid = uuid4()
            entities[(kind, key)] = eid
            conn.execute(
                "INSERT INTO supplier_entity_version VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (eid, tenant, aid, row["id"], kind, key, parent, j(data), digest(data)),
            )
    pools = {}
    for row in [r for r in rows if r["kind"] == "inventory"]:
        d = row["data"]
        key = tuple(d[k] for k in ["property_code", "room_code", "pool_code"])
        if key not in pools:
            pools[key] = uuid4()
            conn.execute(
                "INSERT INTO inventory_pool VALUES(%s,%s,%s,%s,%s,%s)",
                (pools[key], tenant, aid, *key),
            )
        conn.execute(
            "INSERT INTO inventory_cell VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                uuid4(),
                tenant,
                pools[key],
                row["id"],
                d["stay_date"],
                int(d["quantity"]),
                d["stop_sell"] == "true",
                d["observed_at"],
            ),
        )
    linked = set()
    for row in [r for r in rows if r["kind"] == "rates"]:
        d = row["data"]
        key = json.dumps(
            [d[k] for k in ["property_code", "room_code", "plan_code"]], separators=(",", ":")
        )
        plan = entities[("rate", key)]
        if plan not in linked:
            conn.execute(
                "INSERT INTO plan_pool_link VALUES(%s,%s,%s,%s)",
                (
                    uuid4(),
                    tenant,
                    plan,
                    pools[tuple(d[k] for k in ["property_code", "room_code", "pool_code"])],
                ),
            )
            linked.add(plan)
        conn.execute(
            "INSERT INTO rate_cell VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
            (uuid4(), tenant, plan, row["id"], d["stay_date"], 2, "USD", d["net_amount"]),
        )
    if current:
        conn.execute(
            "UPDATE source_partition SET activation_id=%s,version=version+1 WHERE id=%s",
            (aid, current["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO source_partition(id,tenant_id,supplier_id,property_code,scope_hash,activation_id) VALUES(%s,%s,%s,%s,%s,%s)",
            (uuid4(), tenant, batch["supplier_id"], prop, scope_hash, aid),
        )
    conn.execute("UPDATE import_batch SET status='ACTIVE',version=version+1 WHERE id=%s", (bid,))
    if current:
        reuse_unchanged_mappings(conn, actor, aid, current["activation_id"])
    audit(conn, actor, "source.activated", aid, "Atomic complete snapshot activation")
    return get(conn, "source_activation", aid)


def reuse_unchanged_mappings(conn, actor, aid, previous_id):
    from psycopg import sql

    from services.core.g1.matching import conflicts, mapping, targets

    sources = conn.execute(
        "SELECT * FROM supplier_entity_version WHERE activation_id=%s ORDER BY CASE kind WHEN 'property' THEN 0 WHEN 'room' THEN 1 ELSE 2 END",
        (aid,),
    ).fetchall()
    for source in sources:
        prior = conn.execute(
            "SELECT * FROM supplier_entity_version WHERE activation_id=%s AND kind=%s AND natural_key=%s",
            (previous_id, source["kind"], source["natural_key"]),
        ).fetchone()
        if prior and prior["facts_hash"] != source["facts_hash"]:
            from services.core.g2.formats import normalize

            entity = {"property": "properties", "room": "rooms", "rate": "rate_plans"}[
                source["kind"]
            ]
            if normalize(entity, prior["data"], "csv", "snapshot") != normalize(
                entity, source["data"], "csv", "snapshot"
            ):
                continue
        old = mapping(conn, prior) if prior else None
        if not old or old["status"] != "APPROVED":
            continue
        target = next(
            (
                t
                for t in targets(conn, source)
                if t["target_id"] == old["target_id"]
                and t["target_version_id"] == old["target_version_id"]
            ),
            None,
        )
        if not target or conflicts(conn, source, target):
            continue
        reason = f"Unchanged facts; prior approved mapping {old['id']}"
        conn.execute(
            sql.SQL(
                "INSERT INTO {}(id,tenant_id,source_id,target_id,target_version_id,status,actor,reason) VALUES(%s,%s,%s,%s,%s,'APPROVED','system.mapping-reuse',%s)"
            ).format(sql.Identifier(source["kind"] + "_mapping")),
            (
                uuid4(),
                actor["tenant_id"],
                source["id"],
                old["target_id"],
                old["target_version_id"],
                reason,
            ),
        )
        conn.execute(
            "INSERT INTO mapping_decision(id,tenant_id,source_id,kind,decision,target_id,target_version_id,actor,reason) VALUES(%s,%s,%s,%s,'APPROVED',%s,%s,'system.mapping-reuse',%s)",
            (
                uuid4(),
                actor["tenant_id"],
                source["id"],
                source["kind"],
                old["target_id"],
                old["target_version_id"],
                reason,
            ),
        )
        audit(
            conn,
            {**actor, "subject": "system.mapping-reuse"},
            "mapping.reused",
            source["id"],
            reason,
        )
