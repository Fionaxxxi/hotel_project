"""Real login + separate API, worker and channel processes. Appends synthetic demo data."""

import json
import os
import time
from uuid import uuid4

import httpx

from infra.manage import ROOT
from services.core.config import Settings
from tests.g1_helpers import approve_mappings, ok
from tests.g2_helpers import files


def main():
    settings = Settings.load()
    result = {}
    with httpx.Client(base_url="http://127.0.0.1:8000", trust_env=False, timeout=15) as c:
        tokens = {
            role: ok(
                c.post(
                    "/dev/token",
                    json={"subject": "a." + role, "password": os.environ["DEV_LOGIN_PASSWORD"]},
                )
            )["access_token"]
            for role in ["operator", "reviewer", "tenant_admin"]
        }

        def auth(role="operator"):
            return {"Authorization": "Bearer " + tokens[role]}

        def post(path, body=None, role="operator", key=None):
            return ok(
                c.post(
                    path,
                    headers={**auth(role), **({"Idempotency-Key": key} if key else {})},
                    json=body or {},
                )
            )

        def read(path):
            return ok(c.get(path, headers=auth()))

        def wait(path, predicate):
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                value = read(path)
                if predicate(value):
                    return value
                time.sleep(0.25)
            raise RuntimeError("HTTP smoke did not converge: " + path)

        package = ok(
            c.post(
                "/v2/imports",
                headers=auth(),
                files=files(fmt="json", code="G3-HTTP-" + uuid4().hex[:8]),
            )
        )
        run = post("/v2/imports/" + package["id"] + "/runs")
        current = wait(
            "/v2/processing-runs/" + run["id"], lambda r: r["status"] == "READY_TO_ACTIVATE"
        )
        gen = post(
            "/v2/processing-runs/" + run["id"] + "/activate",
            {
                "expected_run_version": current["version"],
                "expected_versions": current["expected_versions"],
                "selected_dates": ["2027-01-10", "2027-01-11", "2027-01-12"],
                "partial_confirmed": False,
            },
            key=str(uuid4()),
        )

        class Reviewer:
            def get(self, path, **kwargs):
                return c.get(path, headers=auth("reviewer"), params=kwargs.get("params"))

            def post(self, path, **kwargs):
                return c.post(path, headers=auth("reviewer"), json=kwargs["json"])

        approve_mappings(Reviewer(), settings, gen["activation_id"])
        assert post("/v3/sources/" + gen["activation_id"] + "/adopt")["legacy_state"] == "CLEAR"
        allocation = post(
            "/v3/allocations",
            {
                "activation_id": gen["activation_id"],
                "expected_version": 0,
                "buffer": 1,
                "amounts": {"SIM-A": 2, "SIM-B": 1},
            },
        )
        snapshots = []
        for channel in ["SIM-A", "SIM-B"]:
            s = post(
                "/v3/snapshots",
                {"allocation_id": allocation["id"], "channel": channel, "plan_codes": ["NONREF"]},
            )
            if os.environ.get("G3_BROWSER_PREPARE") == "true":
                snapshots.append(s)
                continue
            snapshots.append(
                post(
                    "/v3/snapshots/" + s["id"] + "/decisions",
                    {
                        "expected_version": s["version"],
                        "decision": "APPROVED",
                        "reason": "G3 actual HTTP independent review",
                        "allow_repair": True,
                    },
                    "reviewer",
                )
            )
        if os.environ.get("G3_BROWSER_PREPARE") == "true":
            (ROOT / ".runtime/g3-browser-prepared.json").write_text(
                json.dumps(
                    {
                        "activation_id": gen["activation_id"],
                        "allocation_id": allocation["id"],
                        "snapshots": snapshots,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            print("Browser fixture ready: two snapshots awaiting independent review")
            return
        started = time.monotonic()
        plan = post(
            "/v3/plans",
            {
                "snapshot_ids": [s["id"] for s in snapshots],
                "expected_revisions": {s["account_id"]: 0 for s in snapshots},
            },
            key=str(uuid4()),
        )
        plan = wait("/v3/plans/" + plan["id"], lambda p: p["state"] == "VERIFIED")
        publish_seconds = time.monotonic() - started
        closes = []
        started = time.monotonic()
        for branch in plan["branches"]:
            li = read("/v3/listings/" + branch["listing_id"])
            post(
                "/v3/listings/" + li["id"] + "/stop",
                {
                    "expected_version": li["version"],
                    "dates": ["2027-01-10", "2027-01-11", "2027-01-12"],
                    "reason": "G3 real HTTP emergency close proof",
                },
                "tenant_admin",
                str(uuid4()),
            )
        for branch in plan["branches"]:
            li = wait(
                "/v3/listings/" + branch["listing_id"], lambda item: item["state"] == "STOPPED"
            )
            closes.append(
                {
                    "listing_id": li["id"],
                    "state": li["state"],
                    "desired_revision": li["desired_revision"],
                    "observed_revision": li["observed_revision"],
                }
            )
        trace = read("/v3/snapshots/" + snapshots[0]["id"] + "/provenance")
        result = {
            "transport": "real login + independent API + two G3 workers + separate SIM-A / SIM-B stores",
            "run_id": run["id"],
            "activation_id": gen["activation_id"],
            "allocation_id": allocation["id"],
            "plan": plan,
            "close_confirmations": closes,
            "publish_to_verified_seconds": round(publish_seconds, 3),
            "stop_to_both_confirmed_seconds": round(time.monotonic() - started, 3),
            "source_record_count": len(trace["source"]["source_records"]),
            "status": "DUAL_VERIFIED_AND_STOPPED",
        }
    (ROOT / "docs/acceptance/g3-http-smoke.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        "G3 real HTTP: both channels VERIFIED, emergency close both STOPPED; complete provenance captured"
    )


if __name__ == "__main__":
    main()
