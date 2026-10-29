import os
import subprocess
import sys
from uuid import UUID, uuid4

import pytest

from infra.manage import ROOT, uid
from services.core.g1 import matching
from services.core.g1.common import get
from services.core.g4 import ai
from services.core.g4.local_model import LocalModel
from tests.g1_helpers import auth, ok
from tests.g3_helpers import drain
from tests.test_g4 import configured, tick, update


@pytest.mark.parametrize("window,exitcode", [("before", 91), ("after", 92)])
def test_real_process_exit_around_automatic_plan_transaction(client, settings, window, exitcode):
    bundle, p, sub = configured(client, settings)
    run = update(client, settings, bundle)
    rid = uuid4()
    with client.app.state.db.transaction(uid("tenant-A")) as c:
        c.execute(
            "INSERT INTO g4_run(id,tenant_id,subscription_id,processing_id) VALUES(%s,%s,%s,%s)",
            (rid, uid("tenant-A"), UUID(sub["id"]), UUID(run["id"])),
        )
    completed = subprocess.run(
        [sys.executable, "-s", "-m", "infra.g4_fault_child", window, str(rid)],
        cwd=ROOT,
        env=os.environ.copy(),
        timeout=40,
        capture_output=True,
    )
    assert completed.returncode == exitcode, completed.stderr.decode(errors="replace")
    with client.app.state.db.transaction(uid("tenant-A")) as c:
        current = get(c, "g4_run", rid)
        assert bool(current["plan_id"]) == (window == "after")
    resumed = tick(client, sub)
    assert resumed["plan_id"]
    again = tick(client, sub)
    assert again["plan_id"] == resumed["plan_id"]


def test_model_disabled_real_manual_mapping_and_publication(client, settings, monkeypatch):
    bundle, p, sub = configured(client, settings)

    def edit(data, manifest):
        data["properties"][0]["name"] = "海岚滨海酒店 新别名"

    update(client, settings, bundle, edit)
    r = tick(client, sub)
    assert r["state"] == "WAIT_MAPPING"
    with client.app.state.db.transaction(uid("tenant-A")) as c:
        source = c.execute(
            "SELECT * FROM supplier_entity_version WHERE activation_id=%s AND kind='property'",
            (r["activation_id"],),
        ).fetchone()
        task = ai.create(
            c,
            {"tenant_id": uid("tenant-A"), "subject": "a.operator", "role": "operator"},
            source["id"],
        )
    monkeypatch.setenv("G4_AI_ENABLED", "false")
    model = LocalModel()
    try:
        ai.tick(client.app.state.db, uid("tenant-A"), "offline-test", model, [task["id"]])
    finally:
        model.close()
    result = ok(client.get("/v4/suggestions/" + str(task["id"]), headers=auth(settings)))
    assert result["result"]["status"] == "RULE_FALLBACK"
    from tests.g1_helpers import approve_mappings

    approve_mappings(client, settings, r["activation_id"])
    resumed = tick(client, sub)
    assert resumed["plan_id"], resumed
    plan = ok(client.get("/v3/plans/" + resumed["plan_id"], headers=auth(settings)))
    client.app.state.db.g3_assigned_listings = [UUID(b["listing_id"]) for b in plan["branches"]]
    drain(
        client,
        settings,
        lambda: (
            ok(client.get("/v3/plans/" + plan["id"], headers=auth(settings)))["state"] == "VERIFIED"
        ),
    )


def test_late_model_result_cannot_change_current_source(client, settings):
    bundle, p, sub = configured(client, settings)
    with client.app.state.db.transaction(uid("tenant-A")) as c:
        source = c.execute(
            "SELECT id FROM supplier_entity_version WHERE activation_id=%s AND kind='property'",
            (bundle["gen"]["activation_id"],),
        ).fetchone()["id"]
        task = ai.create(
            c, {"tenant_id": uid("tenant-A"), "subject": "a.operator", "role": "operator"}, source
        )

    class LateModel:
        def rank(self, texts):
            update(client, settings, bundle)
            tick(client, sub)
            return {"scores": [0.8] * (len(texts) - 1)}

    ai.tick(client.app.state.db, uid("tenant-A"), "late-model", LateModel(), [task["id"]])
    item = ok(client.get("/v4/suggestions/" + str(task["id"]), headers=auth(settings)))
    assert item["state"] == "STALE"


def test_hard_conflict_required_pairs_sixty(monkeypatch):
    monkeypatch.setattr(
        matching, "parent_target", lambda c, s: "hotel-1" if s["kind"] == "room" else "room-1"
    )
    checked = 0
    for i in range(20):
        assert "LOCATION_CONFLICT" in matching.conflicts(
            None,
            {"kind": "property", "data": {"country_code": "CN", "city": f"City-{i}"}},
            {"country_code": "CN", "city": f"Other-{i}"},
        )
        assert "BED_CONFLICT" in matching.conflicts(
            None,
            {"kind": "room", "data": {"bed_type": "KING", "max_adults": "2"}},
            {"property_id": "hotel-1", "bed_type": "TWIN", "max_adults": 2},
        )
        assert "TAX_BASIS_CONFLICT" in matching.conflicts(
            None,
            {
                "kind": "rate",
                "data": {
                    "meal": "NONE",
                    "payment": "PREPAY",
                    "tax_basis": "UNKNOWN",
                    "cancellation": "{}",
                },
            },
            {
                "plan_id": None,
                "meal": "NONE",
                "payment": "PREPAY",
                "tax_basis": "INCLUDED",
                "cancellation": {},
            },
        )
        checked += 3
    assert checked == 60
