"""Final acceptance metadata, deliberately excluding secrets and connection strings."""

import hashlib
import importlib.metadata
import json
import os
import platform
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

import httpx
import psutil
import psycopg

from infra.manage import ROOT
from services.core.config import Settings
from services.core.g4.local_model import DIRECTORY


def main():
    Settings.load()
    target = ROOT / "docs/acceptance"
    suite = ET.parse(target / "g4-tests.xml").getroot().find("testsuite")
    assert suite is not None and suite.attrib["failures"] == suite.attrib["errors"] == "0"
    selected = [
        {
            "class": c.attrib["classname"],
            "name": c.attrib["name"],
            "seconds": float(c.attrib["time"]),
            "result": "passed",
        }
        for c in suite.findall("testcase")
        if "test_g4" in c.attrib["classname"]
    ]
    race = ET.parse(target / "g4-race-tests.xml").getroot().find("testsuite")
    assert race is not None and race.attrib["failures"] == race.attrib["errors"] == "0"
    hard = ET.parse(target / "g4-hard-conflicts.xml").getroot().find("testsuite")
    assert (
        hard is not None
        and hard.attrib["failures"] == hard.attrib["errors"] == "0"
        and hard.attrib["tests"] == "60"
    )
    (target / "g4-faults.json").write_text(
        json.dumps(
            {
                "suite": suite.attrib,
                "g4_testcases": selected,
                "additional_actual_http_race_suite": race.attrib,
                "frozen_distinct_hard_conflict_suite": hard.attrib,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    databases = {}
    for label, key in [
        ("core", "MIGRATION_DATABASE_URL"),
        ("sim_a", "SIM_MIGRATION_URL"),
        ("sim_b", "SIM_B_MIGRATION_URL"),
    ]:
        with psycopg.connect(os.environ[key]) as c:
            databases[label] = {
                "database": c.execute("SELECT current_database()").fetchone()[0],
                "postgres": c.execute("SELECT version()").fetchone()[0],
                "migration_checksums": c.execute(
                    "SELECT version,sha256 FROM migration_history ORDER BY version"
                ).fetchall(),
                "app_roles": c.execute(
                    "SELECT rolname,rolsuper,rolbypassrls FROM pg_roles WHERE rolname LIKE 'hotel%app' ORDER BY rolname"
                ).fetchall(),
            }
            if label == "core":
                flags = c.execute(
                    "SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class WHERE relname LIKE 'g4_%%' AND relkind='r' ORDER BY relname"
                ).fetchall()
                assert len(flags) == 9 and all(row[1] and row[2] for row in flags)
                databases[label]["g4_rls_flags"] = flags
    processes = {}
    for name in [
        "api",
        "sim",
        "sim-b",
        "g2-worker-0",
        "g2-worker-1",
        "g3-worker-0",
        "g3-worker-1",
        "g4-flow",
        "g4-ai",
    ]:
        process = psutil.Process(int((ROOT / ".runtime" / (name + ".pid")).read_text()))
        assert os.path.normcase(process.exe()) == os.path.normcase(sys.executable)
        assert process.cwd().lower() == str(ROOT).lower()
        processes[name] = {
            "pid": process.pid,
            "executable": process.exe(),
            "cwd": process.cwd(),
            "command": process.cmdline(),
        }
    manifest = json.loads((DIRECTORY / "manifest.json").read_text())
    for name, info in manifest["files"].items():
        with (DIRECTORY / name).open("rb") as f:
            assert hashlib.file_digest(f, "sha256").hexdigest() == info["sha256"]
    with httpx.Client(base_url="http://127.0.0.1:8000", trust_env=False) as c:
        ready = c.get("/health/ready")
        ready.raise_for_status()
        token = c.post(
            "/dev/token",
            json={"subject": "a.auditor", "password": os.environ["DEV_LOGIN_PASSWORD"]},
        )
        token.raise_for_status()
        headers = {"Authorization": "Bearer " + token.json()["access_token"]}
        overview = c.get("/v4/overview", headers=headers)
        overview.raise_for_status()
        browser_server = target / "g4-browser-server.json"
        if browser_server.exists():
            evidence = json.loads(browser_server.read_text())
            evidence["final_close_confirmations"] = []
            for branch in evidence["plan"]["branches"]:
                li = c.get("/v3/listings/" + branch["listing_id"], headers=headers)
                li.raise_for_status()
                data = li.json()
                assert (
                    data["state"] == "STOPPED"
                    and data["desired_revision"] == data["observed_revision"]
                )
                evidence["final_close_confirmations"].append(
                    {
                        "id": data["id"],
                        "state": data["state"],
                        "desired_revision": data["desired_revision"],
                        "observed_revision": data["observed_revision"],
                    }
                )
            browser_server.write_text(
                json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        final_path = target / "g4-final-browser-server.json"
        if final_path.exists():
            final = json.loads(final_path.read_text(encoding="utf-8"))
            browser = json.loads((target / "g4-browser.json").read_text(encoding="utf-8"))
            assert (
                final["status"] == "SERVER_VERIFIED_ALL_STAGES"
                and browser["status"] == "PASSED"
                and browser["uncompleted"] == []
            )
            assert final["code"] == browser["code"]
            for branch in final["exception_plan"]["branches"]:
                li = c.get("/v3/listings/" + branch["listing_id"], headers=headers)
                li.raise_for_status()
                data = li.json()
                assert (
                    data["state"] == "STOPPED"
                    and data["desired_revision"] == data["observed_revision"] == 3
                )
            assert final["attack_business_unchanged"] == {
                "all_mappings_unapproved": True,
                "no_listing_created": True,
            }
    result = {
        "captured_at": datetime.now(UTC).isoformat(),
        "python": sys.executable,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "dependencies": {
            name: importlib.metadata.version(name)
            for name in [
                "onnxruntime",
                "tokenizers",
                "numpy",
                "fastapi",
                "psycopg",
                "httpx",
                "httpx2",
            ]
        },
        "databases": databases,
        "processes": processes,
        "model_manifest": manifest,
        "model_configuration": overview.json()["model"],
        "core_ready": ready.json(),
        "normal_g3_faults": os.environ.get("G3_FAULTS_ENABLED", "false"),
        "normal_legacy_faults": os.environ.get("SIM_FAULTS_ENABLED", "false"),
    }
    (target / "g4-environment.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        "Passing test suite, exact model checksums, isolated environment and process evidence exported"
    )


if __name__ == "__main__":
    main()
