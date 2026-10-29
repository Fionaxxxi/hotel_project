import csv
import hashlib
import io
import json
import time
from uuid import UUID, uuid4

from openpyxl import Workbook

from infra.manage import uid
from services.core.file_contract import REQUIRED
from services.core.g2 import jobs, processing
from tests.g1_helpers import ROOT, auth, ok


def files(fmt="csv", code=None, sequence=1, mode="snapshot", only=None, edit=None):
    code = code or "G2" + uuid4().hex[:8]
    data = {}
    for kind in REQUIRED:
        with (ROOT / f"datasets/fixtures/F00-valid/{kind}.csv").open(
            encoding="utf-8", newline=""
        ) as f:
            data[kind] = list(csv.DictReader(f))
        for row in data[kind]:
            row["property_code"] = code
    authority = {
        "inventory": sorted(
            {
                tuple(r[k] for k in ["property_code", "room_code", "pool_code"])
                for r in data["inventory"]
            }
        ),
        "rates": sorted(
            {
                tuple(
                    r[k] for k in ["property_code", "room_code", "plan_code", "adults", "currency"]
                )
                for r in data["rates"]
            }
        ),
    }
    if only:
        data = {k: v for k, v in data.items() if k in only}
    m = {
        "schema_version": "2.0",
        "supplier_code": "S-A",
        "source_sequence": sequence,
        "sequence_stream": "demo-v1",
        "generated_at": "2027-01-01T02:00:00Z",
        "mode": mode,
        "format": fmt,
        "contract_version": "g2-demo-v1",
        "scope": {
            "property_codes": [code],
            "entities": list(data),
            "stay_date_from": "2027-01-10",
            "stay_date_to_exclusive": "2027-01-13",
            "authority": authority,
        },
        "files": [],
    }
    if edit:
        edit(data, m)
    content = {}
    if fmt == "csv":
        for kind, rows in data.items():
            stream = io.StringIO()
            writer = csv.DictWriter(
                stream,
                fieldnames=list(REQUIRED[kind])
                + (["operation"] if any("operation" in r for r in rows) else []),
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerows(rows)
            content[kind + ".csv"] = stream.getvalue().encode()
    elif fmt == "json":
        content["data.json"] = json.dumps(data, ensure_ascii=False).encode()
    else:
        workbook = Workbook()
        workbook.remove(workbook.active)
        for kind, rows in data.items():
            sheet = workbook.create_sheet(kind)
            headers = list(REQUIRED[kind]) + (
                ["operation"] if any("operation" in r for r in rows) else []
            )
            sheet.append(headers)
            for row in rows:
                sheet.append([row.get(k) for k in headers])
        stream = io.BytesIO()
        workbook.save(stream)
        content["data.xlsx"] = stream.getvalue()
    m["files"] = [{"name": k, "sha256": hashlib.sha256(v).hexdigest()} for k, v in content.items()]
    content["manifest.json"] = json.dumps(m, ensure_ascii=False).encode()
    return [("files", (k, v, "application/octet-stream")) for k, v in content.items()]


def start_run(client, settings, **kwargs):
    bid = ok(client.post("/v2/imports", headers=auth(settings), files=files(**kwargs)))["id"]
    run = ok(client.post(f"/v2/imports/{bid}/runs", headers=auth(settings), json={}))
    client.app.state.db.g2_assigned_runs = [
        *getattr(client.app.state.db, "g2_assigned_runs", []),
        UUID(run["id"]),
    ]
    return bid, run


def parse(client, settings, run):
    db = client.app.state.db
    with db.transaction(uid("tenant-A")) as c:
        job = jobs.claim(c, "parse-test", kinds=["PARSE"], resource=run["id"])
    assert job and str(job["resource_id"]) == run["id"]
    processing.process(db, job)
    return ok(client.get("/v2/processing-runs/" + run["id"], headers=auth(settings)))


def activate(client, settings, run, selected=None):
    current = ok(client.get("/v2/processing-runs/" + run["id"], headers=auth(settings)))
    selected = selected or ["2027-01-10", "2027-01-11", "2027-01-12"]
    return ok(
        client.post(
            f"/v2/processing-runs/{run['id']}/activate",
            headers={**auth(settings), "Idempotency-Key": str(uuid4())},
            json={
                "expected_versions": current["expected_versions"],
                "selected_dates": selected,
                "expected_run_version": current["version"],
                "partial_confirmed": True,
            },
        )
    )


def ready(client, settings, **kwargs):
    _, run = start_run(client, settings, **kwargs)
    parse(client, settings, run)
    return run


def drain(client, settings, predicate, seconds=20):
    from services.core.g2.worker import tick

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        db = client.app.state.db
        runs = getattr(db, "g2_assigned_runs", None)
        resources = None
        event_resources = None
        if runs:
            with db.transaction(uid("tenant-A")) as c:
                workflows = c.execute(
                    "SELECT id,snapshot_id,task_id FROM workflow_run WHERE processing_id=ANY(%s)",
                    (runs,),
                ).fetchall()
                resources = [
                    *runs,
                    *[v for row in workflows for v in row.values() if v is not None],
                ]
                entities = c.execute(
                    "SELECT s.id FROM supplier_entity_version s JOIN generation_activation a ON a.activation_id=s.activation_id JOIN source_generation g ON g.id=a.generation_id WHERE g.run_id=ANY(%s)",
                    (runs,),
                ).fetchall()
                event_resources = [*resources, *[r["id"] for r in entities]]
        tick(db, "test-drain", resources=resources, event_resources=event_resources)
    raise AssertionError("G2 pipeline did not reach expected durable state")
