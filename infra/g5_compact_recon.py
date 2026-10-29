"""Coalesce legacy pending read-only reconciliation jobs; retain every history row."""

import json
from datetime import UTC, datetime

from infra.manage import ROOT, uid
from services.core.config import Settings
from services.core.db import Database
from services.core.g1.common import audit


def compact(c):
    return c.execute(
        "WITH ranked AS (SELECT id,row_number() OVER(PARTITION BY tenant_id,resource_id ORDER BY (state='RUNNING') DESC,created_at,id) AS n FROM execution_job WHERE kind IN ('G3_RECON','G3_VERIFY') AND state IN ('READY','RETRY_WAIT','RUNNING')) UPDATE execution_job j SET state='CANCELLED',error='G5_RECONCILIATION_COALESCED',lease_until=NULL FROM ranked r WHERE j.id=r.id AND r.n>1 RETURNING j.id,j.resource_id"
    ).fetchall()


def main():
    for marker in ["api", "g3-worker-0", "g3-worker-1"]:
        if (ROOT / f".runtime/{marker}.pid").exists():
            raise RuntimeError("STOP_PROJECT_DISPATCHERS_BEFORE_QUEUE_COMPACTION")
    db = Database(Settings.load().database_url)
    db.pool.open()
    result = {
        "at": datetime.now(UTC).isoformat(),
        "operation": "coalesce duplicate pending G3_RECON only; no delete or publish change",
        "tenants": {},
    }
    try:
        for label in ["A", "B"]:
            actor = {"tenant_id": uid("tenant-" + label), "subject": "system.g5-maintenance"}
            with db.transaction(actor["tenant_id"]) as c:
                rows = compact(c)
                audit(
                    c,
                    actor,
                    "g5.queue.recon-coalesced",
                    None,
                    "Retained oldest pending read for each listing; cancelled "
                    + str(len(rows))
                    + " duplicate query jobs",
                )
                result["tenants"][label] = [
                    {"job_id": str(x["id"]), "listing_id": str(x["resource_id"])} for x in rows
                ]
        (ROOT / "docs/acceptance/g5-recon-compaction.json").write_text(
            json.dumps(result, indent=2) + "\n", encoding="utf-8"
        )
        print(
            "Coalesced duplicate pending reconciliation jobs: "
            + str(sum(len(x) for x in result["tenants"].values()))
        )
    finally:
        db.pool.close()


if __name__ == "__main__":
    main()
