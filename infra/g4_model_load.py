"""Bounded actual CPU model load for stop-sell isolation verification."""

import json
import os
import sys
import time
from pathlib import Path

from infra.manage import ROOT
from services.core.g4.local_model import LocalModel


def main():
    prefix = Path(sys.argv[1]).resolve()
    if prefix.parent != (ROOT / ".runtime").resolve():
        raise SystemExit("Project runtime evidence path required")
    os.environ["G4_AI_ENABLED"] = "true"
    model = LocalModel()
    count = 0
    start = time.monotonic()
    try:
        while time.monotonic() - start < 20 and not prefix.with_suffix(".stop").exists():
            result = model.rank(
                [
                    "海岚海湾酒店 上海 滨海路",
                    "Hailan Bay Hotel Shanghai Seaside Road",
                    "Airport Annex Hotel Shanghai",
                ]
            )
            count += 1
            if count == 1:
                prefix.with_suffix(".ready").write_text(str(model.process.pid))
        prefix.with_suffix(".json").write_text(
            json.dumps(
                {
                    "actual_inferences": count,
                    "seconds": time.monotonic() - start,
                    "model_revision": result["model_revision"],
                }
            )
        )
    finally:
        model.close()


if __name__ == "__main__":
    main()
