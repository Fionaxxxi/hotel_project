"""Five bounded sources: actual human publication, automatic update, then verified stop."""

import json
from uuid import uuid4

from infra.g5_http import Scenario
from infra.manage import ROOT


def main():
    s = Scenario(days=3)
    evidence = {"status": "IN_PROGRESS", "sources": [], "snapshot_ids": []}
    try:
        for index in range(5):
            bundle = s.initialize()
            plan = s.post("/v3/plans", {"snapshot_ids": [x["id"] for x in bundle["baselines"]], "expected_revisions": {x["account_id"]: 0 for x in bundle["baselines"]}, "reopen": False}, key=str(uuid4()))
            human = s.wait("/v3/plans/" + plan["id"], lambda x: x["state"] == "VERIFIED")
            automatic = s.update(bundle, 2)
            stopped = s.stop(automatic["plan"])
            evidence["sources"].append({"code": bundle["code"], "human_plan": human, "automatic": automatic, "stop": stopped})
            evidence["snapshot_ids"].extend(x["id"] for x in bundle["baselines"])
            evidence["snapshot_ids"].extend(automatic["workflow"]["snapshot_ids"])
            (ROOT / "docs/acceptance/g5-audit-fixtures.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
            print("Actual bounded human/policy dual-channel audit source " + str(index + 1), flush=True)
        assert len(set(evidence["snapshot_ids"])) == 20
        evidence["status"] = "PASSED"
        (ROOT / "docs/acceptance/g5-audit-fixtures.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    finally:
        s.close()


if __name__ == "__main__":
    main()
