"""Read actual live remote states, fence ALL restored source scopes, resume only new work.

Never marks unknown writes successful and never changes a simulator during quarantine.
"""

import argparse
import json
import re
from datetime import UTC, datetime

import psycopg
from dotenv import load_dotenv
from fastapi.encoders import jsonable_encoder
from psycopg.rows import dict_row

from infra.g5_recovery import connection
from infra.manage import ROOT
from services.core.g3 import transport


def main(run_id):
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,20}", run_id):
        raise ValueError("INVALID_RESTORE_ID")
    load_dotenv(ROOT / ".env")
    report = json.loads(
        (ROOT / f"docs/acceptance/g5-restore-{run_id}.json").read_text(encoding="utf-8")
    )
    name = report["databases"]["core"]["database"]
    if name != "hotel_project_g5_restore_" + run_id + "_core":
        raise ValueError("RESTORE_REPORT_TARGET_MISMATCH")
    observations = []
    with psycopg.connect(connection(name), row_factory=dict_row) as c:
        tables = c.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='g5_recovery_scope'"
        ).fetchone()
        if not tables:
            raise ValueError("RESTORE_REQUIRES_G5_RECOVERY_MIGRATION")
        listings = c.execute(
            "SELECT l.*,a.code FROM g3_listing l JOIN g3_account a ON(a.tenant_id,a.id)=(l.tenant_id,l.account_id) ORDER BY l.tenant_id,l.id"
        ).fetchall()
        for row in listings:
            try:
                response = transport.request(
                    row["tenant_id"], row["code"], "GET", transport.listing_path(row["listing_key"])
                )
                data = response.json()
                complete = response.status_code == 200 and data.get("complete") is True
                closed = (
                    complete
                    and bool(data.get("cells"))
                    and all(x["value"].get("sellable") is False for x in data["cells"].values())
                )
                status = (
                    "CLOSED"
                    if closed
                    else "REMOTE_AHEAD"
                    if complete and data.get("revision", 0) > row["delivery_revision"]
                    else "OBSERVED"
                    if complete
                    else "MISSING"
                    if response.status_code == 404
                    else "UNKNOWN"
                )
                observations.append(
                    {
                        "listing_id": str(row["id"]),
                        "tenant_id": str(row["tenant_id"]),
                        "channel": row["code"],
                        "local_delivery_revision": row["delivery_revision"],
                        "remote_revision": data.get("revision"),
                        "classification": status,
                        "http_status": response.status_code,
                        "complete": complete,
                        "remote": data,
                    }
                )
            except Exception as exc:
                observations.append(
                    {
                        "listing_id": str(row["id"]),
                        "classification": "UNKNOWN",
                        "error_type": type(exc).__name__,
                    }
                )
        # Do not invent local approvals/receipts from these reads. Old work is quarantined.
        sources = c.execute(
            "SELECT DISTINCT tenant_id,supplier_id::text||'/'||property_code AS key FROM source_activation UNION SELECT tenant_id,source_key AS key FROM g3_listing UNION SELECT tenant_id,source_key AS key FROM g4_subscription"
        ).fetchall()
        for source in sources:
            c.execute(
                "INSERT INTO g5_recovery_scope(tenant_id,source_key,backup_id,reason) VALUES(%s,%s,%s,'Restored history quarantined; remote may be newer; explicit new snapshot review and scope-release required') ON CONFLICT DO NOTHING",
                (source["tenant_id"], source["key"], report["backup_id"]),
            )
        c.execute(
            "UPDATE execution_job SET state='CANCELLED',error='RECOVERY_QUARANTINE',lease_until=NULL WHERE state IN ('READY','RETRY_WAIT','RUNNING')"
        )
        c.execute(
            "UPDATE g3_listing SET paused=true,stop_epoch=stop_epoch+1,version=version+1 WHERE NOT paused"
        )
        c.execute(
            "UPDATE g4_policy SET state='PAUSED',epoch=epoch+1,version=version+1 WHERE state='ENABLED'"
        )
        c.execute(
            "UPDATE g4_subscription SET state='PAUSED',version=version+1 WHERE state='ACTIVE'"
        )
    path = ROOT / ".runtime/g5-restore-permits"
    path.mkdir(exist_ok=True)
    (path / (name + ".json")).write_text(
        json.dumps(
            {
                "database": name,
                "mode": "QUARANTINED_NEW_SOURCES_ONLY",
                "backup_id": report["backup_id"],
                "created_at": datetime.now(UTC).isoformat(),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    evidence = {
        "schema": "g5-recovery-quarantine-v1",
        "database": name,
        "at": datetime.now(UTC).isoformat(),
        "remote_mutations": 0,
        "observations": observations,
        "held_source_count": len(sources),
        "old_tasks": "cancelled; original events/receipts retained",
        "new_business": "new independent source scopes allowed after instance restart",
        "old_business": "HELD; no automatic scope release or old approval reuse",
        "limitations": "CLOSED is remote observation, not a newly created local STOPPED target; no invented applied revision",
    }
    out = ROOT / f"docs/acceptance/g5-reconcile-{run_id}.json"
    out.write_text(json.dumps(jsonable_encoder(evidence), indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(out), "held_sources": len(sources), "remote_mutations": 0}))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--run-id", required=True)
    main(p.parse_args().run_id)
