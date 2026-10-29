import sys
import time

import httpx

names = {"sim": 8001, "core": 8000}
chosen = names if sys.argv[1] == "all" else {sys.argv[1]: names[sys.argv[1]]}
with httpx.Client(trust_env=False, timeout=2) as client:
    for name, port in chosen.items():
        deadline = time.monotonic() + 15
        while True:
            try:
                response = client.get(f"http://127.0.0.1:{port}/health/ready")
                if response.status_code == 200:
                    print(f"{name} ready on loopback:{port}")
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() >= deadline:
                raise SystemExit(f"{name} not ready; inspect .runtime/{name}.stderr.log")
            time.sleep(0.25)
