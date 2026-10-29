"""One isolated ingest/parse/persistence benchmark. No channel activation or network publish."""

import argparse
import csv
import json
import time
from uuid import uuid4

from openpyxl import Workbook

from infra.manage import uid
from services.core.config import Settings
from services.core.db import Database
from services.core.g1.ingestion import STORE
from services.core.g2 import formats, jobs, processing


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--format", choices=["csv", "xlsx", "json"])
    p.add_argument("--rows", type=int)
    p.add_argument("--phase")
    p.add_argument("--reuse-run")
    args = p.parse_args()
    settings = Settings.load()
    if not settings.database_url.endswith("/hotel_project_g2_test"):
        raise SystemExit("Benchmark requires isolated G2 test DB")
    if args.reuse_run:
        db = Database(settings.database_url, max_size=4)
        db.pool.open()
        actor = {"tenant_id": uid("tenant-A"), "subject": "a.operator", "role": "operator"}
        try:
            with db.transaction(actor["tenant_id"]) as c:
                old = c.execute(
                    "SELECT batch_id FROM processing_run WHERE id=%s", (args.reuse_run,)
                ).fetchone()
                artifact = c.execute(
                    "SELECT storage_key FROM artifact WHERE batch_id=%s AND name!='manifest.json'",
                    (old["batch_id"],),
                ).fetchone()
                run = processing.start(c, actor, old["batch_id"], template="supplier-v2-reviewed")
                job = jobs.claim(c, "benchmark-warm", kinds=["PARSE"], resource=run["id"])
            start = time.perf_counter()
            status = processing.process(db, job)
            elapsed = time.perf_counter() - start
            with db.transaction(actor["tenant_id"]) as c:
                actual = c.execute(
                    "SELECT rows_committed FROM processing_run WHERE id=%s", (run["id"],)
                ).fetchone()["rows_committed"]
            assert actual == args.rows and status == "READY_TO_ACTIVATE"
            print(
                json.dumps(
                    {
                        "format": args.format,
                        "rows": args.rows,
                        "phase": args.phase,
                        "file_bytes": (STORE / artifact["storage_key"]).stat().st_size,
                        "seconds": round(elapsed, 3),
                        "records_per_second": round(args.rows / elapsed),
                        "rows_committed": actual,
                        "status": status,
                        "run_id": str(run["id"]),
                        "reused_original_run": args.reuse_run,
                    }
                )
            )
        finally:
            db.pool.close()
        return
    bid = uuid4()
    folder = STORE / str(bid)
    folder.mkdir(parents=True)
    filename = "rooms.csv" if args.format == "csv" else "data." + args.format
    path = folder / filename
    code = "BENCH-" + uuid4().hex[:10]
    headers = ["property_code", "room_code", "name", "max_adults", "bed_type"]

    def rows():
        for i in range(args.rows):
            yield dict(
                zip(
                    headers,
                    [code, f"R{i:07}", "Synthetic King " + str(i), "2", "KING"],
                    strict=True,
                )
            )

    if args.format == "csv":
        with path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=headers)
            w.writeheader()
            w.writerows(rows())
    elif args.format == "json":
        with path.open("w", encoding="utf-8") as f:
            f.write('{"rooms":[')
            for index, row in enumerate(rows()):
                if index:
                    f.write(",")
                f.write(json.dumps(row))
            f.write("]}")
    else:
        workbook = Workbook(write_only=True)
        sheet = workbook.create_sheet("rooms")
        sheet.append(headers)
        for row in rows():
            sheet.append([row[h] for h in headers])
        workbook.save(path)
    manifest = {
        "schema_version": "2.0",
        "supplier_code": "S-A",
        "source_sequence": 1,
        "sequence_stream": "demo-v1",
        "generated_at": "2027-01-01T02:00:00Z",
        "mode": "snapshot",
        "format": args.format,
        "contract_version": "benchmark-v2",
        "scope": {
            "property_codes": [code],
            "entities": ["rooms"],
            "stay_date_from": "2027-01-10",
            "stay_date_to_exclusive": "2027-01-11",
            "authority": {},
        },
        "files": [{"name": filename, "sha256": formats.file_hash(path)}],
    }
    (folder / "manifest.json").write_text(json.dumps(manifest))
    metadata = [
        {"name": p.name, "sha256": formats.file_hash(p), "size": p.stat().st_size}
        for p in folder.iterdir()
    ]
    db = Database(settings.database_url, max_size=4)
    db.pool.open()
    actor = {"tenant_id": uid("tenant-A"), "subject": "a.operator", "role": "operator"}
    try:
        with db.transaction(actor["tenant_id"], "a.operator") as c:
            processing.register(c, actor, bid, manifest, metadata)
            run = processing.start(c, actor, bid)
            job = jobs.claim(c, "benchmark", kinds=["PARSE"], resource=run["id"])
        start = time.perf_counter()
        status = processing.process(db, job)
        elapsed = time.perf_counter() - start
        with db.transaction(actor["tenant_id"]) as c:
            actual = c.execute(
                "SELECT rows_committed FROM processing_run WHERE id=%s", (run["id"],)
            ).fetchone()["rows_committed"]
        assert actual == args.rows and status == "READY_TO_ACTIVATE"
        print(
            json.dumps(
                {
                    "format": args.format,
                    "rows": args.rows,
                    "phase": args.phase,
                    "file_bytes": path.stat().st_size,
                    "seconds": round(elapsed, 3),
                    "records_per_second": round(args.rows / elapsed),
                    "rows_committed": actual,
                    "status": status,
                    "run_id": str(run["id"]),
                }
            )
        )
    finally:
        db.pool.close()


if __name__ == "__main__":
    main()
