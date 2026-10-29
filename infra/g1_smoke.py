"""True HTTP end-to-end check; requires core, simulator and dispatcher running."""

import json
import os
import time
from uuid import uuid4

import httpx

from infra.manage import ROOT


def main():
    from services.core.config import Settings
    from tests.g1_helpers import approved_snapshot, auth, import_active, ok

    settings = Settings.load()
    with httpx.Client(base_url="http://127.0.0.1:8000", trust_env=False, timeout=15) as client:
        assert client.get("/admin/").status_code == 200
        # Authenticate using the actual development login endpoint, not a mock token endpoint.
        login = ok(
            client.post(
                "/dev/token",
                json={"subject": "a.operator", "password": os.environ["DEV_LOGIN_PASSWORD"]},
            )
        )
        assert (
            client.get(
                "/v1/me", headers={"Authorization": "Bearer " + login["access_token"]}
            ).status_code
            == 200
        )
        bid, aid = import_active(client, settings, sequence=1)
        snapshot = approved_snapshot(client, settings, aid)
        task = ok(
            client.post(
                "/v1/publish-plans",
                headers={**auth(settings), "Idempotency-Key": str(uuid4())},
                json={"snapshot_id": snapshot["id"]},
            )
        )
        deadline = time.monotonic() + 20
        while True:
            result = ok(client.get("/v1/publish-tasks/" + task["id"], headers=auth(settings)))
            if result["status"] == "VERIFIED":
                break
            if time.monotonic() > deadline:
                raise RuntimeError(f"Dispatcher did not verify task: {result['status']}")
            time.sleep(0.3)
        provenance = ok(
            client.get(
                f"/v1/snapshots/{snapshot['id']}/provenance", headers=auth(settings, "auditor")
            )
        )
        report = {
            "status": "VERIFIED",
            "transport": "real HTTP + separate dispatcher process",
            "batch_id": bid,
            "activation_id": aid,
            "snapshot_id": snapshot["id"],
            "task_id": task["id"],
            "revision": snapshot["revision"],
            "payload_hash": snapshot["payload_hash"],
            "source_record_count": len(provenance["source_records"]),
            "remote_receipts": result["receipts"],
        }
        (ROOT / "docs/acceptance/g1-http-smoke.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            "G1 HTTP smoke VERIFIED: upload, activation, mapping, snapshot, independent approval, dispatcher, simulator, provenance"
        )


if __name__ == "__main__":
    main()
