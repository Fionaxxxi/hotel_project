"""Real restored API/Workers on extra port; live remotes are never rolled back."""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv

from infra.g5_http import Scenario
from infra.g5_reconcile_restore import main as quarantine
from infra.manage import ROOT


def main(run_id, source_key, listing_ids):
    load_dotenv(ROOT / ".env")
    started = time.monotonic()
    quarantine(run_id)
    report = json.loads(
        (ROOT / f"docs/acceptance/g5-restore-{run_id}.json").read_text(encoding="utf-8")
    )
    name = report["databases"]["core"]["database"]
    if name != "hotel_project_g5_restore_" + run_id + "_core":
        raise ValueError("RESTORE_TARGET_MISMATCH")
    env = os.environ.copy()
    core_original = urlparse(os.environ["DATABASE_URL"]).path
    migration_original = urlparse(os.environ["MIGRATION_DATABASE_URL"]).path
    env.update(
        DATABASE_URL=os.environ["DATABASE_URL"].replace(core_original, "/" + name),
        MIGRATION_DATABASE_URL=os.environ["MIGRATION_DATABASE_URL"].replace(
            migration_original, "/" + name
        ),
        HOTEL_G1_TEST_MODE="false",
        HOTEL_ARTIFACT_ROOT=report["artifact_root"],
        G3_FAULTS_ENABLED="false",
        SIM_FAULTS_ENABLED="false",
        G4_AI_ENABLED="false",
        G5_OFFLINE_ONLY="true",
        G5_ISOLATED_SIMULATORS="false",
    )
    processes = []
    logs = []
    evidence = {
        "schema": "g5-restore-business-drill-v1",
        "run_id": run_id,
        "started_at": datetime.now(UTC).isoformat(),
        "old_source": source_key,
        "listing_ids": listing_ids,
        "api_port": 8005,
    }
    try:
        commands = [
            [
                "uvicorn",
                "services.core.main:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                "8005",
            ],
            ["services.core.g2.worker", "--lane", "parse"],
            ["services.core.g2.worker", "--lane", "control"],
            ["services.core.g3.worker"],
            ["services.core.g4.worker", "--lane", "flow"],
        ]
        for i, command in enumerate(commands):
            log = (ROOT / f".runtime/g5-drill-{run_id}-{i}.log").open("w")
            logs.append(log)
            processes.append(
                subprocess.Popen(
                    [sys.executable, "-s", "-m", *command],
                    env=env,
                    cwd=ROOT,
                    stdout=log,
                    stderr=log,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            )
        with httpx.Client(trust_env=False, timeout=2) as c:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if any(p.poll() is not None for p in processes):
                    raise RuntimeError("RESTORE_SERVICE_EXITED")
                try:
                    if c.get("http://127.0.0.1:8005/health/ready").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.2)
            else:
                raise RuntimeError("RESTORE_API_NOT_READY")
        scenario = Scenario(days=3, base_url="http://127.0.0.1:8005")
        try:
            held = scenario.get("/v5/recovery/scopes", "tenant_admin")["items"]
            assert any(x["source_key"] == source_key and x["state"] == "HELD" for x in held)
            evidence["held_scope_confirmed"] = True
            evidence["old_listings_before"] = [
                scenario.get("/v3/listings/" + x) for x in listing_ids
            ]
            for lid in listing_ids:
                li = scenario.get("/v3/listings/" + lid)
                scenario.post(
                    "/v5/recovery/listings/" + lid + "/stop",
                    {
                        "expected_version": li["version"],
                        "reason": "G5 real restore: retain newer remote revision, explicitly close",
                    },
                    "tenant_admin",
                )
            evidence["fresh_stops"] = [
                scenario.wait("/v3/listings/" + x, lambda r: r["state"] == "STOPPED")
                for x in listing_ids
            ]
            assert all(
                x["desired_revision"] == x["observed_revision"] for x in evidence["fresh_stops"]
            )
            evidence["release"] = scenario.post(
                "/v5/recovery/release",
                {
                    "source_key": source_key,
                    "reason": "Both channels freshly closed and verified after actual restore",
                },
                "tenant_admin",
            )
            bundle = scenario.initialize()
            update = scenario.update(bundle, 2)
            evidence["new_business"] = update
            evidence["new_business_stop"] = scenario.stop(update["plan"])
            evidence["status"] = "PASSED_CONSERVATIVE_RESTORE"
        finally:
            scenario.close()
    finally:
        for p in reversed(processes):
            if p.poll() is None:
                p.terminate()
                p.wait(timeout=10)
        for log in logs:
            log.close()
    evidence["duration_from_quarantine_seconds"] = time.monotonic() - started
    evidence["restore_plus_reconciliation_seconds"] = (
        report["duration_seconds"] + evidence["duration_from_quarantine_seconds"]
    )
    evidence["completed_at"] = datetime.now(UTC).isoformat()
    out = ROOT / f"docs/acceptance/g5-restore-business-{run_id}.json"
    out.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "report": str(out),
                "seconds": evidence["restore_plus_reconciliation_seconds"],
                "status": evidence["status"],
            }
        )
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--run-id", required=True)
    p.add_argument("--source-key", required=True)
    p.add_argument("--listing-ids", nargs="+", required=True)
    args = p.parse_args()
    main(args.run_id, args.source_key, args.listing_ids)
