import os
import sys
import time
from pathlib import Path

import httpx
import psutil

from services.core.config import Settings


def main():
    Settings.load()
    targets = [
        ("SIM-A", "http://127.0.0.1:8001/g3/health"),
        ("SIM-B", "http://127.0.0.1:8002/g3/health"),
    ]
    if len(sys.argv) > 1 and sys.argv[1] == "all":
        targets.append(("core", "http://127.0.0.1:8000/health/ready"))
    for name, url in targets:
        for _ in range(60):
            try:
                with httpx.Client(trust_env=False, timeout=0.5) as c:
                    if c.get(url).status_code == 200:
                        break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:
            raise SystemExit(name + " not ready")
        print(name + " ready on loopback")
    if len(sys.argv) > 1 and sys.argv[1] == "all":
        from infra.manage import ROOT

        expected = {
            "api": "services.core.main:create_app",
            "g3-worker-0": "services.core.g3.worker",
            "g3-worker-1": "services.core.g3.worker",
            "g2-worker-0": "services.core.g2.worker",
            "g2-worker-1": "services.core.g2.worker",
        }
        for name, module in expected.items():
            marker = ROOT / ".runtime" / (name + ".pid")
            if not marker.exists():
                raise SystemExit(name + " PID marker missing")
            process = psutil.Process(int(marker.read_text().strip()))
            if (
                os.path.normcase(process.exe()) != os.path.normcase(sys.executable)
                or module not in " ".join(process.cmdline())
                or Path(process.cwd()).resolve() != ROOT.resolve()
            ):
                raise SystemExit(name + " does not match this project")
            print(name + " verified: PID " + str(process.pid))


if __name__ == "__main__":
    main()
