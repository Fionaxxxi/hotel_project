"""Recovered sources stay fenced even after the instance allows new independent work."""

import json
from pathlib import Path

from services.core.g1.common import audit, authorize, check_version, fail, get


def permit(database):
    if not database.startswith("hotel_project_g5_restore_"):
        return True
    root = Path(__file__).resolve().parents[3]
    path = root / ".runtime/g5-restore-permits" / (database + ".json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return (
            data.get("database") == database and data.get("mode") == "QUARANTINED_NEW_SOURCES_ONLY"
        )
    except (OSError, ValueError):
        return False


def guard(c, key):
    if c.execute(
        "SELECT 1 FROM g5_recovery_scope WHERE source_key=%s AND state='HELD'", (key,)
    ).fetchone():
        fail("RECOVERY_SCOPE_HELD")


def stop_restored(db, actor, lid, version, reason):
    from uuid import uuid4

    from services.core.g3 import domain, transport
    from services.core.g3.contract import cell_key

    authorize(actor, {"tenant_admin"})
    with db.transaction(actor["tenant_id"]) as c:
        li = get(c, "g3_listing", lid)
        account = get(c, "g3_account", li["account_id"])
        intent = get(c, "g3_intent", li["intent_id"])
        if not c.execute(
            "SELECT 1 FROM g5_recovery_scope WHERE source_key=%s AND state='HELD'",
            (li["source_key"],),
        ).fetchone():
            fail("RECOVERY_SCOPE_NOT_HELD")
    response = transport.request(
        actor["tenant_id"], account["code"], "GET", transport.listing_path(li["listing_key"])
    )
    if response.status_code != 200 or response.json().get("complete") is not True:
        fail("RECOVERY_REMOTE_UNKNOWN")
    remote = response.json()
    if set(remote.get("cells", {})) != {cell_key(x) for x in intent["payload"]["cells"]}:
        fail("RECOVERY_REMOTE_SCOPE_CHANGED")
    with db.transaction(actor["tenant_id"]) as c:
        domain.lock_source(c, actor["tenant_id"], li["source_key"])
        current = get(c, "g3_listing", lid, True)
        check_version(current, version)
        floor = max(current["delivery_revision"], remote["revision"])
        c.execute(
            "UPDATE g3_listing SET delivery_revision=%s,desired_revision=greatest(desired_revision,%s),version=version+1 WHERE id=%s",
            (floor, floor, lid),
        )
        audit(
            c,
            actor,
            "g5.recovery.remote-counter-floor",
            lid,
            f"Observed remote revision {remote['revision']}; prior delivery {current['delivery_revision']}; explicit STOP only: "
            + reason,
        )
        updated = get(c, "g3_listing", lid)
        return domain.stop(
            c,
            actor,
            lid,
            str(uuid4()),
            updated["version"],
            sorted({x["date"] for x in intent["payload"]["cells"]}),
            reason,
        )


def release_scope(db, actor, key, reason):
    from services.core.g3 import domain, transport
    from services.core.g3.contract import cell_key, normalize_content

    authorize(actor, {"tenant_admin"})
    with db.transaction(actor["tenant_id"]) as c:
        scope = c.execute(
            "SELECT * FROM g5_recovery_scope WHERE source_key=%s AND state='HELD'", (key,)
        ).fetchone()
        if not scope:
            fail("RECOVERY_SCOPE_NOT_HELD")
        rows = c.execute(
            "SELECT * FROM g3_listing WHERE source_key=%s ORDER BY id", (key,)
        ).fetchall()
        if not rows:
            fail("RECOVERY_NO_REMOTE_CLOSE_PROOF")
        checks = []
        for li in rows:
            intent = get(c, "g3_intent", li["intent_id"])
            account = get(c, "g3_account", li["account_id"])
            if (
                li["state"] != "STOPPED"
                or intent["action"] != "STOP"
                or intent["created_at"] <= scope["created_at"]
            ):
                fail("RECOVERY_FRESH_STOP_REQUIRED")
            checks.append((li, intent, account))
    for li, intent, account in checks:
        response = transport.request(
            actor["tenant_id"], account["code"], "GET", transport.listing_path(li["listing_key"])
        )
        if response.status_code != 200:
            fail("RECOVERY_REMOTE_UNKNOWN")
        data = response.json()
        cells = {k: v.get("value") for k, v in data.get("cells", {}).items()}
        expected = {cell_key(x): x for x in intent["payload"]["cells"]}
        if (
            data.get("complete") is not True
            or data.get("revision") != intent["delivery_revision"]
            or cells != expected
            or any(x.get("sellable") is not False for x in cells.values())
            or normalize_content(data.get("content"), account["code"])
            != intent["payload"]["content"]
        ):
            fail("RECOVERY_CLOSE_PROOF_MISMATCH")
    with db.transaction(actor["tenant_id"]) as c:
        domain.lock_source(c, actor["tenant_id"], key)
        for li, _, _ in checks:
            check_version(get(c, "g3_listing", li["id"], True), li["version"])
        c.execute(
            "UPDATE g5_recovery_scope SET state='RELEASED',reason=%s WHERE source_key=%s AND state='HELD'",
            (reason, key),
        )
        c.execute(
            "UPDATE g3_listing SET paused=false,version=version+1 WHERE source_key=%s", (key,)
        )
        audit(
            c,
            actor,
            "g5.recovery.scope-released",
            None,
            "Actual fresh stop proof checked; new independent snapshot approval still required: "
            + key
            + " "
            + reason,
        )
    return {"source_key": key, "state": "RELEASED", "stop_fence": "RETAINED_NEW_APPROVAL_REQUIRED"}
