import json
import os
import platform
import subprocess
import sys
import time

import psutil

from infra.manage import ROOT


def main():
    if psutil.disk_usage(str(ROOT)).free < 3 * 1024**3:
        raise SystemExit("At least 3 GiB free disk required for non-destructive benchmark records")
    env = os.environ.copy()
    env["HOTEL_G1_TEST_MODE"] = "true"
    results = []
    for size in [10000, 100000]:
        for fmt in ["csv", "xlsx", "json"]:
            for phase in ["first-touch", "repeat"]:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-s",
                        "-m",
                        "infra.g2_benchmark_case",
                        "--format",
                        fmt,
                        "--rows",
                        str(size),
                        "--phase",
                        phase,
                    ]
                    + (["--reuse-run", results[-1]["run_id"]] if phase == "repeat" else []),
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                peak = 0
                started = time.monotonic()
                try:
                    while process.poll() is None:
                        try:
                            family = [
                                psutil.Process(process.pid),
                                *psutil.Process(process.pid).children(recursive=True),
                            ]
                            peak = max(
                                peak, sum(p.memory_info().rss for p in family if p.is_running())
                            )
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            pass
                        if time.monotonic() - started > 300:
                            process.terminate()
                            raise RuntimeError("Benchmark case exceeded 300 seconds")
                        time.sleep(0.03)
                    out, err = process.communicate()
                    if process.returncode:
                        raise RuntimeError("Benchmark failed: " + err[-600:])
                    record = json.loads(out.strip().splitlines()[-1])
                    record["peak_process_tree_mib"] = round(peak / 1024**2, 2)
                    results.append(record)
                    print(
                        f"{fmt} {size} {phase}: {record['seconds']} s, peak {record['peak_process_tree_mib']} MiB",
                        flush=True,
                    )
                    (ROOT / "docs/acceptance/g2-benchmark.json").write_text(
                        json.dumps(
                            {
                                "platform": platform.platform(),
                                "python": sys.version,
                                "cpu_logical": psutil.cpu_count(),
                                "memory_gib": round(psutil.virtual_memory().total / 1024**3, 2),
                                "stage": "normalize, validate, persist, checkpoints; excludes upload, activation, compiler and channel",
                                "batch_size": 1000,
                                "samples_per_cell": 1,
                                "concurrency": 1,
                                "data_distribution": "unique rooms only; text codes, five fields; no duplicate natural keys; no activation baseline",
                                "dependencies": "requirements.lock.txt; PostgreSQL18.4; same host as live API/SIM and three idle G2 workers",
                                "cache_note": "No OS/DB cache flushing. first-touch uses a new file; repeat reparses the exact same artifact with a new supported template and fresh process. Warm OS/DB cache is likely, not enforced. No strict cold-cache or p95 claim.",
                                "results": results,
                            },
                            ensure_ascii=False,
                            indent=2,
                        )
                        + "\n"
                    )
                finally:
                    if process.poll() is None:
                        process.terminate()
                        process.wait(timeout=5)
    lines = [
        "# G2 流式处理基准",
        "",
        "> 合成数据；独立测试库；仅解析、校验、持久化及检查点，不包括上传、来源激活、报价或渠道。每档每状态1样本，不计算p95，不声称SLA。first-touch为新文件，repeat为同一原件新模板重解析、新进程；未清OS/DB缓存，不代表严格冷缓存。",
        "",
        "| 格式 | 记录 | 运行 | 文件字节 | 耗时秒 | 峰值进程树MiB |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    lines.extend(
        f"| {r['format']} | {r['rows']} | {r['phase']} | {r['file_bytes']} | {r['seconds']} | {r['peak_process_tree_mib']} |"
        for r in results
    )
    (ROOT / "docs/acceptance/g2-benchmark.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
