"""Replay 100 seeded shuffled, previously applied requests after a verified stop."""

import json
import random
import time
from datetime import UTC, datetime
from uuid import UUID

from infra.g5_http import Scenario
from infra.manage import ROOT, uid
from services.core.db import Database
from services.core.g3 import transport


def main():
    s = Scenario(days=3)
    db = Database(s.settings.database_url)
    db.pool.open()
    evidence = {"at": datetime.now(UTC).isoformat(), "seed": 20261028, "requests": 0, "attempts": [], "status": "IN_PROGRESS"}
    try:
        bundle = s.initialize()
        update = s.update(bundle, 2)
        evidence["stop"] = s.stop(update["plan"])
        lids = [UUID(x["listing_id"]) for x in update["plan"]["branches"]]
        with db.transaction(uid("tenant-A")) as c:
            tasks = c.execute("SELECT t.body,t.request_key,a.code,li.listing_key FROM g3_task t JOIN g3_intent i ON i.id=t.intent_id JOIN g3_listing li ON li.id=i.listing_id JOIN g3_account a ON a.id=li.account_id WHERE i.listing_id=ANY(%s) AND t.state='APPLIED' ORDER BY i.desired_revision,t.chunk_index", (lids,)).fetchall()
        assert tasks and {x["body"]["action"] for x in tasks} >= {"PUBLISH", "STOP"}
        keys = {(x["code"], x["listing_key"]) for x in tasks}

        def observed():
            result = {}
            for channel, key in sorted(keys):
                response = transport.request(uid("tenant-A"), channel, "GET", transport.listing_path(key))
                response.raise_for_status()
                result[channel + ":" + key] = response.json()
            return result

        before = observed()
        rng = random.Random(evidence["seed"])
        order = [rng.choice(tasks) for _ in range(100)]
        rng.shuffle(order)
        for task in order:
            response = transport.request(uid("tenant-A"), task["code"], "PUT", transport.listing_path(task["listing_key"]), task["body"], task["request_key"])
            evidence["requests"] += 1
            evidence["attempts"].append({"request_key": task["request_key"], "action": task["body"]["action"], "channel": task["code"], "status": response.status_code})
            assert response.status_code in {200, 202}, "Previously applied idempotent request was not recognized"
            time.sleep(0.4)
        after = observed()
        evidence.update(status="PASSED" if before == after else "FAILED", before=before, after=after, distinct_original_requests=len(tasks), scope="Real HTTP duplicate requests with original keys, shuffled after explicit stop; original replies may describe historical publish success while current remote must remain closed.")
        assert before == after
    finally:
        db.pool.close()
        s.close()
        (ROOT / "docs/acceptance/g5-duplicate-delivery.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": evidence["status"], "requests": evidence["requests"]}))


if __name__ == "__main__":
    main()
