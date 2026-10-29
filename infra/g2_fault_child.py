"""Explicit test-only crash harness. Never enabled by a business HTTP input."""

import argparse
import json
import os
import time
from pathlib import Path

from infra.manage import uid
from services.core.config import Settings
from services.core.db import Database
from services.core.g1 import distribution
from services.core.g2 import jobs, processing, views, workflow
from services.core.g2.worker import execute


def main():
    p = argparse.ArgumentParser()
    p.add_argument("operation")
    p.add_argument("resource")
    p.add_argument("--body")
    p.add_argument("--marker")
    args = p.parse_args()
    settings = Settings.load()
    from urllib.parse import urlparse

    if os.environ.get("HOTEL_G1_TEST_MODE") != "true" or urlparse(
        settings.database_url
    ).path not in {
        "/hotel_project_g2_test",
        "/hotel_project_g3_test",
        "/hotel_project_g4_test",
        "/hotel_project_g5_test",
    }:
        raise SystemExit("Crash harness requires the named isolated G2 test DB")
    db = Database(settings.database_url, max_size=4)
    db.pool.open()
    tenant = uid("tenant-A")
    actor = {"tenant_id": tenant, "subject": "a.operator", "role": "operator"}
    try:
        if args.operation.startswith("parse"):
            with db.transaction(tenant) as c:
                job = jobs.claim(c, "child-parse", kinds=["PARSE"], resource=args.resource)
            assert job
            processing.process(
                db,
                job,
                after_chunk=(lambda: os._exit(73)) if args.operation == "parse-crash" else None,
            )
        elif args.operation == "relay-crash":
            workflow.relay_one(
                db, tenant, after_consume=lambda: os._exit(74), event_id=args.resource
            )
        elif args.operation.startswith("activate"):
            body = json.loads(Path(args.body).read_text())
            with db.transaction(tenant) as c:
                views.activate(c, actor, args.resource, body["body"], body["key"])
                if args.operation == "activate-before-commit":
                    os._exit(75)
            os._exit(76)
        elif args.operation in {"publish-crash", "publish-recover"}:
            with db.transaction(tenant) as c:
                job = jobs.claim(c, "child-publish", kinds=["PUBLISH"], resource=args.resource)
            assert job
            if args.operation == "publish-crash":
                distribution.dispatch_one(
                    db, tenant, args.resource, lease=job, after_remote=lambda: os._exit(77)
                )
            else:
                execute(db, job)
        elif args.operation == "hold-lease":
            with db.transaction(tenant) as c:
                job = jobs.claim(c, "child-hold", kinds=["TEST_HOLD"], resource=args.resource)
            assert job
            marker = Path(args.marker)
            marker.write_text(json.dumps({"token": job["fencing_token"], "owner": job["owner"]}))
            deadline = time.monotonic() + 15
            while not marker.with_suffix(".release").exists():
                if time.monotonic() > deadline:
                    raise RuntimeError("Barrier not released")
                time.sleep(0.02)
            try:
                with db.transaction(tenant) as c:
                    jobs.finish(c, job)
            except Exception:
                raise SystemExit(78)
    finally:
        db.pool.close()


if __name__ == "__main__":
    main()
