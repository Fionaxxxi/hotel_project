"""Timed real-service workload. Incremental evidence survives an interrupted run."""

import argparse
import json
import time
from datetime import UTC, datetime

import psutil

from infra.g5_http import Scenario
from infra.manage import ROOT


def resources():
    roots = {}
    for marker in (ROOT / ".runtime").glob("*.pid"):
        if marker.stem not in {
            "api",
            "sim",
            "sim-b",
            "g3-worker-0",
            "g3-worker-1",
            "g2-worker-0",
            "g2-worker-1",
            "g4-flow",
            "g4-ai",
            "g5-ops",
        }:
            continue
        try:
            roots[marker.stem] = psutil.Process(int(marker.read_text().strip()))
        except (ValueError, psutil.Error):
            continue
    pg = ROOT / ".runtime/postgres-g0/postmaster.pid"
    if pg.exists():
        try:
            roots["postgres"] = psutil.Process(int(pg.read_text().splitlines()[0]))
        except (ValueError, psutil.Error):
            pass
    seen = set()
    result = {}
    for label, parent in roots.items():
        try:
            family = {p.pid: p for p in [parent, *parent.children(recursive=True)]}
            rss = 0
            for pid, process in family.items():
                if pid not in seen:
                    rss += process.memory_info().rss
                    seen.add(pid)
            result[label] = {"rss_bytes": rss, "pids": list(family)}
        except psutil.Error:
            result[label] = {"error": "PROCESS_UNAVAILABLE"}
    return {
        "processes": result,
        "rss_sum_bytes": sum(x.get("rss_bytes", 0) for x in result.values()),
        "host_available_bytes": psutil.virtual_memory().available,
        "rss_note": "unique PIDs; RSS shared pages may still be counted across processes",
    }


def main(seconds):
    scenario = Scenario(days=3)
    result = {
        "schema": "g5-soak-v1",
        "started_at": datetime.now(UTC).isoformat(),
        "requested_seconds": seconds,
        "status": "RUNNING",
        "samples": [],
        "updates": [],
        "ai": [],
        "failures": [],
    }
    target = ROOT / "docs/acceptance/g5-soak.json"
    try:
        bundle = scenario.initialize()
        result["source"] = bundle["code"]
        start = time.monotonic()
        next_update = 0
        sequence = 2
        last = None
        while time.monotonic() - start < seconds:
            elapsed = time.monotonic() - start
            if elapsed >= next_update:
                try:
                    if sequence % 15 == 0:
                        scenario.close()
                        scenario = Scenario(days=3)
                    last = scenario.update(bundle, sequence, "105.00")
                    result["updates"].append(last)
                    run = scenario.get("/v4/runs/" + last["workflow"]["id"])
                    source = next(
                        x
                        for x in scenario.get(
                            "/v1/match-proposals?activation_id=" + run["activation_id"]
                        )["items"]
                        if x["kind"] == "property"
                    )
                    task = scenario.post("/v4/suggestions", {"source_id": source["id"]})
                    done = scenario.wait(
                        "/v4/suggestions/" + task["id"], lambda x: x["state"] == "COMPLETED"
                    )
                    result["ai"].append(
                        {
                            "task_id": task["id"],
                            "status": done["result"]["status"],
                            "attempts": done.get("attempt_history", []),
                        }
                    )
                except Exception as exc:
                    result["failures"].append(
                        {
                            "elapsed_seconds": elapsed,
                            "operation": "update/inference",
                            "error_type": type(exc).__name__,
                            "error_code":str(exc)[:200] if isinstance(exc,RuntimeError) else None,
                        }
                    )
                sequence += 1
                next_update += 60
            sample = {
                "elapsed_seconds": time.monotonic() - start,
                "at": datetime.now(UTC).isoformat(),
                "resources": resources(),
            }
            try:
                sample["metrics"] = scenario.get("/v5/operations/metrics")
                sample["status"] = "OK"
            except Exception as exc:
                sample.update(status="FAILED", error_type=type(exc).__name__)
                result["failures"].append(
                    {
                        "elapsed_seconds": sample["elapsed_seconds"],
                        "operation": "metrics",
                        "error_type": type(exc).__name__,
                            "error_code":str(exc)[:200] if isinstance(exc,RuntimeError) else None,
                    }
                )
            result["samples"].append(sample)
            result["elapsed_seconds"] = time.monotonic() - start
            target.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            if len(result["samples"]) % 6 == 0:
                print(
                    json.dumps(
                        {
                            "elapsed_seconds": round(result["elapsed_seconds"]),
                            "updates": len(result["updates"]),
                            "failures": len(result["failures"]),
                        }
                    ),
                    flush=True,
                )
            time.sleep(min(10, max(0, seconds - (time.monotonic() - start))))
        if last:
            result["stop"] = scenario.stop(last["plan"])
        result.update(
            status="PASSED" if not result["failures"] else "FAILED",
            completed_at=datetime.now(UTC).isoformat(),
            elapsed_seconds=time.monotonic() - start,
        )
        target.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(
            json.dumps(
                {
                    "status": result["status"],
                    "elapsed_seconds": result["elapsed_seconds"],
                    "updates": len(result["updates"]),
                }
            ),
            flush=True,
        )
        if result["failures"]:
            raise RuntimeError("SOAK_ACCEPTANCE_FAILED")
    finally:
        scenario.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--seconds", type=int, default=3600)
    main(p.parse_args().seconds)
