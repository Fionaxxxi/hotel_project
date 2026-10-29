"""Age probe only; it never claims to have revalidated every backup byte."""

import json
from datetime import UTC, datetime
from pathlib import Path


def status(base=None, clock=None):
    base = base or Path(__file__).resolve().parents[3] / ".runtime/g5-backups"
    clock = clock or datetime.now(UTC)
    latest = None
    for path in sorted(base.glob("*/manifest.json"), reverse=True)[:100]:
        try:
            if path.stat().st_size > 2 * 1024 * 1024:
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            stamp = datetime.fromisoformat(data["completed_at"])
            if (
                data.get("status") != "COMPLETE"
                or data.get("schema") != "g5-backup-v1"
                or stamp.tzinfo is None
                or stamp > clock
            ):
                continue
            if latest is None or stamp > latest:
                latest = stamp
        except (OSError, ValueError, KeyError, TypeError):
            continue
    age = (clock - latest).total_seconds() if latest else None
    return {
        "state": "FRESH" if age is not None and age <= 86400 else "STALE",
        "latest_completed_at": latest.isoformat() if latest else None,
        "age_seconds": age,
        "threshold_seconds": 86400,
        "integrity": "age probe only; use verify-backup for byte checks",
    }
