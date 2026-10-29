import os
import sys

import psutil

from infra.g4_health import main as base_health
from infra.manage import ROOT


def main():
    base_health()
    marker = ROOT / ".runtime/g5-ops.pid"
    process = psutil.Process(int(marker.read_text().strip()))
    if (
        os.path.normcase(process.exe()) != os.path.normcase(sys.executable)
        or "services.core.g5.worker" not in " ".join(process.cmdline())
        or process.cwd().lower() != str(ROOT).lower()
    ):
        raise RuntimeError("G5_OPERATIONS_PROCESS_MISMATCH")
    print("G5 operations process verified: PID " + str(process.pid))


if __name__ == "__main__":
    main()
