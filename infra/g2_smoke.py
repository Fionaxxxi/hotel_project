"""Real HTTP + three independently scheduled worker lanes; appends synthetic demo facts."""

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
    Settings.load()
    reports = []
    with httpx.Client(base_url="http://127.0.0.1:8000", trust_env=False, timeout=20) as client:
        tokens = {}
        for role in ["operator", "reviewer", "auditor"]:
            tokens[role] = ok(
                client.post(
                    "/dev/token",
                    json={"subject": "a." + role, "password": os.environ["DEV_LOGIN_PASSWORD"]},
                )
            )["access_token"]
        headers = {"Authorization": "Bearer " + tokens["operator"]}
        reviewer = {"Authorization": "Bearer " + tokens["reviewer"]}

        class LoggedClient:
            def get(self, path, **kwargs):
                kwargs["headers"] = reviewer
                return client.get(path, **kwargs)

            def post(self, path, **kwargs):
                kwargs["headers"] = reviewer
                return client.post(path, **kwargs)

        from services.core.config import Settings as SettingsClass

        settings = SettingsClass.load()

        def current(rid):
            return ok(client.get("/v2/processing-runs/" + rid, headers=headers))

        def wait(rid, predicate):
            deadline = time.monotonic() + 50
            while time.monotonic() < deadline:
                result = current(rid)
                if predicate(result):
                    return result
                time.sleep(0.25)
            raise RuntimeError(
                "G2 HTTP flow did not progress: "
                + str(result.get("workflow", result.get("status")))
            )

        def execute(fmt, code, seq=1, mode="snapshot", edit=None, only=None):
            batch = ok(
                client.post(
                    "/v2/imports",
                    headers=headers,
                    files=files(fmt=fmt, code=code, sequence=seq, mode=mode, edit=edit, only=only),
                )
            )
            run = ok(client.post(f"/v2/imports/{batch['id']}/runs", headers=headers, json={}))
            r = wait(run["id"], lambda r: r["status"] == "READY_TO_ACTIVATE")
            generation = ok(
                client.post(
                    f"/v2/processing-runs/{run['id']}/activate",
                    headers={**headers, "Idempotency-Key": str(uuid4())},
                    json={
                        "expected_run_version": r["version"],
                        "expected_versions": r["expected_versions"],
                        "selected_dates": ["2027-01-10", "2027-01-11", "2027-01-12"],
                        "partial_confirmed": False,
                    },
                )
            )
            r = wait(
                run["id"],
                lambda r: (
                    r.get("workflow")
                    and r["workflow"]["state"] in ["WAIT_MAPPING", "WAIT_APPROVAL"]
                ),
            )
            if r["workflow"]["state"] == "WAIT_MAPPING":
                approve_mappings(LoggedClient(), settings, generation["activation_id"])
            r = wait(run["id"], lambda r: r["workflow"]["state"] == "WAIT_APPROVAL")
            sid = r["workflow"]["snapshot_id"]
            snap = ok(client.get("/v1/snapshots/" + sid, headers=headers))
            ok(
                client.post(
                    f"/v1/snapshots/{sid}/decisions",
                    headers=reviewer,
                    json={
                        "expected_version": snap["version"],
                        "decision": "APPROVED",
                        "reason": "G2 real HTTP independent approval",
                    },
                )
            )
            r = wait(run["id"], lambda r: r["workflow"]["state"] == "COMPLETED")
            task = ok(client.get("/v1/publish-tasks/" + r["workflow"]["task_id"], headers=headers))
            trace = ok(
                client.get(
                    f"/v1/snapshots/{sid}/provenance",
                    headers={"Authorization": "Bearer " + tokens["auditor"]},
                )
            )
            assert task["status"] == "VERIFIED" and trace["source_records"]
            report = {
                "format": fmt,
                "mode": mode,
                "run_id": run["id"],
                "generation_id": generation["id"],
                "view_hash": generation["view_hash"],
                "workflow_id": r["workflow"]["id"],
                "snapshot_id": sid,
                "task_id": task["id"],
                "payload_hash": snap["payload_hash"],
                "source_record_count": len(trace["source_records"]),
                "receipts": task["receipts"],
                "status": "COMPLETED/VERIFIED",
            }
            reports.append(report)
            print(fmt, mode, "COMPLETED/VERIFIED", flush=True)
            return report

        code = "HTTP-G2-" + uuid4().hex[:8]
        execute("csv", code)
        execute("xlsx", "HTTP-XLSX-" + uuid4().hex[:8])
        execute("json", "HTTP-JSON-" + uuid4().hex[:8])

        def edit(data, m):
            data["rates"] = [
                r
                for r in data["rates"]
                if r["plan_code"] == "FLEX" and r["stay_date"] == "2027-01-11"
            ]
            data["rates"][0]["net_amount"] = "115.00"

        execute("json", code, 2, "delta", edit, ["rates"])
    (ROOT / "docs/acceptance/g2-http-smoke.json").write_text(
        json.dumps(
            {
                "transport": "real HTTP + independent parse/control/publish worker processes + separate SIM-A",
                "human_decisions": "real dev login operator/reviewer; no workflow next-step or task creation call",
                "results": reports,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
