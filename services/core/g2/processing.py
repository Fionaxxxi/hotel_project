import json
from itertools import islice
from uuid import uuid4

from jsonschema import Draft202012Validator, FormatChecker

from services.core.g1.common import audit, authorize, digest, fail, get, j
from services.core.g1.ingestion import ROOT, STORE
from services.core.g2 import formats, jobs


def validate_manifest(manifest, names):
    schema = json.loads(
        (ROOT / "contracts/files/v2/manifest.schema.json").read_text(encoding="utf-8")
    )
    if not Draft202012Validator(schema, format_checker=FormatChecker()).is_valid(manifest):
        fail("MANIFEST_V2_SCHEMA", 422)
    if set(names) != {"manifest.json", *[f["name"] for f in manifest["files"]]} or len(
        manifest["files"]
    ) != len({f["name"] for f in manifest["files"]}):
        fail("FILE_SET_MISMATCH", 422)
    scope = manifest["scope"]
    from datetime import date

    start, end = map(date.fromisoformat, [scope["stay_date_from"], scope["stay_date_to_exclusive"]])
    if not 0 < (end - start).days <= 366:
        fail("INVALID_SCOPE", 422)
    expected = (
        {f"{kind}.csv" for kind in scope["entities"]}
        if manifest["format"] == "csv"
        else {f"data.{manifest['format']}"}
    )
    if {f["name"] for f in manifest["files"]} != expected:
        fail("FORMAT_FILE_MISMATCH", 422)
    if manifest["sequence_stream"] != "demo-v1":
        fail("SEQUENCE_STREAM_NOT_CONFIGURED", 422)
    if manifest["mode"] == "snapshot" and any(
        kind in scope["entities"] and kind not in scope["authority"]
        for kind in ["inventory", "rates"]
    ):
        fail("SNAPSHOT_AUTHORITY_REQUIRED", 422)


def register(c, actor, bid, manifest, file_metadata):
    authorize(actor)
    supplier = c.execute(
        "SELECT id FROM supplier WHERE code=%s AND status='active'", (manifest["supplier_code"],)
    ).fetchone()
    if not supplier:
        fail("SUPPLIER_NOT_FOUND", 422)
    package_hash = digest({f["name"]: f["sha256"] for f in file_metadata})
    c.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        (str(actor["tenant_id"]) + package_hash,),
    )
    old = c.execute(
        "SELECT id FROM import_batch WHERE supplier_id=%s AND package_hash=%s",
        (supplier["id"], package_hash),
    ).fetchone()
    if old:
        return {"id": old["id"], "reused": True}
    c.execute(
        "INSERT INTO import_batch(id,tenant_id,supplier_id,package_hash,manifest,created_by) VALUES(%s,%s,%s,%s,%s,%s)",
        (bid, actor["tenant_id"], supplier["id"], package_hash, j(manifest), actor["subject"]),
    )
    for f in file_metadata:
        c.execute(
            "INSERT INTO artifact VALUES(%s,%s,%s,%s,%s,%s,%s)",
            (
                uuid4(),
                actor["tenant_id"],
                bid,
                f["name"],
                f"{bid}/{f['name']}",
                f["sha256"],
                f["size"],
            ),
        )
    audit(c, actor, "g2.import.uploaded", bid, "Immutable streaming upload")
    return {"id": bid, "reused": False}


def start(c, actor, bid, template="supplier-v2", normalizer=formats.VERSION):
    authorize(actor)
    batch = get(c, "import_batch", bid)
    if batch["manifest"]["schema_version"] != "2.0":
        fail("USE_V1_CONFIRM_FOR_LEGACY_PACKAGE", 422)
    params = {
        "template": template,
        "normalizer": normalizer,
        "parser": "adapters-v2",
        "format": batch["manifest"]["format"],
    }
    key = digest(params)
    c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (str(bid) + key,))
    run = c.execute(
        "SELECT * FROM processing_run WHERE batch_id=%s AND params_hash=%s", (bid, key)
    ).fetchone()
    if run:
        return run
    c.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        (str(actor["tenant_id"]) + ":parse-quota",),
    )
    pending = c.execute(
        "SELECT count(*) AS n FROM execution_job WHERE kind='PARSE' AND state IN ('READY','RUNNING','RETRY_WAIT')"
    ).fetchone()["n"]
    if pending >= 8:
        fail("TENANT_PROCESSING_QUEUE_FULL", 429)
    # Template IDs identify the currently supported fixed mapping; no arbitrary executable parser.
    if template not in {"supplier-v2", "supplier-v2-reviewed"} or normalizer != formats.VERSION:
        fail("PARSER_VERSION_NOT_AVAILABLE", 422)
    rid = uuid4()
    c.execute(
        "INSERT INTO processing_run(id,tenant_id,batch_id,definition,params_hash,params,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s)",
        (rid, actor["tenant_id"], bid, "processing-v2", key, j(params), actor["subject"]),
    )
    jobs.enqueue(c, actor["tenant_id"], "PARSE", rid, str(rid))
    jobs.emit(c, actor["tenant_id"], "processing.created", rid, 1, {"run_id": str(rid)})
    audit(c, actor, "processing.created", rid, key)
    return get(c, "processing_run", rid)


def add_issue(c, tenant, rid, code, partition, location, record=None, severity="partition"):
    c.execute(
        "INSERT INTO dq_issue(id,tenant_id,run_id,record_id,code,partition_key,severity,location,detail) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            uuid4(),
            tenant,
            rid,
            record,
            code,
            partition,
            severity,
            j(location),
            "Correct input and create a new package or explicitly confirm complete independent partitions",
        ),
    )


def process(db, job, after_chunk=None):
    tenant = job["tenant_id"]
    rid = job["resource_id"]
    with db.transaction(tenant) as c:
        jobs.guard(c, job)
        run = get(c, "processing_run", rid)
        batch = get(c, "import_batch", run["batch_id"])
        manifest = batch["manifest"]
        artifacts = c.execute(
            "SELECT * FROM artifact WHERE batch_id=%s AND name!='manifest.json' ORDER BY name",
            (batch["id"],),
        ).fetchall()
        c.execute("UPDATE processing_run SET status='RUNNING' WHERE id=%s", (rid,))
    for artifact in artifacts:
        path = STORE / artifact["storage_key"]
        if formats.file_hash(path) != artifact["sha256"]:
            with db.transaction(tenant) as c:
                jobs.guard(c, job)
                add_issue(
                    c,
                    tenant,
                    rid,
                    "HASH_MISMATCH",
                    "*",
                    {"file": artifact["name"]},
                    severity="package",
                )
            continue
        kinds = [artifact["name"][:-4]] if path.suffix == ".csv" else manifest["scope"]["entities"]
        if path.suffix in {".xlsx", ".json"}:
            try:
                formats.inspect_structure(path, manifest["scope"]["entities"])
            except Exception as exc:
                with db.transaction(tenant) as c:
                    jobs.guard(c, job)
                    add_issue(
                        c,
                        tenant,
                        rid,
                        type(exc).__name__ + ":" + str(exc)[:100],
                        "*",
                        {"file": artifact["name"]},
                        severity="package",
                    )
                continue
        for kind in kinds:
            with db.transaction(tenant) as c:
                checkpoint = c.execute(
                    "SELECT position FROM parse_checkpoint WHERE run_id=%s AND artifact_id=%s AND kind=%s",
                    (rid, artifact["id"], kind),
                ).fetchone()
                committed = checkpoint["position"] if checkpoint else 0
            iterator = formats.records(path, kind)
            try:
                while True:
                    group = list(islice(iterator, 1000))
                    if not group:
                        break
                    pending = [item for item in group if item[0] > committed]
                    if not pending:
                        continue
                    if pending[-1][0] > 200001:
                        raise ValueError("RECORD_LIMIT")
                    with db.transaction(tenant) as c:
                        jobs.guard(c, job)
                        jobs.heartbeat(c, job)
                        prepared = []
                        issues = []
                        for position, raw, location in pending:
                            record = uuid4()
                            data = {}
                            code = None
                            try:
                                data = formats.normalize(
                                    kind, raw, manifest["format"], manifest["mode"]
                                )
                                if data["property_code"] not in manifest["scope"]["property_codes"]:
                                    raise ValueError("PROPERTY_OUTSIDE_SCOPE")
                                if (
                                    "stay_date" in data
                                    and not manifest["scope"]["stay_date_from"]
                                    <= data["stay_date"]
                                    < manifest["scope"]["stay_date_to_exclusive"]
                                ):
                                    raise ValueError("DATE_OUTSIDE_SCOPE")
                                key = formats.natural_key(kind, data)
                            except (ValueError, ArithmeticError, KeyError, TypeError) as exc:
                                key = f"invalid:{artifact['id']}:{kind}:{position}"
                                code = str(exc)[:100]
                            raw_safe = json.loads(json.dumps(raw, default=str))
                            day = formats.partition(data or raw_safe)
                            if day != "MASTER":
                                from datetime import date

                                try:
                                    date.fromisoformat(day)
                                except (ValueError, TypeError):
                                    day = "*"
                            if code:
                                issues.append((record, code, day, location))
                            prepared.append(
                                (
                                    record,
                                    tenant,
                                    rid,
                                    artifact["id"],
                                    kind,
                                    position,
                                    j(location),
                                    key,
                                    j(raw_safe),
                                    j(data),
                                    digest(raw_safe),
                                    not bool(code),
                                )
                            )
                        with c.cursor() as cursor:
                            cursor.executemany(
                                "INSERT INTO processing_record VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(tenant_id,run_id,artifact_id,kind,position) DO NOTHING",
                                prepared,
                            )
                        for record, code, day, location in issues:
                            add_issue(
                                c,
                                tenant,
                                rid,
                                code,
                                day,
                                location,
                                record,
                                severity="package" if day == "*" else "partition",
                            )
                        c.execute(
                            "INSERT INTO parse_checkpoint VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(tenant_id,run_id,artifact_id,kind) DO UPDATE SET position=excluded.position",
                            (uuid4(), tenant, rid, artifact["id"], kind, pending[-1][0]),
                        )
                        c.execute(
                            "UPDATE processing_run SET rows_committed=(SELECT count(*) FROM processing_record WHERE run_id=%s) WHERE id=%s",
                            (rid, rid),
                        )
                    if after_chunk:
                        after_chunk()
            except (ValueError, UnicodeError, KeyError) as exc:
                with db.transaction(tenant) as c:
                    jobs.guard(c, job)
                    add_issue(
                        c,
                        tenant,
                        rid,
                        "FORMAT_ERROR:" + str(exc)[:100],
                        "*",
                        {"file": artifact["name"], "entity": kind},
                        severity="package",
                    )
    with db.transaction(tenant) as c:
        jobs.guard(c, job)
        duplicates = c.execute(
            "SELECT kind,natural_key FROM processing_record WHERE run_id=%s AND is_valid GROUP BY kind,natural_key HAVING count(*)>1",
            (rid,),
        ).fetchall()
        for duplicate in duplicates:
            rows = c.execute(
                "UPDATE processing_record SET is_valid=false WHERE run_id=%s AND kind=%s AND natural_key=%s RETURNING id,data,locator",
                (rid, duplicate["kind"], duplicate["natural_key"]),
            ).fetchall()
            for row in rows:
                add_issue(
                    c,
                    tenant,
                    rid,
                    "DUPLICATE_NATURAL_KEY",
                    formats.partition(row["data"]),
                    row["locator"],
                    row["id"],
                )
        bad = c.execute("SELECT count(*) AS n FROM dq_issue WHERE run_id=%s", (rid,)).fetchone()[
            "n"
        ]
        status = "QUALITY_REVIEW" if bad else "READY_TO_ACTIVATE"
        c.execute(
            "UPDATE processing_run SET status=%s,version=version+1 WHERE id=%s", (status, rid)
        )
        jobs.emit(c, tenant, "processing.completed", rid, run["version"] + 1, {"status": status})
        jobs.finish(c, job)
    return status


def preview(c, bid):
    batch = get(c, "import_batch", bid)
    result = {"id": bid, "manifest": batch["manifest"], "preview": {}}
    for artifact in c.execute(
        "SELECT * FROM artifact WHERE batch_id=%s AND name!='manifest.json'", (bid,)
    ).fetchall():
        kinds = (
            [artifact["name"][:-4]]
            if artifact["name"].endswith(".csv")
            else batch["manifest"]["scope"]["entities"]
        )
        for kind in kinds:
            try:
                result["preview"][kind] = [
                    {"data": raw, "location": loc}
                    for _, raw, loc in islice(
                        formats.records(STORE / artifact["storage_key"], kind), 50
                    )
                ]
            except Exception:
                result["preview"][kind] = {"error": "FORMAT_INVALID"}
    return result
