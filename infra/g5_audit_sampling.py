"""Read-only sampling of twenty distinct, actually delivered snapshot histories."""

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

from infra.g5_http import Scenario
from infra.manage import ROOT, uid
from services.core.db import Database
from services.core.g5.audit import evidence_package, verify_package


def main():
    s = Scenario(days=3)
    db = Database(s.settings.database_url)
    db.pool.open()
    report = {"at": datetime.now(UTC).isoformat(), "samples": [], "status": "IN_PROGRESS"}
    try:
        fixtures = json.loads((ROOT / "docs/acceptance/g5-audit-fixtures.json").read_text(encoding="utf-8"))
        assert fixtures["status"] == "PASSED"
        selected = [UUID(x) for x in fixtures["snapshot_ids"]]
        with db.transaction(uid("tenant-A")) as c:
            rows = c.execute(
                "SELECT s.id,a.code AS channel,CASE WHEN s.reviewer LIKE 'system.%%' THEN 'SYSTEM_POLICY' ELSE 'HUMAN' END AS approval_kind FROM g3_snapshot s JOIN g3_account a ON a.id=s.account_id WHERE s.id=ANY(%s) AND EXISTS(SELECT 1 FROM g3_intent i JOIN g3_task t ON t.intent_id=i.id WHERE i.snapshot_id=s.id AND t.state='APPLIED') ORDER BY a.code,approval_kind,s.created_at",
                (selected,),
            ).fetchall()
        report["selection"] = "Twenty predeclared snapshots across five actual bounded source scenarios: SIM-A/SIM-B × HUMAN/SYSTEM_POLICY, five per stratum. Prior historical sample is preserved separately, including honest 400-node truncation; these curated acceptance scenarios are not a random audit of all historical data."
        assert len(rows) == 20, "Twenty actual delivered snapshots required"
        for row in rows:
            graph = s.get("/v5/audit/snapshot/" + str(row["id"]), role="auditor")
            tables = {x["table"] for x in graph["nodes"]}
            required = {"g3_snapshot", "g3_intent", "g3_task", "g3_receipt", "g3_observation", "source_activation", "artifact"}
            if row["approval_kind"] == "SYSTEM_POLICY":
                required |= {"g4_decision", "g4_policy", "g4_run"}
            package = evidence_package(graph)
            assert verify_package(package)
            raw = json.dumps(package, ensure_ascii=False, sort_keys=True).encode()
            item = {"snapshot_id": str(row["id"]), "channel": row["channel"], "approval_kind": row["approval_kind"], "complete": graph["complete"], "truncated": graph["truncated"], "missing": graph["missing"], "tables": sorted(tables), "hash_mismatches": [n["id"] for n in graph["nodes"] if n["hash_status"] == "MISMATCH"], "package_sha256": hashlib.sha256(raw).hexdigest(), "nodes": len(graph["nodes"])}
            item["passed"] = graph["complete"] and required <= tables and not item["hash_mismatches"]
            report["samples"].append(item)
        report["strata"] = sorted({x["channel"] + ":" + x["approval_kind"] for x in report["samples"]})
        report["status"] = "PASSED" if len(report["strata"]) == 4 and all(x["passed"] for x in report["samples"]) else "FAILED"
    finally:
        db.pool.close()
        s.close()
        (ROOT / "docs/acceptance/g5-audit-sampling.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "samples": len(report["samples"])}))
    if report["status"] != "PASSED":
        raise RuntimeError("AUDIT_SAMPLING_FAILED")


if __name__ == "__main__":
    main()
