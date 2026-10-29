"""Find an actually recorded correlation across API, asynchronous jobs and simulators."""

import json
from datetime import UTC, datetime

from infra.manage import ROOT, uid
from services.core.config import Settings
from services.core.db import Database


def main():
    Settings.load()
    events = {}
    for path in (ROOT / ".runtime/logs").glob("*.jsonl*"):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            trace = event.get("trace_id") or event.get("correlation_id")
            if trace:
                events.setdefault(trace, []).append({k: event[k] for k in ("service", "event", "method", "status", "job_id", "cause_event_id", "timestamp") if k in event})
    db = Database(Settings.load().database_url)
    db.pool.open()
    found = None
    try:
        with db.transaction(uid("tenant-A")) as c:
            rows = c.execute("SELECT trace_id,array_agg(DISTINCT kind) AS kinds,array_agg(DISTINCT cause_event_id) FILTER(WHERE cause_event_id IS NOT NULL) AS causes FROM execution_job WHERE trace_id IS NOT NULL GROUP BY trace_id HAVING bool_or(kind='PARSE') AND bool_or(kind='G3_DELIVER')").fetchall()
        for row in rows:
            recorded = events.get(str(row["trace_id"]), [])
            services = {x.get("service") for x in recorded}
            if "api" in services and any("sim" in str(x).lower() for x in services) and row["causes"]:
                found = {"trace_id": str(row["trace_id"]), "job_kinds": row["kinds"], "cause_event_ids": [str(x) for x in row["causes"]], "recorded_events": recorded, "services": sorted(str(x) for x in services)}
                break
    finally:
        db.pool.close()
    report = {"at": datetime.now(UTC).isoformat(), "status": "PASSED" if found else "FAILED", "chain": found, "scope": "Historical immutable job/event references plus actual retained JSON service logs; no synthetic logging injected"}
    (ROOT / "docs/acceptance/g5-trace.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "chain_found": bool(found)}))
    if not found:
        raise RuntimeError("CROSS_PROCESS_TRACE_NOT_FOUND")


if __name__ == "__main__":
    main()
