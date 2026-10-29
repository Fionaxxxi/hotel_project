import argparse
import time

import psycopg

from infra.manage import uid
from services.core.config import Settings
from services.core.db import Database
from services.core.g1.distribution import dispatch_one, query_remote


def run(once=False):
    settings = Settings.load()
    db = Database(settings.database_url)
    db.pool.open()
    try:
        with psycopg.connect(settings.database_url, autocommit=True) as guard:
            if not guard.execute("SELECT pg_try_advisory_lock(7140027)").fetchone()[0]:
                raise RuntimeError("A G1 dispatcher already owns the process lock")
            # G1 development tenants are explicitly provisioned A/B, no global RLS bypass.
            for label in ["A", "B"]:
                with db.transaction(uid(f"tenant-{label}")) as c:
                    c.execute(
                        "UPDATE publish_task SET status='UNKNOWN',error_code='DISPATCH_INTERRUPTED' WHERE status='SENDING'"
                    )
            while True:
                for label in ["A", "B"]:
                    tenant = uid(f"tenant-{label}")
                    with db.transaction(tenant) as c:
                        tasks = c.execute(
                            "SELECT t.id,t.status FROM publish_task t WHERE t.status='READY' OR (t.status IN ('UNKNOWN','ACKNOWLEDGED') AND (SELECT count(*) FROM channel_receipt r WHERE r.task_id=t.id)<5) ORDER BY t.created_at LIMIT 20"
                        ).fetchall()
                    for task in tasks:
                        if task["status"] == "READY":
                            dispatch_one(db, tenant, task["id"])
                        else:
                            query_remote(db, tenant, task["id"])
                if once:
                    return
                time.sleep(2)
    finally:
        db.pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    run(parser.parse_args().once)
