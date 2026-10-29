"""Fresh local HTTP acceptance: real model, automatic update, human exception and stop."""

import json

from infra.g5_http import Scenario
from infra.manage import ROOT
from services.core.g5.audit import verify_package


def main():
    s = Scenario(days=3)
    evidence = {
        "schema": "g5-http-smoke-v1",
        "preparation": "HTTP setup with explicit operator/admin/reviewer roles; not browser actions",
    }
    try:
        bundle = s.initialize()
        regular = s.update(bundle, 2)
        source = next(
            x
            for x in s.get(
                "/v1/match-proposals?activation_id=" + regular["workflow"]["activation_id"]
            )["items"]
            if x["kind"] == "property"
        )
        task = s.post("/v4/suggestions", {"source_id": source["id"]})
        suggestion = s.wait("/v4/suggestions/" + task["id"], lambda r: r["state"] == "COMPLETED")
        assert suggestion['result']['status'] == 'AI_AVAILABLE'
        waiting = s.upload(bundle["code"], 3, "150.00")
        overview = s.wait(
            "/v4/overview",
            lambda d: any(
                r["processing_id"] == waiting["id"] and r["state"] == "WAIT_APPROVAL"
                for r in d["runs"]
            ),
        )
        run = next(r for r in overview["runs"] if r["processing_id"] == waiting["id"])
        evidence["exception_wait"] = s.get("/v4/runs/" + run["id"])
        for sid in run["snapshot_ids"]:
            snap = s.get("/v3/snapshots/" + sid)
            s.post(
                "/v3/snapshots/" + sid + "/decisions",
                {
                    "expected_version": snap["version"],
                    "decision": "APPROVED",
                    "reason": "G5 exact-version human exception review",
                },
                "reviewer",
            )
        done = s.wait("/v4/runs/" + run["id"], lambda r: r["state"] == "COMPLETED")
        plan = s.get("/v3/plans/" + done["plan_id"])
        stop = s.stop(plan)
        lid = plan["branches"][0]["listing_id"]
        export = s.post("/v5/audit/listing/" + lid + "/export", role="auditor")
        assert verify_package(export)
        assert export['manifest']['complete'] is True
        evidence.update(
            status="PASSED",
            bundle=bundle,
            regular=regular,
            suggestion=suggestion,
            exception_done=done,
            stop=stop,
            export_manifest=export["manifest"],
            listing_id=lid,
        )
        (ROOT / "docs/acceptance/g5-http-smoke.json").write_text(
            json.dumps(evidence, indent=2) + "\n", encoding="utf-8"
        )
        print(
            "G5 HTTP: actual suggestion, regular automation, exact human exception, dual STOPPED and verifiable audit export passed"
        )
    finally:
        s.close()


if __name__ == "__main__":
    main()
