"""Close only sources explicitly listed in interrupted G5 load evidence; retain history."""

import argparse
import json

from infra.g5_http import Scenario
from infra.manage import ROOT


def main(filenames=None, output="g5-initial-load-close.json"):
    codes = set()
    for filename in filenames or ["g5-load-initial.json", "g5-soak-initial.json"]:
        if (
            "/" in filename
            or "\\" in filename
            or not filename.startswith("g5-")
            or not filename.endswith(".json")
        ):
            raise ValueError("INVALID_ACCEPTANCE_EVIDENCE_NAME")
        data = json.loads((ROOT / "docs/acceptance" / filename).read_text(encoding="utf-8"))
        codes.update(b["code"] for b in data.get("bundles", []))
        if data.get("source"):
            codes.add(data["source"])
    s = Scenario(days=3)
    evidence = {
        "source_codes": sorted(codes),
        "paused": [],
        "closed": [],
        "history": "retained; no reset/delete",
    }
    try:
        overview = s.get("/v4/overview")
        for sub in overview["subscriptions"]:
            if sub["source_key"].split("/")[-1] in codes and sub["state"] == "ACTIVE":
                s.post(
                    "/v4/subscriptions/" + sub["id"] + "/commands",
                    {
                        "expected_version": sub["version"],
                        "action": "pause",
                        "reason": "Interrupted G5 experiment retained; explicit safe close",
                    },
                    "tenant_admin",
                )
                evidence["paused"].append(sub["id"])
        from infra.manage import uid
        from services.core.db import Database

        if any(not code.startswith("G5-") for code in codes):
            raise ValueError("ONLY_G5_SYNTHETIC_SOURCE_CODES_ALLOWED")
        db = Database(s.settings.database_url)
        db.pool.open()
        try:
            with db.transaction(uid("tenant-A")) as c:
                targets = c.execute(
                    "SELECT id FROM g3_listing WHERE split_part(source_key,'/',2)=ANY(%s) ORDER BY id",
                    (sorted(codes),),
                ).fetchall()
        finally:
            db.pool.close()
        for target in targets:
            li = s.get("/v3/listings/" + str(target["id"]))
            if li["state"] != "STOPPED":
                s.request_stop(li["id"])
        for target in targets:
            li = s.wait("/v3/listings/" + str(target["id"]), lambda x: x["state"] == "STOPPED")
            evidence["closed"].append(
                {
                    "id": li["id"],
                    "desired": li["desired_revision"],
                    "observed": li["observed_revision"],
                    "state": li["state"],
                }
            )
        if (
            "/" in output
            or "\\" in output
            or not output.startswith("g5-")
            or not output.endswith(".json")
        ):
            raise ValueError("INVALID_OUTPUT_NAME")
        (ROOT / "docs/acceptance" / output).write_text(
            json.dumps(evidence, indent=2) + "\n", encoding="utf-8"
        )
        print(
            "Interrupted experiment sources paused and "
            + str(len(targets))
            + " listings actually STOPPED; all history retained"
        )
    finally:
        s.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--evidence", nargs="+")
    p.add_argument("--output", default="g5-initial-load-close.json")
    a = p.parse_args()
    main(a.evidence, a.output)
