"""Read-only local operations/audit benchmark; report every request including failures."""

import json
import os
import platform
import statistics
import sys
import time
from datetime import UTC, datetime

import httpx
import psutil
from dotenv import load_dotenv

from infra.manage import ROOT
from services.core.auth import issue_token


def run():
    load_dotenv(ROOT / ".env")
    headers = {"Authorization": "Bearer " + issue_token("a.auditor", os.environ["JWT_SECRET"])}
    data = {
        "schema": "g5-query-benchmark-v1",
        "started_at": datetime.now(UTC).isoformat(),
        "python": sys.version,
        "platform": platform.platform(),
        "cpu_logical": psutil.cpu_count(),
        "host_memory_bytes": psutil.virtual_memory().total,
        "scope": "read-only API metrics and bounded audit, not full workflow capacity",
        "samples": [],
        "summary": {},
    }
    with httpx.Client(
        base_url="http://127.0.0.1:8000", headers=headers, trust_env=False, timeout=15
    ) as c:
        overview = c.get("/v3/overview")
        overview.raise_for_status()
        listings = overview.json()["listings"]
        endpoints = {"metrics": "/v5/operations/metrics"}
        if listings:
            endpoints["audit"] = "/v5/audit/listing/" + listings[0]["id"]
        for label, path in endpoints.items():
            c.get(path)
            for i in range(100):
                start = time.perf_counter()
                response = c.get(path)
                data["samples"].append(
                    {
                        "endpoint": label,
                        "index": i,
                        "seconds": time.perf_counter() - start,
                        "status": response.status_code,
                        "bytes": len(response.content),
                    }
                )
            values = [s["seconds"] for s in data["samples"] if s["endpoint"] == label]
            data["summary"][label] = {
                "n": len(values),
                "p50": statistics.median(values),
                "p95_nearest_rank": sorted(values)[94],
                "max": max(values),
                "failures": sum(
                    s["status"] != 200 for s in data["samples"] if s["endpoint"] == label
                ),
            }
    data["completed_at"] = datetime.now(UTC).isoformat()
    path = ROOT / "docs/acceptance/g5-query-benchmark.json"
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(data["summary"]))


if __name__ == "__main__":
    run()
