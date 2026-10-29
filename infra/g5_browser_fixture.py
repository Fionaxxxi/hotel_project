"""Prepare and verify fixtures around browser actions; never claim HTTP as UI evidence."""

import argparse
import json
from uuid import uuid4

from infra.g5_http import Scenario
from infra.manage import ROOT

STATE = ROOT / ".runtime/g5-browser-fixture.json"


def main(stage):
    s = Scenario(days=3)
    try:
        if stage == "prepare":
            bundle = s.initialize("G5-UI-" + uuid4().hex[:6])
            regular = s.update(bundle, 2)
            detail = s.get("/v4/runs/" + regular["workflow"]["id"])
            source = next(x for x in s.get("/v1/match-proposals?activation_id=" + detail["activation_id"])["items"] if x["kind"] == "property")
            task = s.post("/v4/suggestions", {"source_id": source["id"]})
            suggestion = s.wait("/v4/suggestions/" + task["id"], lambda x: x["state"] == "COMPLETED")
            processing = s.upload(bundle["code"], 3, "150.00")
            overview = s.wait("/v4/overview", lambda d: any(r["processing_id"] == processing["id"] and r["state"] == "WAIT_APPROVAL" for r in d["runs"]))
            run = next(r for r in overview["runs"] if r["processing_id"] == processing["id"])
            data = {"bundle": bundle, "regular": regular, "suggestion": suggestion, "exception_run": run, "stage": stage, "preparation": "HTTP fixture; approval/rebind/stop UI actions tracked separately"}
        else:
            data = json.loads(STATE.read_text(encoding="utf-8"))
            if stage == "after-exception":
                done = s.wait("/v4/runs/" + data["exception_run"]["id"], lambda r: r["state"] == "COMPLETED")
                policy = next(p for p in s.get("/v4/overview")["policies"] if p["id"] == data["bundle"]["policy"]["id"])
                s.post("/v4/policies/" + policy["id"] + "/commands", {"action": "pause", "expected_version": policy["version"], "reason": "G5 browser controlled policy replacement preparation"}, "tenant_admin")
                new = s.post("/v4/policies", {"baseline_ids": done["snapshot_ids"], "settings": {"auto_activate": True, "auto_publish": True}}, "tenant_admin")
                new = s.post("/v4/policies/" + new["id"] + "/commands", {"action": "submit", "expected_version": new["version"], "reason": "G5 pending independent browser review"}, "tenant_admin")
                data.update(exception_done=done, new_policy=new, stage=stage)
            elif stage == "after-rebind":
                overview = s.get("/v4/overview")
                sub = next(x for x in overview["subscriptions"] if x["id"] == data["bundle"]["subscription"]["id"])
                assert sub["policy_id"] == data["new_policy"]["id"]
                assert sub["version"] > data["bundle"]["subscription"]["version"]
                stale = s.client.post("/v4/subscriptions/" + sub["id"] + "/policy", headers=s.headers("tenant_admin"), json={"policy_id": data["new_policy"]["id"], "expected_version": data["bundle"]["subscription"]["version"], "reason": "G5 stale version must not rebind again"})
                assert stale.status_code == 409
                fresh = next(x for x in s.get("/v4/overview")["subscriptions"] if x["id"] == sub["id"])
                assert fresh["version"] == sub["version"] and fresh["policy_id"] == sub["policy_id"]
                data.update(rebound_subscription=sub, stale_rebind={"status": stale.status_code, "body": stale.json(), "unchanged": True}, updated=s.update(data["bundle"], 4, "155.00"), stage=stage)
            elif stage == "bump-subscription":
                sub = next(x for x in s.get("/v4/overview")["subscriptions"] if x["id"] == data["bundle"]["subscription"]["id"])
                for action in ["pause", "resume"]:
                    sub = s.post("/v4/subscriptions/" + sub["id"] + "/commands", {"action": action, "expected_version": sub["version"], "reason": "G5 controlled concurrent operator version change for browser CAS recovery"}, "tenant_admin")
                data["concurrent_subscription"] = sub
            elif stage == "after-stop":
                listings = [s.wait("/v3/listings/" + b["listing_id"], lambda x: x["state"] == "STOPPED" and x["desired_revision"] == x["observed_revision"]) for b in data["updated"]["plan"]["branches"]]
                assert all(x["state"] == "STOPPED" and x["desired_revision"] == x["observed_revision"] for x in listings)
                data.update(stopped_listings=listings, stage=stage)
        STATE.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"stage": stage, "source": data["bundle"]["code"], "exception_run": data["exception_run"]["id"], "new_policy": data.get("new_policy", {}).get("id")}))
    finally:
        s.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["prepare", "after-exception", "bump-subscription", "after-rebind", "after-stop"])
    main(parser.parse_args().stage)
