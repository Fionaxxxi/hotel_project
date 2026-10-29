"""Actual maintenance failure: hold a local table lock beyond backup lock timeout."""

import argparse
import json
import subprocess
import sys
import time
from datetime import UTC, datetime

import httpx
import psycopg

from infra.g5_recovery import connection
from infra.manage import ROOT
from services.core.config import Settings


def main(blocker=False):
    Settings.load()
    if blocker:
        with psycopg.connect(connection("hotel_project")) as c:
            c.execute("LOCK TABLE activation_command IN ACCESS EXCLUSIVE MODE")
            print("CONTROLLED_LOCAL_LOCK_READY", flush=True)
            time.sleep(60)
        return
    started = datetime.now(UTC)
    child = subprocess.Popen([sys.executable, "-s", "-m", "infra.g5_backup_failure_drill", "--blocker"], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        assert child.stdout.readline().strip() == "CONTROLLED_LOCAL_LOCK_READY"
        with (ROOT / ".runtime/g5-controlled-backup-failure.log").open("w", encoding="utf-8") as log:
            result = subprocess.run(["pwsh", "-NoProfile", "-File", str(ROOT / "infra/scripts/g5.ps1"), "backup"], cwd=ROOT, stdout=log, stderr=log, timeout=150)
        with httpx.Client(trust_env=False, timeout=3) as http:
            ready = http.get("http://127.0.0.1:8000/health/ready")
        incomplete = []
        complete = []
        for directory in (ROOT / ".runtime/g5-backups").iterdir():
            if directory.stat().st_mtime < started.timestamp():
                continue
            path = directory / "manifest.json"
            manifest = json.loads(path.read_text()) if path.exists() else {}
            (complete if manifest.get("status") == "COMPLETE" else incomplete).append(directory.name)
        passed = result.returncode != 0 and ready.status_code == 200 and incomplete and not complete
        report = {"at": started.isoformat(), "status": "PASSED" if passed else "FAILED", "injection": "Actual normal-core table lock held while real backup command reaches its 5-second lock timeout; no row mutation", "command_exit_code": result.returncode, "normal_api_ready_after_failure": ready.status_code, "incomplete_backup_directories": incomplete, "incorrect_complete_packages": complete, "private_log": ".runtime/g5-controlled-backup-failure.log"}
        (ROOT / "docs/acceptance/g5-backup-failure-drill.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        assert passed, "FAILED_BACKUP_MUST_RESTORE_SERVICES_AND_REJECT_PACKAGE"
        print("Controlled real backup timeout: incomplete package retained and normal services restarted")
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--blocker", action="store_true")
    main(parser.parse_args().blocker)
