"""Create a real backup window, then run both live-remote restore cases."""

import argparse
import json
from datetime import UTC, datetime
from uuid import uuid4

from infra.g5_http import Scenario
from infra.g5_recovery import restore, verify
from infra.g5_restore_drill import main as drill
from infra.manage import ROOT
from services.core.version import SCHEMA_VERSION

STATE = ROOT / ".runtime/g5-current-recovery-window.json"


def main(stage, backup=None):
    if stage == "prepare":
        s = Scenario(days=3)
        try:
            bundle = s.initialize()
            current = s.update(bundle, 2)
            data = {"bundle": bundle, "before_backup": current, "prepared_at": datetime.now(UTC).isoformat()}
        finally:
            s.close()
    else:
        data = json.loads(STATE.read_text(encoding="utf-8"))
        manifest = verify(backup)
        assert manifest["core_schema"] == SCHEMA_VERSION
        assert manifest["completed_at"] > data["prepared_at"]
        data["backup"] = str(backup)
        data["backup_completed_at"] = manifest["completed_at"]
        s = Scenario(days=3)
        try:
            data["after_backup"] = s.update(data["bundle"], 3, "106.00")
        finally:
            s.close()
        STATE.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        listings = [b["listing_id"] for b in data["before_backup"]["plan"]["branches"]]
        source = data["bundle"]["policy"]["source_key"]
        results = []
        stamp = uuid4().hex[:6]
        for label, scenario in [("a", "remote_ahead"), ("b", "remote_already_closed")]:
            run_id = "current" + label + stamp
            restored = restore(backup, run_id)
            drill(run_id, source, listings)
            raw = json.loads((ROOT / f"docs/acceptance/g5-restore-business-{run_id}.json").read_text(encoding="utf-8"))
            assert raw["status"] == "PASSED_CONSERVATIVE_RESTORE"
            observed = json.loads((ROOT / f"docs/acceptance/g5-reconcile-{run_id}.json").read_text(encoding="utf-8"))
            selected = [x for x in observed["observations"] if x["listing_id"] in listings]
            assert len(selected) == 2
            assert all(x["classification"] == ("REMOTE_AHEAD" if label == "a" else "CLOSED") for x in selected)
            results.append({"run_id": run_id, "scenario": scenario, "rto_seconds": raw["restore_plus_reconciliation_seconds"], "restore_report": "g5-restore-" + run_id + ".json", "business_report": "g5-restore-business-" + run_id + ".json", "inventory_hashes_identical": all(x["content_matches"] for x in restored["databases"].values())})
        report = {"at": datetime.now(UTC).isoformat(), "status": "PASSED", "schema": SCHEMA_VERSION, "backup": str(backup), "backup_completed_at": manifest["completed_at"], "runs": results, "rpo_note": "Both drills use this consistent snapshot; the known post-backup sequence3 is outside the core backup and is detected via actual remote reads. Backup age is measured by timestamps, not a claim of zero data loss.", "same_disk_limitation": True}
        assert all(r["rto_seconds"] <= 900 for r in results)
        (ROOT / "docs/acceptance/g5-restore-summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    STATE.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"stage": stage, "source": data["bundle"]["code"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["prepare", "drills"])
    parser.add_argument("--backup")
    args = parser.parse_args()
    main(args.stage, args.backup)
