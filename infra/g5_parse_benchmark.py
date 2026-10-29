"""Six independent samples per size/format, including an explicitly excluded warmup."""

import json
import os
import platform
import statistics
import subprocess
import sys
import time
from datetime import UTC, datetime

import psutil

from infra.manage import ROOT


def main():
    if psutil.disk_usage(str(ROOT)).free < 10 * 1024**3:
        raise RuntimeError("BENCHMARK_REQUIRES_10_GIB_FREE")
    env = os.environ.copy()
    env.update(
        HOTEL_G1_TEST_MODE="true", HOTEL_TEST_ENV_PATH=str(ROOT / ".runtime/g5-benchmark.env")
    )
    result = {
        "schema": "g5-parse-benchmark-v1",
        "started_at": datetime.now(UTC).isoformat(),
        "platform": platform.platform(),
        "python": sys.version,
        "cpu": psutil.cpu_count(),
        "memory_bytes": psutil.virtual_memory().total,
        "scope": "parse/normalize/validate/persist/checkpoints; excludes activation and channel",
        "samples": [],
        "summary": [],
    }
    target = ROOT / "docs/acceptance/g5-parse-benchmark.json"
    for rows in [10000, 100000]:
        for fmt in ["csv", "xlsx", "json"]:
            for i in range(6):
                start = time.monotonic()
                p = subprocess.Popen(
                    [
                        sys.executable,
                        "-s",
                        "-m",
                        "infra.g5_parse_case",
                        "--format",
                        fmt,
                        "--rows",
                        str(rows),
                        "--phase",
                        "warmup" if i == 0 else "measured",
                    ],
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                peak = 0
                while p.poll() is None:
                    try:
                        parent = psutil.Process(p.pid)
                        family = {x.pid: x for x in [parent, *parent.children(recursive=True)]}
                        peak = max(peak, sum(x.memory_info().rss for x in family.values()))
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                    if time.monotonic() - start > 300:
                        p.terminate()
                        p.wait(timeout=10)
                        raise RuntimeError("PARSE_BENCHMARK_DEADLINE")
                    time.sleep(0.05)
                out, err = p.communicate()
                sample = {
                    "format": fmt,
                    "rows": rows,
                    "index": i,
                    "warmup": i == 0,
                    "exit_code": p.returncode,
                    "peak_process_tree_mib": round(peak / 1024**2, 2),
                }
                if p.returncode == 0:
                    sample.update(json.loads(out.strip().splitlines()[-1]))
                else:
                    sample["error"] = "CHILD_FAILED"  # never publish raw environment in reports
                result["samples"].append(sample)
                target.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
                print(json.dumps(sample), flush=True)
            measured = [
                x
                for x in result["samples"]
                if x["rows"] == rows and x["format"] == fmt and not x["warmup"]
            ]
            times = [x["seconds"] for x in measured if x["exit_code"] == 0]
            result["summary"].append(
                {
                    "format": fmt,
                    "rows": rows,
                    "n": len(times),
                    "failures": sum(x["exit_code"] != 0 for x in measured),
                    "p50_seconds": statistics.median(times) if times else None,
                    "p95_nearest_rank_seconds": max(times) if times else None,
                    "max_peak_mib": max(x["peak_process_tree_mib"] for x in measured),
                    "targets": "p95<=120s, peak<=1024MiB; only this layer",
                }
            )
    result["completed_at"] = datetime.now(UTC).isoformat()
    target.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
