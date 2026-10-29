"""Verify repeat setup preserves existing configuration, business rows and checksums."""

import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime

import psycopg
from psycopg import sql

from infra.manage import ROOT, uid
from services.core.config import Settings
from services.core.db import Database


def snapshot():
    settings = Settings.load()
    with psycopg.connect(settings.migration_url) as c:
        rows = c.execute("SELECT version,sha256 FROM migration_history ORDER BY version").fetchall()
        rls = c.execute("SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class WHERE relnamespace='public'::regnamespace AND relname=ANY(%s)", (["g5_alert", "g5_recovery_scope", "g3_listing", "processing_record"],)).fetchall()
    db = Database(settings.database_url)
    db.pool.open()
    counts = {}
    try:
        for tenant in ["A", "B"]:
            with db.transaction(uid("tenant-" + tenant)) as c:
                counts[tenant] = {table: c.execute(sql.SQL("SELECT count(*) AS n FROM {}").format(sql.Identifier(table))).fetchone()["n"] for table in ["import_batch", "g3_listing", "g3_snapshot", "g3_receipt", "audit_event"]}
    finally:
        db.pool.close()
    return {"env_sha256": hashlib.sha256((ROOT / ".env").read_bytes()).hexdigest(), "migration_checksums": dict(rows), "business_counts": counts, "rls": [{"table": t, "enabled": e, "forced": f} for t, e, f in rls]}


def main():
    # Apply forward migrations first, then verify repeating the same setup.
    for module in ["infra.g3_setup", "infra.g5_setup"]:
        subprocess.run([sys.executable, "-s", "-m", module], cwd=ROOT, check=True)
    before = snapshot()
    for module in ["infra.g3_setup", "infra.g5_setup"]:
        subprocess.run([sys.executable, "-s", "-m", module], cwd=ROOT, check=True)
    after = snapshot()
    files_valid = all(hashlib.sha256((ROOT / "infra/migrations" / (version + ".sql")).read_bytes()).hexdigest() == checksum for version, checksum in after["migration_checksums"].items())
    passed = before == after and files_valid and all(x["enabled"] and x["forced"] for x in after["rls"])
    report = {"at": datetime.now(UTC).isoformat(), "status": "PASSED" if passed else "FAILED", "before": before, "after": after, "applied_files_match": files_valid, "scope": "Current normal stores; append-only upgrade, then repeat setup without resetting any database. Environment contents never exported."}
    (ROOT / "docs/acceptance/g5-setup-idempotence.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "migration_count": len(after["migration_checksums"])}))
    assert passed


if __name__ == "__main__":
    main()
