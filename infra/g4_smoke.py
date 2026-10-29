"""Actual login, API, G4 orchestration and independent G3/channel processes."""

import json
import os
import subprocess
import sys
import time
from uuid import uuid4

import httpx

from infra.manage import ROOT
from services.core.config import Settings
from tests.g1_helpers import approve_mappings, ok
from tests.g2_helpers import files


def main():
    settings = Settings.load()
    with httpx.Client(base_url="http://127.0.0.1:8000", trust_env=False, timeout=20) as c:
        tokens = {
            role: ok(
                c.post(
                    "/dev/token",
                    json={"subject": "a." + role, "password": os.environ["DEV_LOGIN_PASSWORD"]},
                )
            )["access_token"]
            for role in ["operator", "reviewer", "tenant_admin"]
        }

        def headers(role="operator", key=None):
            return {
                "Authorization": "Bearer " + tokens[role],
                **({"Idempotency-Key": key} if key else {}),
            }

        def post(path, body=None, role="operator", key=None):
            return ok(c.post(path, headers=headers(role, key), json=body or {}))

        def read(path):
            return ok(c.get(path, headers=headers()))

        def wait(path, predicate, seconds=90):
            start = time.monotonic()
            while time.monotonic() - start < seconds:
                data = read(path)
                if predicate(data):
                    return data
                time.sleep(0.25)
            raise RuntimeError(
                "G4 HTTP convergence timeout: "
                + path
                + " "
                + str(data.get("state", data.get("status")))
            )

        def upload(code, sequence, price):
            def edit(data, manifest):
                for row in data["rates"]:
                    row["net_amount"] = price

            package = ok(
                c.post(
                    "/v2/imports",
                    headers=headers(),
                    files=files(fmt="json", code=code, sequence=sequence, edit=edit),
                )
            )
            return post("/v2/imports/" + package["id"] + "/runs")

        def autorun(run, status):
            found = wait(
                "/v4/overview",
                lambda d: any(
                    r["processing_id"] == run["id"] and r["state"] == status for r in d["runs"]
                ),
            )
            return next(r for r in found["runs"] if r["processing_id"] == run["id"])

        def finish(run):
            return autorun(run, "COMPLETED")

        def close(plan):
            for branch in plan["branches"]:
                li = read("/v3/listings/" + branch["listing_id"])
                post(
                    "/v3/listings/" + li["id"] + "/stop",
                    {
                        "expected_version": li["version"],
                        "dates": sorted({x["date"] for x in li["intent"]["payload"]["cells"]}),
                        "reason": "G4 actual HTTP final emergency stop",
                    },
                    "tenant_admin",
                    str(uuid4()),
                )
            return [
                wait("/v3/listings/" + b["listing_id"], lambda li: li["state"] == "STOPPED")
                for b in plan["branches"]
            ]

        if os.environ.get("G4_BROWSER_CONTINUE") == "true":
            prepared = json.loads((ROOT / ".runtime/g4-browser-prepared.json").read_text())
            run = upload(prepared["code"], 2, "105.00")
            result = finish(run)
            evidence = {
                "status": "BROWSER_AUTHORIZED_AUTOMATIC_DUAL_VERIFIED",
                "run": read("/v4/runs/" + result["id"]),
                "plan": read("/v3/plans/" + result["plan_id"]),
            }
            (ROOT / "docs/acceptance/g4-browser-server.json").write_text(
                json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(
                "Browser-approved policy: actual automatic update completed and both branches VERIFIED"
            )
            return
        code = "G4-HTTP-" + uuid4().hex[:8]
        first = upload(code, 1, "100.00")
        first = wait(
            "/v2/processing-runs/" + first["id"], lambda r: r["status"] == "READY_TO_ACTIVATE"
        )
        gen = post(
            "/v2/processing-runs/" + first["id"] + "/activate",
            {
                "expected_run_version": first["version"],
                "expected_versions": first["expected_versions"],
                "selected_dates": ["2027-01-10", "2027-01-11", "2027-01-12"],
                "partial_confirmed": False,
            },
            key=str(uuid4()),
        )
        proposals = read("/v1/match-proposals?activation_id=" + gen["activation_id"])["items"]
        source = next(e for e in proposals if e["kind"] == "property")
        suggestion = post("/v4/suggestions", {"source_id": source["id"]})
        suggestion = wait(
            "/v4/suggestions/" + suggestion["id"], lambda s: s["state"] == "COMPLETED"
        )
        expected_ai = (
            "RULE_FALLBACK"
            if os.environ.get("G4_EXPECT_MODEL_DISABLED") == "true"
            else "AI_AVAILABLE"
        )
        assert suggestion["result"]["status"] == expected_ai, suggestion["result"]

        class Reviewer:
            def get(self, path, **kwargs):
                return c.get(path, headers=headers("reviewer"), params=kwargs.get("params"))

            def post(self, path, **kwargs):
                return c.post(path, headers=headers("reviewer"), json=kwargs["json"])

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
        baselines = []
        for channel in ["SIM-A", "SIM-B"]:
            s = post(
                "/v3/snapshots",
                {"allocation_id": allocation["id"], "channel": channel, "plan_codes": ["NONREF"]},
            )
            baselines.append(
                post(
                    "/v3/snapshots/" + s["id"] + "/decisions",
                    {
                        "expected_version": s["version"],
                        "decision": "APPROVED",
                        "reason": "G4 independent human baseline review",
                    },
                    "reviewer",
                )
            )
        policy = post(
            "/v4/policies",
            {
                "baseline_ids": [s["id"] for s in baselines],
                "settings": {"auto_activate": True, "auto_publish": True},
            },
            "tenant_admin",
        )
        policy = post(
            "/v4/policies/" + policy["id"] + "/commands",
            {
                "action": "submit",
                "expected_version": policy["version"],
                "reason": "Explicit G4 synthetic source policy",
            },
            "tenant_admin",
        )
        if os.environ.get("G4_BROWSER_PREPARE") == "true":
            (ROOT / ".runtime/g4-browser-prepared.json").write_text(
                json.dumps(
                    {
                        "code": code,
                        "policy": policy,
                        "suggestion_id": suggestion["id"],
                        "baselines": baselines,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            print(
                "G4 browser fixture: actual suggestion and submitted policy awaiting independent review"
            )
            return
        for action, role in [("approve", "reviewer"), ("enable", "tenant_admin")]:
            policy = post(
                "/v4/policies/" + policy["id"] + "/commands",
                {
                    "action": action,
                    "expected_version": policy["version"],
                    "reason": "Independent G4 bounded automation grant",
                },
                role,
            )
        subscription = post("/v4/subscriptions", {"policy_id": policy["id"]}, "tenant_admin")
        start = time.monotonic()
        regular = finish(upload(code, 2, "105.00"))
        normal_seconds = time.monotonic() - start
        policy = post(
            "/v4/policies/" + policy["id"] + "/commands",
            {
                "action": "pause",
                "expected_version": policy["version"],
                "reason": "Demonstrate effective pause before next update",
            },
            "tenant_admin",
        )
        paused_update = upload(code, 3, "106.00")
        paused = autorun(paused_update, "WAIT_SCOPE")
        assert not paused["activation_id"]
        policy = post(
            "/v4/policies/" + policy["id"] + "/commands",
            {
                "action": "enable",
                "expected_version": policy["version"],
                "reason": "Resume original independent grant and re-evaluate current input",
            },
            "tenant_admin",
        )
        resumed = finish(paused_update)
        exception_update = upload(code, 4, "150.00")
        exception = autorun(exception_update, "WAIT_APPROVAL")
        waiting = read("/v4/runs/" + exception["id"])
        assert all(d["result"] == "REVIEW" for d in waiting["decisions"])
        for sid in exception["snapshot_ids"]:
            s = read("/v3/snapshots/" + sid)
            post(
                "/v3/snapshots/" + sid + "/decisions",
                {
                    "expected_version": s["version"],
                    "decision": "APPROVED",
                    "reason": "Independent exact out-of-policy snapshot review",
                },
                "reviewer",
            )
        exception_done = finish(exception_update)
        plan = read("/v3/plans/" + exception_done["plan_id"])
        busy_evidence = None
        if expected_ai == "AI_AVAILABLE":
            prefix = ROOT / ".runtime" / ("g4-model-load-" + uuid4().hex)
            busy = subprocess.Popen(
                [sys.executable, "-s", "-m", "infra.g4_model_load", str(prefix)],
                cwd=ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            try:
                deadline = time.monotonic() + 15
                while not prefix.with_suffix(".ready").exists() and time.monotonic() < deadline:
                    if busy.poll() is not None:
                        raise RuntimeError("Actual model load failed")
                    time.sleep(0.05)
                if not prefix.with_suffix(".ready").exists():
                    raise RuntimeError("Actual model load not ready")
                close_start = time.monotonic()
                stopped = close(plan)
                close_seconds = time.monotonic() - close_start
                prefix.with_suffix(".stop").write_text("stop")
                busy.wait(timeout=25)
                busy_evidence = json.loads(prefix.with_suffix(".json").read_text())
                busy_evidence["stop_to_both_confirmed_seconds"] = round(close_seconds, 3)
                assert busy_evidence["actual_inferences"] > 1
            finally:
                if busy.poll() is None:
                    busy.kill()
                    busy.wait(timeout=5)
        else:
            stopped = close(plan)
        evidence = {
            "status": "PASSED",
            "code": code,
            "model_status": expected_ai,
            "actual_model_load_during_stop": busy_evidence,
            "suggestion": suggestion,
            "policy": policy,
            "subscription_id": subscription["id"],
            "regular_update": read("/v4/runs/" + regular["id"]),
            "normal_receive_to_dual_verified_seconds": round(normal_seconds, 3),
            "paused_wait": paused,
            "resume_after_pause": read("/v4/runs/" + resumed["id"]),
            "exception_wait": waiting,
            "exception_completed": read("/v4/runs/" + exception_done["id"]),
            "final_plan": plan,
            "close_confirmations": [
                {
                    "id": li["id"],
                    "state": li["state"],
                    "desired_revision": li["desired_revision"],
                    "observed_revision": li["observed_revision"],
                }
                for li in stopped
            ],
        }
        suffix = "-disabled" if expected_ai == "RULE_FALLBACK" else ""
        (ROOT / f"docs/acceptance/g4-http-smoke{suffix}.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            "G4 real HTTP passed: actual suggestion, automatic dual update, pause/resume, exception human review, dual STOPPED"
        )


if __name__ == "__main__":
    main()
