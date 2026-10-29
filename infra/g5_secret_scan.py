"""Scan deliverable sources, evidence and project logs without echoing secrets."""

import json
import os
from datetime import UTC, datetime

from dotenv import load_dotenv

from infra.g5_release import source_files
from infra.manage import ROOT


def main():
    load_dotenv(ROOT / ".env")
    secrets = {v.encode() for k, v in os.environ.items() if len(v) >= 12 and any(x in k for x in ("PASSWORD", "TOKEN", "SECRET", "DATABASE_URL", "MIGRATION_URL"))}
    files = {p for p, _ in source_files()}
    files.update((ROOT / ".runtime/logs").glob("*.jsonl*"))
    files.update((ROOT / ".runtime").glob("*.stderr.log"))
    matches = []
    count = 0
    for path in sorted(files):
        if not path.is_file():
            continue
        data = path.read_bytes()
        count += 1
        if any(s in data for s in secrets):
            matches.append(str(path.relative_to(ROOT)))
    report = {"at": datetime.now(UTC).isoformat(), "status": "FAILED" if matches else "PASSED", "files": count, "matches": matches, "scope": "Exact current configured secret values of at least 12 characters, deliverable sources/evidence and local process logs; no credential values retained. Not a certification of unknown historic secrets."}
    (ROOT / "docs/acceptance/g5-secret-scan.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))
    if matches:
        raise RuntimeError("SECRET_SCAN_FAILED")


if __name__ == "__main__":
    main()
