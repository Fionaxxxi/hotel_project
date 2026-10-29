"""Real channel protocol benchmark on extra loopback ports and named G3 test stores.

Not a quote compiler or end-to-end business benchmark. No fault injection or resets.
"""

import json
import os
import platform
import subprocess
import sys
import time
from uuid import uuid4

import httpx
import psutil
from dotenv import load_dotenv

from infra.manage import ROOT, uid
from services.core.g1.common import digest
from services.core.g3.contract import PROFILES, cell_key, chunks, wire_content
from services.core.g3.transport import headers


def family_rss(processes):
    total = psutil.Process().memory_info().rss
    for p in processes:
        try:
            total += psutil.Process(p.pid).memory_info().rss
        except psutil.NoSuchProcess:
            pass
    return total


def case(channel, count, port, processes):
    code = "BENCH-" + uuid4().hex[:10]
    n = count // 2
    plans = [
        {
            "key": f"P{i}",
            "room_key": f"R{i}",
            "pool_key": f"POOL{i}",
            "adults": 2,
            "currency": "USD",
            "meal": "NONE",
            "payment": "PREPAID",
            "tax_basis": "NET_EXCLUSIVE",
            "cancellation": {"kind": "NONREF", "deadline_hours": 0, "penalty_basis": "stay_total"},
        }
        for i in range(n)
    ]
    content = {
        "hotel_key": code,
        "scope": {"stay_date_from": "2027-01-10", "stay_date_to_exclusive": "2027-01-11"},
        "pools": [p["pool_key"] for p in plans],
        "plans": plans,
    }
    cells = []
    for p in plans:
        cells.extend(
            [
                {
                    "kind": "pool",
                    "key": p["pool_key"],
                    "date": "2027-01-10",
                    "quantity": 1,
                    "sellable": True,
                },
                {
                    "kind": "rate",
                    "key": p["key"],
                    "date": "2027-01-10",
                    "net_amount": "100.0000",
                    "sell_amount": "110.00",
                    "sellable": True,
                },
            ]
        )
    split = chunks({"cells": cells}, channel)
    wire = wire_content(content, channel)
    manifest = {
        "payload_hash": digest({"content": content, "cells": cells}),
        "content_hash": digest(wire),
        "chunks": {str(i): digest(x) for i, x in enumerate(split, 1)},
    }
    bodies = [
        {
            "revision": 1,
            "action": "CONTENT",
            "manifest_hash": digest(manifest),
            "chunk_index": 0,
            "content": wire,
            "manifest": manifest,
            "cells": [],
        }
    ] + [
        {
            "revision": 1,
            "action": "PUBLISH",
            "manifest_hash": digest(manifest),
            "chunk_index": i,
            "content": None,
            "manifest": None,
            "cells": x,
        }
        for i, x in enumerate(split, 1)
    ]
    auth = headers(uid("tenant-A"), channel)
    pending = {}
    next_send = 0
    index = 0
    queries = 0
    rate_limits = 0
    peak = family_rss(processes)
    started = time.monotonic()
    last_progress = started
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=5) as c:
        while index < len(bodies) or pending:
            clock = time.monotonic()
            if clock - started > 720:
                raise RuntimeError("Benchmark exceeded720s: " + channel + " " + str(count))
            if index < len(bodies) and clock >= next_send:
                key = f"{code}-{index}"
                r = c.put(
                    "/g3/listings/" + code,
                    headers={**auth, "Idempotency-Key": key},
                    json=bodies[index],
                )
                next_send = clock + 1 / PROFILES[channel]["rate"] + 0.005
                if r.status_code == 429:
                    rate_limits += 1
                    next_send = clock + 1
                else:
                    r.raise_for_status()
                    result = r.json()
                    index += 1
                    if result["state"] != "DONE":
                        pending[key] = clock + 0.2
                    else:
                        assert result["result"]["status"] == "APPLIED"
            for key, due in list(pending.items()):
                if clock >= due:
                    r = c.get("/g3/operations/" + key, headers=auth)
                    queries += 1
                    r.raise_for_status()
                    result = r.json()
                    if result["state"] == "DONE":
                        assert result["result"]["status"] == "APPLIED"
                        pending.pop(key)
                    else:
                        pending[key] = clock + 0.2
            peak = max(peak, family_rss(processes))
            if clock - last_progress >= 30:
                print(
                    f"benchmark {channel} {count}: {index}/{len(bodies)} writes, {len(pending)} pending",
                    flush=True,
                )
                last_progress = clock
            time.sleep(0.01)
        r = c.get("/g3/listings/" + code, headers=auth)
        r.raise_for_status()
        observed = r.json()
        assert observed["complete"] and len(observed["cells"]) == count
        assert {k: v["value"] for k, v in observed["cells"].items()} == {
            cell_key(x): x for x in cells
        }
    elapsed = time.monotonic() - started
    return {
        "channel": channel,
        "cells": count,
        "write_requests": len(bodies),
        "operation_queries": queries,
        "rate_limits": rate_limits,
        "seconds": round(elapsed, 3),
        "peak_driver_and_sim_mib": round(peak / 1024**2, 2),
        "status": "VERIFIED",
        "listing_key": code,
        "payload_bytes": sum(len(json.dumps(b).encode()) for b in bodies),
    }


def main():
    load_dotenv(ROOT / ".env")
    load_dotenv(ROOT / ".runtime/test.env", override=True)
    if not os.environ["DATABASE_URL"].endswith("/hotel_project_g5_test") or not os.environ[
        "SIM_B_DATABASE_URL"
    ].endswith("/hotel_project_sim_b_g5_test"):
        raise SystemExit("Prepare named G3 test stores first")
    env = os.environ.copy()
    env["HOTEL_G1_TEST_MODE"] = "true"
    env["G3_FAULTS_ENABLED"] = "false"
    env["SIM_FAULTS_ENABLED"] = "false"
    processes = []
    logs = []
    results = []
    try:
        for module, port in [
            ("services.channel_simulator.main:create_app", 8003),
            ("services.channel_simulator_b.main:create_app", 8004),
        ]:
            log = (ROOT / f".runtime/g5-protocol-bench-{port}.log").open("w")
            logs.append(log)
            processes.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "-s",
                        "-m",
                        "uvicorn",
                        module,
                        "--factory",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(port),
                    ],
                    env=env,
                    stdout=log,
                    stderr=log,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            )
        for port in [8003, 8004]:
            for _ in range(80):
                try:
                    with httpx.Client(trust_env=False) as c:
                        if c.get(f"http://127.0.0.1:{port}/g3/health").status_code == 200:
                            break
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError("Benchmark simulator not ready")
        for size in [100, 1000, 10000]:
            for channel, port in [("SIM-A", 8003), ("SIM-B", 8004)]:
                r = case(channel, size, port, processes)
                results.append(r)
                print(json.dumps(r), flush=True)
                output = {
                    "platform": platform.platform(),
                    "python": sys.version,
                    "logical_cpu": psutil.cpu_count(),
                    "memory_gib": round(psutil.virtual_memory().total / 1024**3, 2),
                    "scope": "real HTTP wire serialization, remote token bucket, async operation polling and final cell comparison; excludes core quote compiler, allocation, core worker queue and PostgreSQL server RSS",
                    "samples_per_cell": 1,
                    "cache": "not controlled; no p95/SLA claim",
                    "store": "named G3 isolated simulator stores, extra loopback ports8003/8004; no developer data reset",
                    "results": results,
                }
                (ROOT / "docs/acceptance/g5-protocol-benchmark.json").write_text(
                    json.dumps(output, indent=2) + "\n", encoding="utf-8"
                )
        lines = [
            "# G3 真实渠道协议基准",
            "",
            "> 单样本、缓存未控制，不计算p95。范围为序列化/分片、真实HTTP、独立远端配额、异步查询及最终逐格比较；不包含核心报价编译、额度、核心Worker队列或PostgreSQL服务内存。数据为合成唯一自然键，独立G3测试库，额外端口8003/8004。",
            "",
            "| 渠道 | 日期格 | 写请求（含内容） | operation查询 | 秒 | 驱动及两个模拟器峰值MiB | 结果 |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        lines.extend(
            f"| {r['channel']} | {r['cells']} | {r['write_requests']} | {r['operation_queries']} | {r['seconds']} | {r['peak_driver_and_sim_mib']} | {r['status']} |"
            for r in results
        )
        (ROOT / "docs/acceptance/g5-protocol-benchmark.md").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
    finally:
        for p in processes:
            if p.poll() is None:
                p.terminate()
                p.wait(timeout=10)
        for log in logs:
            log.close()


if __name__ == "__main__":
    main()
