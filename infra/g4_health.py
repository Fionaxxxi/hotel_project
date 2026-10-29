import os
import sys

import httpx
import psutil

from infra.g3_health import main as base_health
from infra.manage import ROOT


def main():
    base_health()
    for name in ["g4-flow", "g4-ai"]:
        p = psutil.Process(int((ROOT / ".runtime" / (name + ".pid")).read_text()))
        if (
            os.path.normcase(p.exe()) != os.path.normcase(sys.executable)
            or "services.core.g4.worker" not in " ".join(p.cmdline())
            or p.cwd().lower() != str(ROOT).lower()
        ):
            raise SystemExit("G4 process does not belong to this project")
        print(name + " verified: PID " + str(p.pid))
    with httpx.Client(trust_env=False) as c:
        response = c.get("http://127.0.0.1:8000/health/ready")
        response.raise_for_status()
    print("G4 processes and schema ready")


if __name__ == "__main__":
    main()
