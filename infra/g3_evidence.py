"""Export local acceptance evidence without credentials or connection strings."""

import json
import os
import platform
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import httpx
import psutil
import psycopg

from infra.manage import ROOT
from services.core.config import Settings


def main():
    Settings.load()
    target = ROOT / "docs/acceptance"
    tree = ET.parse(target / "g3-tests.xml")
    suite = tree.getroot().find("testsuite")
    assert suite is not None
    assert suite.attrib["failures"] == suite.attrib["errors"] == "0"
    cases = [
        {
            "class": c.attrib.get("classname"),
            "name": c.attrib["name"],
            "seconds": float(c.attrib["time"]),
            "result": "passed",
        }
        for c in suite.findall("testcase")
        if "test_g3" in c.attrib.get("classname", "")
    ]
    (target / "g3-faults.json").write_text(
        json.dumps({"suite": suite.attrib, "g3_cases": cases}, ensure_ascii=False, indent=2),
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
                "version": c.execute("SELECT version()").fetchone()[0],
                "migrations": c.execute(
                    "SELECT version,sha256 FROM migration_history ORDER BY version"
                ).fetchall(),
                "role_flags": c.execute(
                    "SELECT rolname,rolsuper,rolbypassrls FROM pg_roles WHERE rolname LIKE 'hotel%app' OR rolname LIKE 'hotel%migrator' ORDER BY rolname"
                ).fetchall(),
            }
    processes = {}
    for name in ["api", "g3-worker-0", "g3-worker-1", "g2-worker-0", "g2-worker-1", "sim", "sim-b"]:
        p = psutil.Process(int((ROOT / ".runtime" / (name + ".pid")).read_text()))
        assert os.path.normcase(p.exe()) == os.path.normcase(sys.executable)
        assert p.cwd().lower() == str(ROOT).lower()
        processes[name] = {
            "pid": p.pid,
            "executable": p.exe(),
            "cwd": p.cwd(),
            "command": p.cmdline(),
        }
    with httpx.Client(trust_env=False) as c:
        health = {
            str(port): c.get(f"http://127.0.0.1:{port}/g3/health").json() for port in [8001, 8002]
        }
        ready = c.get("http://127.0.0.1:8000/health/ready")
        ready.raise_for_status()
        browser_path = target / "g3-browser.json"
        if browser_path.exists():
            browser = json.loads(browser_path.read_text(encoding="utf-8"))
            login = c.post(
                "http://127.0.0.1:8000/dev/token",
                json={"subject": "a.tenant_admin", "password": os.environ["DEV_LOGIN_PASSWORD"]},
            )
            login.raise_for_status()
            headers = {"Authorization": "Bearer " + login.json()["access_token"]}
            overview = c.get("http://127.0.0.1:8000/v3/overview", headers=headers)
            overview.raise_for_status()
            listings = [
                li
                for li in overview.json()["listings"]
                if li["listing_key"] == browser["listing_key"]
            ]
            assert len(listings) == 2 and all(li["state"] == "STOPPED" for li in listings)
            assert all(li["desired_revision"] == li["observed_revision"] == 2 for li in listings)
            browser["server_confirmations"] = listings
            browser_path.write_text(
                json.dumps(browser, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    (target / "g3-environment.json").write_text(
        json.dumps(
            {
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "python": sys.executable,
                "python_version": platform.python_version(),
                "databases": databases,
                "processes": processes,
                "sim_health": health,
                "core_ready": ready.json(),
                "normal_faults_enabled": os.environ.get("G3_FAULTS_ENABLED", "false"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print("Final passing JUnit and local environment evidence exported")


if __name__ == "__main__":
    main()
