"""Normal real-HTTP automation, real queued AI tasks and stop confirmation samples."""

import json
import statistics
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from infra.g5_http import Scenario
from infra.manage import ROOT


def main():
    scenario = Scenario(days=30)
    target = ROOT / "docs/acceptance/g5-load.json"
    result = {
        "schema": "g5-load-v1",
        "started_at": datetime.now(UTC).isoformat(),
        "source_count": 10,
        "days": 30,
        "channels": 2,
        "rounds": 5,
        "samples": [],
        "stops": [],
        "ai_tasks": [],
        "failures": [],
        "scope": "local synthetic S-A distinct sources; accounts share configured quotas",
    }
    try:
        bundles = []
        for i in range(10):
            bundles.append(scenario.initialize())
            print("Prepared independent source " + str(i + 1), flush=True)
        result["bundles"] = bundles
        target.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        last = {}
        for round_no, concurrency in enumerate([1, 2, 4, 2, 4], start=1):

            def work(bundle):
                try:
                    value = scenario.update(bundle, round_no + 1, "105.00")
                    value.update(round=round_no, concurrency=concurrency)
                    return value
                except Exception as exc:
                    return {
                        "code": bundle["code"],
                        "round": round_no,
                        "concurrency": concurrency,
                        "status": "FAILED",
                        "error_type": type(exc).__name__,
                        "error_code":str(exc)[:200] if isinstance(exc,RuntimeError) else None,
                    }

            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                values = list(pool.map(work, bundles))
            for value in values:
                result["samples"].append(value)
                if value["status"] == "VERIFIED":
                    last[value["code"]] = value
                else:
                    result["failures"].append(value)
            target.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            print(
                "Completed round " + str(round_no) + " with concurrency " + str(concurrency),
                flush=True,
            )
        # Queue actual model tasks against current hotel source entities before each stop.
        for bundle in bundles:
            latest = last.get(bundle["code"])
            if not latest:
                continue
            workflow = scenario.get("/v4/runs/" + latest["workflow"]["id"])
            sources = scenario.get(
                "/v1/match-proposals?activation_id=" + workflow["activation_id"]
            )["items"]
            source = next(x for x in sources if x["kind"] == "property")
            task = scenario.post("/v4/suggestions", {"source_id": source["id"]})
            result["ai_tasks"].append({"id": task["id"], "initial_state": task["state"]})
        for bundle in bundles:
            if bundle["code"] in last:
                stop = scenario.stop(last[bundle["code"]]["plan"])
                stop["code"] = bundle["code"]
                result["stops"].append(stop)
                target.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        for task in result["ai_tasks"]:
            done = scenario.wait(
                "/v4/suggestions/" + task["id"], lambda t: t["state"] == "COMPLETED"
            )
            task.update(
                final_state=done["state"],
                result_status=done["result"]["status"],
                attempt_history=done.get("attempt_history", []),
            )
        times = [x["seconds"] for x in result["samples"] if x["status"] == "VERIFIED"]
        result["summary"] = {
            "accepted_updates": len(result["samples"]),
            "verified": len(times),
            "failed": len(result["failures"]),
            "p50_seconds": statistics.median(times) if times else None,
            "p95_nearest_rank_seconds": sorted(times)[
                max(0, __import__("math").ceil(0.95 * len(times)) - 1)
            ]
            if times
            else None,
            "max_seconds": max(times) if times else None,
            "stop_count": len(result["stops"]),
            "stop_max_seconds": max((x["seconds"] for x in result["stops"]), default=None),
        }
        result["completed_at"] = datetime.now(UTC).isoformat()
        target.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result["summary"]), flush=True)
        if result["failures"] or len(times) != 50 or len(result["stops"]) != 10:
            raise RuntimeError("LOAD_ACCEPTANCE_FAILED")
    finally:
        scenario.close()


if __name__ == "__main__":
    main()
