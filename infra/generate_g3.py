"""Synthetic packages and scenario specs, without database/network effects."""

import json

from infra.manage import ROOT
from tests.g2_helpers import files


def main():
    folder = ROOT / "datasets/g3/compatible/source"
    folder.mkdir(parents=True, exist_ok=True)
    for _, (name, data, _) in files(fmt="json", code="G3-DEMO", sequence=1):
        (folder / name).write_bytes(data)
    specs = {
        "compatible": {
            "source": "source/",
            "buffer": 1,
            "amounts": {"SIM-A": 2, "SIM-B": 1},
            "plan_codes": ["NONREF"],
            "expected": "Both channel targets VERIFIED; shared pool is 2+1, never multiplied by plans",
        },
        "capability-blocked": {
            "plan_codes": ["FLEX"],
            "channel": "SIM-B",
            "expected": "422 CANCELLATION_NOT_REPRESENTABLE; no dropped plan or lossy boolean policy",
        },
        "allocations": {
            "start": {"SIM-A": 3, "SIM-B": 0},
            "proposed": {"SIM-A": 0, "SIM-B": 3},
            "expected": "Block increase until remote A decrease verified; retain UNKNOWN exposure",
        },
        "chunking": {
            "cells": [10, 11, 30, 31],
            "expected": "SIM-A max30, SIM-B max10; count pool/day and plan/day independently",
        },
        "async-partial": {
            "faults": ["lost_operation", "partial_temp", "partial_permanent"],
            "expected": "Query original operation; only transient rejected cells retried; successes retained",
        },
        "ordering": {
            "fault": "hold",
            "expected": "Release old HTTP only after new close; actual remote rejects STALE_REVISION",
        },
        "reconciliation": {
            "faults": ["drift", "missing", "read_failure", "incomplete", "remote_ahead"],
            "expected": "Precise diff; read errors UNKNOWN; no blind overwrite of remote ahead",
        },
        "stop-sell": {
            "dates": ["2027-01-10", "2027-01-11", "2027-01-12"],
            "expected": "Separate channel confirmation; no old repair resurrection; fresh independent approval required to reopen",
        },
        "scale": {
            "cells": [100, 1000, 10000],
            "generator": "infra.g3_benchmark",
            "expected": "Real HTTP, unique cell keys, actual request/query counts, sample scope explicit",
        },
    }
    for name, spec in specs.items():
        folder = ROOT / "datasets/g3" / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "scenario.json").write_text(
            json.dumps({"synthetic": True, **spec}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print("G3 synthetic source and nine scenario specifications generated")


if __name__ == "__main__":
    main()
