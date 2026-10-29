"""Two clean source directories and restored independent DB triplets on THIS machine.

This is explicitly not a second Windows machine or a clean Conda installation.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from dotenv import load_dotenv

from infra.g5_http import Scenario
from infra.g5_reconcile_restore import main as quarantine
from infra.g5_recovery import restore
from infra.g5_release import source_files
from infra.manage import ROOT


def one(backup, run_id):
    restored = restore(backup, run_id)
    quarantine(run_id)
    clone = ROOT / ".runtime/g5-reproduction" / run_id
    clone.mkdir(parents=True, exist_ok=False)
    copied = 0
    for path, name in source_files():
        target = clone / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        copied += 1
    runtime = clone / ".runtime"
    runtime.mkdir()
    shutil.copytree(ROOT / ".runtime/models", runtime / "models")
    shutil.copytree(restored["artifact_root"], runtime / "artifacts")
    permits = runtime / "g5-restore-permits"
    permits.mkdir()
    for entry in restored["databases"].values():
        (permits / (entry["database"] + ".json")).write_text(
            json.dumps(
                {
                    "database": entry["database"],
                    "mode": "QUARANTINED_NEW_SOURCES_ONLY",
                    "purpose": "isolated local reproduction profile",
                }
            )
            + "\n",
            encoding="utf-8",
        )
    node = []
    for args in [["npm", "ci", "--offline"], ["npm", "run", "build"]]:
        result = subprocess.run(
            [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", *args],
            cwd=clone / "apps/admin",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
        node.append({"command": " ".join(args), "exit_code": result.returncode})
        if result.returncode:
            raise RuntimeError("ISOLATED_FRONTEND_BUILD_OR_PACKAGE_CACHE_UNAVAILABLE")
    env = os.environ.copy()
    for key, label in [
        ("DATABASE_URL", "core"),
        ("MIGRATION_DATABASE_URL", "core"),
        ("SIM_DATABASE_URL", "sim-a"),
        ("SIM_MIGRATION_URL", "sim-a"),
        ("SIM_B_DATABASE_URL", "sim-b"),
        ("SIM_B_MIGRATION_URL", "sim-b"),
    ]:
        old = urlparse(env[key]).path
        env[key] = env[key].replace(old, "/" + restored["databases"][label]["database"])
    env.update(
        HOTEL_G1_TEST_MODE="false",
        HOTEL_ARTIFACT_ROOT=str(runtime / "artifacts"),
        G5_OFFLINE_ONLY="true",
        G5_ISOLATED_SIMULATORS="true",
        G3_FAULTS_ENABLED="false",
        SIM_FAULTS_ENABLED="false",
        G4_AI_ENABLED="true",
        PYTHONPATH=str(clone),
    )
    commands = [
        [
            "uvicorn",
            "services.channel_simulator.main:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            "8006",
        ],
        [
            "uvicorn",
            "services.channel_simulator_b.main:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            "8007",
        ],
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
        ["services.core.g4.worker", "--lane", "ai"],
    ]
    processes = []
    logs = []
    try:
        for i, command in enumerate(commands):
            log = (runtime / f"service-{i}.log").open("w")
            logs.append(log)
            child_env = {**env, "HOTEL_WORKER_SLOT": run_id + "-" + str(i)}
            processes.append(
                subprocess.Popen(
                    [sys.executable, "-s", "-m", *command],
                    env=child_env,
                    cwd=clone,
                    stdout=log,
                    stderr=log,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            )
        with httpx.Client(trust_env=False, timeout=2) as c:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if any(p.poll() is not None for p in processes):
                    raise RuntimeError("REPRODUCTION_SERVICE_EXITED")
                try:
                    if all(
                        c.get(url).status_code == 200
                        for url in [
                            "http://127.0.0.1:8005/health/ready",
                            "http://127.0.0.1:8006/g3/health",
                            "http://127.0.0.1:8007/g3/health",
                        ]
                    ):
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.2)
            else:
                raise RuntimeError("REPRODUCTION_NOT_READY")
        s = Scenario(days=3, base_url="http://127.0.0.1:8005")
        try:
            bundle = s.initialize(code="G5-DEMO-001")
            update = s.update(bundle, 2)
            source = next(
                x
                for x in s.get(
                    "/v1/match-proposals?activation_id=" + update["workflow"]["activation_id"]
                )["items"]
                if x["kind"] == "property"
            )
            task = s.post("/v4/suggestions", {"source_id": source["id"]})
            suggestion = s.wait(
                "/v4/suggestions/" + task["id"], lambda r: r["state"] == "COMPLETED"
            )
            assert suggestion["result"]["status"] == "AI_AVAILABLE"
            stop = s.stop(update["plan"])
            return {
                "run_id": run_id,
                "code_root": str(clone),
                "copied_source_files": copied,
                "node": node,
                "databases": restored["databases"],
                "ports": [8005, 8006, 8007],
                "fixed_business_code": bundle["code"],
                "payload_hashes": {
                    x["code"]: x["payload_hash"] for x in update["plan"]["branches"]
                },
                "model_status": suggestion["result"]["status"],
                "stop": stop,
                "normal_simulators": "not used for reproduced business; separate restored stores on 8006/8007",
                "status": "PASSED",
            }
        finally:
            s.close()
    finally:
        for p in reversed(processes):
            if p.poll() is None:
                p.terminate()
                p.wait(timeout=10)
        for log in logs:
            log.close()


def main(backup):
    load_dotenv(ROOT / ".env")
    results = []
    stamp = uuid4().hex[:6]
    for label in ["a", "b"]:
        value = one(backup, "repro" + label + stamp)
        results.append(value)
        print("Isolated source/build/three-store reproduction " + label + " passed", flush=True)
    same = results[0]["payload_hashes"] == results[1]["payload_hashes"]
    assert same
    report = {
        "schema": "g5-local-reproduction-v1",
        "at": datetime.now(UTC).isoformat(),
        "status": "PASSED_LOCAL_ISOLATED",
        "new_windows_machine_verified": False,
        "python_environment": sys.prefix,
        "dependency_note": "existing dedicated Conda environment; two independent npm ci --offline installs from this machine cache",
        "business_ids": "fixed natural business IDs and fixture values; generated technical UUIDs differ",
        "business_payload_hashes_identical": same,
        "runs": results,
    }
    out = ROOT / "docs/acceptance/g5-local-reproduction.json"
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        "Local isolated reproduction passed twice; independent Windows-machine item remains deferred"
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--backup", required=True)
    main(p.parse_args().backup)
