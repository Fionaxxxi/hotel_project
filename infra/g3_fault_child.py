import argparse
import json
import os
from pathlib import Path

from infra.manage import uid
from services.core.config import Settings
from services.core.db import Database
from services.core.g2 import jobs
from services.core.g3 import domain, engine


def main():
    p = argparse.ArgumentParser()
    p.add_argument("window")
    p.add_argument("resource")
    p.add_argument("--body")
    args = p.parse_args()
    settings = Settings.load()
    if os.environ.get("HOTEL_G1_TEST_MODE") != "true" or not settings.database_url.endswith(
        ("/hotel_project_g3_test", "/hotel_project_g4_test", "/hotel_project_g5_test")
    ):
        raise SystemExit("G3 crash harness requires exact isolated test DB")
    db = Database(settings.database_url, max_size=6)
    db.pool.open()
    tenant = uid("tenant-A")
    actor = {"tenant_id": tenant, "subject": "a.operator", "role": "operator"}
    try:
        if args.window in {"before", "after"}:
            body = json.loads(Path(args.body).read_text())
            with db.transaction(tenant) as c:
                domain.publish(c, actor, body["snapshots"], body["key"], body["expected"])
                if args.window == "before":
                    os._exit(81)
            os._exit(82)
        elif args.window == "relay":
            engine.relay(db, tenant, after_consume=lambda: os._exit(84), event_id=args.resource)
        else:
            with db.transaction(tenant) as c:
                job = jobs.claim(c, "g3-fault-child", kinds=["G3_DELIVER"], resource=args.resource)
            assert job
            engine.execute(
                db, job, after_remote=(lambda: os._exit(83)) if args.window == "remote" else None
            )
    finally:
        db.pool.close()


if __name__ == "__main__":
    main()
