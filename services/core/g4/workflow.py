"""Durable G4 subscription reconciler. Current facts, not event order, drive progress."""

from uuid import UUID, uuid4

from fastapi import HTTPException

from services.core.g1 import matching, offers
from services.core.g1.common import audit, authorize, digest, fail, get, j
from services.core.g2 import views
from services.core.g3 import domain
from services.core.g4 import policy


def subscribe(c, actor, pid):
    authorize(actor, {"tenant_admin"})
    p = get(c, "g4_policy", pid)
    domain.lock_source(c, actor["tenant_id"], p["source_key"])
    if p["state"] != "ENABLED":
        fail("POLICY_NOT_ENABLED")
    sid = uuid4()
    c.execute(
        "INSERT INTO g4_subscription(id,tenant_id,source_key,policy_id,initial_activation_id,created_by) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
        (
            sid,
            actor["tenant_id"],
            p["source_key"],
            pid,
            UUID(p["body"]["activation_id"]),
            actor["subject"],
        ),
    )
    return c.execute(
        "SELECT * FROM g4_subscription WHERE source_key=%s", (p["source_key"],)
    ).fetchone()


def record(c, run, state, reason, refs=None):
    detail = {"reason": reason, **(refs or {})}
    c.execute(
        "INSERT INTO g4_step(id,tenant_id,run_id,step_key,input_hash,state,detail) VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
        (uuid4(), run["tenant_id"], run["id"], state, digest(detail), state, j(detail)),
    )
    if run["state"] != state or run["reason"] != reason:
        c.execute(
            "UPDATE g4_run SET state=%s,reason=%s,version=version+1 WHERE id=%s",
            (state, reason, run["id"]),
        )


def fingerprint(data):
    from services.core.g4.ai import normalized

    return digest({k: normalized(v) if isinstance(v, str) else v for k, v in data.items()})


def reuse_mappings(c, tenant, aid, old_aid):
    for source in c.execute(
        "SELECT * FROM supplier_entity_version WHERE activation_id=%s ORDER BY CASE kind WHEN 'property' THEN 0 WHEN 'room' THEN 1 ELSE 2 END,natural_key",
        (aid,),
    ).fetchall():
        current = matching.mapping(c, source)
        if current and current["status"] == "APPROVED":
            continue
        old = c.execute(
            "SELECT * FROM supplier_entity_version WHERE activation_id=%s AND kind=%s AND natural_key=%s",
            (old_aid, source["kind"], source["natural_key"]),
        ).fetchone()
        approved = matching.mapping(c, old) if old else None
        if (
            not approved
            or approved["status"] != "APPROVED"
            or fingerprint(old["data"]) != fingerprint(source["data"])
        ):
            continue
        target = next(
            (
                x
                for x in matching.targets(c, source)
                if x["target_id"] == approved["target_id"]
                and x["target_version_id"] == approved["target_version_id"]
            ),
            None,
        )
        if not target or matching.conflicts(c, source, target):
            continue
        if current:
            # Rejected current decisions must not be silently overwritten.
            continue
        table = source["kind"] + "_mapping"
        c.execute(
            f"INSERT INTO {table}(id,tenant_id,source_id,target_id,target_version_id,status,actor,reason) VALUES(%s,%s,%s,%s,%s,'APPROVED','system.mapping-reuse','Verified unchanged facts and prior human approval')",
            (uuid4(), tenant, source["id"], approved["target_id"], approved["target_version_id"]),
        )
        c.execute(
            "INSERT INTO g4_mapping_reuse(id,tenant_id,source_id,previous_source_id,fingerprint,target_version_id) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (
                uuid4(),
                tenant,
                source["id"],
                old["id"],
                fingerprint(source["data"]),
                approved["target_version_id"],
            ),
        )


def advance(c, run):
    sub = get(c, "g4_subscription", run["subscription_id"])
    domain.lock_source(c, run["tenant_id"], sub["source_key"])
    run = get(c, "g4_run", run["id"], True)
    if run["state"] in {"COMPLETED", "STALE", "CANCELLED"}:
        return
    p = get(c, "g4_policy", sub["policy_id"])
    actor = {"tenant_id": run["tenant_id"], "subject": sub["created_by"], "role": "tenant_admin"}
    processing = get(c, "processing_run", run["processing_id"])
    c.execute("SELECT set_config('app.subject',%s,true)", (sub["created_by"],))
    member = c.execute(
        "SELECT m.role FROM membership m JOIN app_user u ON u.id=m.user_id WHERE u.subject=%s AND m.status='active'",
        (sub["created_by"],),
    ).fetchone()
    if not member or member["role"] != "tenant_admin":
        record(c, run, "BLOCKED", "AUTOMATION_INITIATOR_REVOKED")
        return
    if run["activation_id"]:
        current = c.execute(
            "SELECT l.activation_id FROM source_head h JOIN generation_activation l ON l.generation_id=h.generation_id WHERE h.supplier_id=%s AND h.property_code=%s",
            (UUID(p["body"]["supplier_id"]), p["body"]["property_code"]),
        ).fetchone()
        if not current or current["activation_id"] != run["activation_id"]:
            record(c, run, "STALE", "SOURCE_NOT_CURRENT")
            return
    if run["plan_id"]:
        plan = domain.plan_detail(c, run["plan_id"])
        record(
            c,
            run,
            "COMPLETED" if plan["state"] == "VERIFIED" else "WAIT_EXTERNAL",
            plan["state"],
            {"plan_id": str(plan["id"])},
        )
        return
    if sub["state"] != "ACTIVE":
        record(c, run, "PAUSED", "SUBSCRIPTION_PAUSED")
        return
    batch = get(c, "import_batch", processing["batch_id"])
    manifest = batch["manifest"]
    if processing["status"] not in {"ACTIVE", "READY_TO_ACTIVATE", "QUALITY_REVIEW"}:
        record(
            c,
            run,
            "WAIT_QUALITY" if processing["status"] == "MANUAL" else "PROCESSING",
            processing["status"],
        )
        return
    if processing["status"] != "ACTIVE":
        if (
            manifest["scope"] != p["body"]["scope"]
            or manifest["mode"] != p["body"]["mode"]
            or manifest["contract_version"] != p["body"]["contract_version"]
            or manifest["sequence_stream"] != p["body"]["sequence_stream"]
        ):
            record(c, run, "WAIT_SCOPE", "SCOPE_OR_CONTRACT_CHANGED")
            return
        from services.core.g1.common import now

        if p["state"] != "ENABLED" or p["expires_at"] <= now() or not p["body"]["auto_activate"]:
            record(c, run, "WAIT_SCOPE", "AUTOMATIC_ACTIVATION_NOT_AUTHORIZED")
            return
        if c.execute(
            "SELECT 1 FROM dq_issue WHERE run_id=%s LIMIT 1", (processing["id"],)
        ).fetchone():
            record(c, run, "WAIT_QUALITY", "QUALITY_ISSUES_REQUIRE_REVIEW")
            return
        current = views.versions(c, batch["supplier_id"], manifest["scope"]["property_codes"][0])
        if manifest["source_sequence"] <= max((v["sequence"] for v in current.values()), default=0):
            record(c, run, "STALE", "SOURCE_SEQUENCE_CONFLICT")
            return
        body = {
            "expected_run_version": processing["version"],
            "expected_versions": {k: v["version"] for k, v in current.items()},
            "selected_dates": views.dates(manifest["scope"]),
            "partial_confirmed": False,
        }
        # Missing initial partition versions are explicit zero, never inferred overwrite.
        for key in ["MASTER", *body["selected_dates"]]:
            body["expected_versions"].setdefault(key, 0)
        with c.transaction():
            gen = views.activate(c, actor, processing["id"], body, "g4:" + str(run["id"]))
        aid = gen["activation_id"]
    else:
        link = c.execute(
            "SELECT l.activation_id FROM source_generation g JOIN generation_activation l ON l.generation_id=g.id WHERE g.run_id=%s ORDER BY g.created_at DESC LIMIT 1",
            (processing["id"],),
        ).fetchone()
        if not link:
            record(c, run, "BLOCKED", "ACTIVATION_LINK_MISSING")
            return
        aid = link["activation_id"]
    c.execute("UPDATE g4_run SET activation_id=%s WHERE id=%s", (aid, run["id"]))
    head = c.execute(
        "SELECT l.activation_id FROM source_head h JOIN generation_activation l ON l.generation_id=h.generation_id WHERE h.supplier_id=%s AND h.property_code=%s",
        (batch["supplier_id"], manifest["scope"]["property_codes"][0]),
    ).fetchone()
    if not head or head["activation_id"] != aid:
        record(c, run, "STALE", "SOURCE_NOT_CURRENT")
        return
    reuse_mappings(c, run["tenant_id"], aid, sub["initial_activation_id"])
    try:
        with c.transaction():
            offers.dependencies(c, aid)
    except HTTPException as exc:
        if exc.detail in {"MAPPING_REQUIRED", "MAPPING_STALE"}:
            from services.core.g4 import ai

            for entity in c.execute(
                "SELECT id FROM supplier_entity_version WHERE activation_id=%s AND kind='property'",
                (aid,),
            ).fetchall():
                ai.create(c, actor, entity["id"])
        record(
            c,
            run,
            "WAIT_MAPPING" if exc.detail in {"MAPPING_REQUIRED", "MAPPING_STALE"} else "BLOCKED",
            str(exc.detail),
        )
        return
    allocation = None
    if run["allocation_id"]:
        allocation = get(c, "g3_allocation", run["allocation_id"])
        current = c.execute(
            "SELECT * FROM g3_allocation_head WHERE source_key=%s", (sub["source_key"],)
        ).fetchone()
        if current and current["allocation_id"] != allocation["id"]:
            proposed = get(c, "g3_allocation", current["allocation_id"])
            if proposed["activation_id"] == aid:
                allocation = proposed
                c.execute(
                    "UPDATE g4_run SET allocation_id=%s,snapshot_ids='[]' WHERE id=%s",
                    (allocation["id"], run["id"]),
                )
                run = {**run, "snapshot_ids": []}
    if allocation is None:
        current = c.execute(
            "SELECT * FROM g3_allocation_head WHERE source_key=%s", (sub["source_key"],)
        ).fetchone()
        try:
            with c.transaction():
                allocation = domain.allocate(
                    c,
                    actor,
                    aid,
                    p["body"]["amounts"],
                    p["body"]["buffer"],
                    current["version"] if current else 0,
                )
        except HTTPException as exc:
            record(c, run, "WAIT_ALLOCATION", str(exc.detail))
            return
        c.execute("UPDATE g4_run SET allocation_id=%s WHERE id=%s", (allocation["id"], run["id"]))
    snapshots = []
    if run["snapshot_ids"]:
        snapshots = [get(c, "g3_snapshot", UUID(x)) for x in run["snapshot_ids"]]
    if not snapshots:
        for channel in ["SIM-A", "SIM-B"]:
            with c.transaction():
                snapshots.append(
                    domain.compile_snapshot(
                        c, actor, allocation["id"], channel, p["body"]["plan_codes"]
                    )
                )
        c.execute(
            "UPDATE g4_run SET snapshot_ids=%s WHERE id=%s",
            (j([str(s["id"]) for s in snapshots]), run["id"]),
        )
    for s in snapshots:
        if s["state"] == "REJECTED":
            record(c, run, "BLOCKED", "SNAPSHOT_REJECTED")
            return
        if s["state"] == "SUBMITTED":
            policy.evaluate(c, p["id"], s["id"])
    snapshots = [get(c, "g3_snapshot", s["id"]) for s in snapshots]
    if any(s["state"] != "APPROVED" for s in snapshots):
        record(
            c,
            run,
            "WAIT_APPROVAL",
            "POLICY_OUTSIDE_AUTHORIZED_RANGE",
            {"snapshot_ids": [str(s["id"]) for s in snapshots]},
        )
        return
    expected = {}
    for s in snapshots:
        li = c.execute(
            "SELECT * FROM g3_listing WHERE account_id=%s AND listing_key=%s",
            (s["account_id"], s["listing_key"]),
        ).fetchone()
        expected[str(s["account_id"])] = li["desired_revision"] if li else 0
    with c.transaction():
        plan = domain.publish(
            c, actor, [s["id"] for s in snapshots], "g4:" + str(run["id"]), expected
        )
    c.execute("UPDATE g4_run SET plan_id=%s WHERE id=%s", (plan["id"], run["id"]))
    record(c, run, "RUNNING", "G3_PLAN_COMMITTED", {"plan_id": str(plan["id"])})
    audit(
        c,
        actor,
        "g4.workflow.published",
        run["id"],
        "Separate per-snapshot authorization and durable G3 plan",
    )


def tick(db, tenant, subscription_ids=None):
    with db.transaction(tenant) as c:
        subscriptions = c.execute(
            "SELECT * FROM g4_subscription WHERE (%s::uuid[] IS NULL OR id=ANY(%s::uuid[]))",
            (subscription_ids, subscription_ids),
        ).fetchall()
        for sub in subscriptions:
            supplier, prop = sub["source_key"].split("/", 1)
            rows = c.execute(
                "SELECT p.id FROM processing_run p JOIN import_batch b ON b.id=p.batch_id WHERE b.supplier_id=%s AND b.manifest->'scope'->'property_codes'->>0=%s AND p.created_at>=%s AND NOT EXISTS(SELECT 1 FROM g4_run r WHERE r.processing_id=p.id) ORDER BY p.created_at LIMIT 100",
                (UUID(supplier), prop, sub["created_at"]),
            ).fetchall()
            for row in rows:
                c.execute(
                    "INSERT INTO g4_run(id,tenant_id,subscription_id,processing_id) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    (uuid4(), tenant, sub["id"], row["id"]),
                )
    for sub in subscriptions:
        with db.transaction(tenant) as c:
            runs = c.execute(
                "SELECT id FROM g4_run WHERE subscription_id=%s AND state NOT IN ('COMPLETED','STALE','CANCELLED') ORDER BY created_at LIMIT 100",
                (sub["id"],),
            ).fetchall()
        for run in runs:
            with db.transaction(tenant) as c:
                r = get(c, "g4_run", run["id"])
                resources = [r["processing_id"]]
                resources += [UUID(x) for x in r["snapshot_ids"]]
                if r["activation_id"]:
                    resources += [
                        x["id"]
                        for x in c.execute(
                            "SELECT id FROM supplier_entity_version WHERE activation_id=%s",
                            (r["activation_id"],),
                        ).fetchall()
                    ]
                for event in c.execute(
                    "SELECT e.id FROM outbox_event e WHERE e.aggregate_id=ANY(%s::uuid[]) AND NOT EXISTS(SELECT 1 FROM consumer_inbox i WHERE i.event_id=e.id AND i.consumer='g4-flow-v1') ORDER BY e.created_at LIMIT 100",
                    (resources,),
                ).fetchall():
                    c.execute(
                        "INSERT INTO consumer_inbox(id,tenant_id,event_id,consumer,result) VALUES(%s,%s,%s,'g4-flow-v1','APPLIED') ON CONFLICT DO NOTHING",
                        (uuid4(), tenant, event["id"]),
                    )
                try:
                    with c.transaction():
                        from services.core.g1.common import REQUEST_TRACE
                        from services.core.g5.tracing import CAUSE_EVENT

                        origin = c.execute(
                            "SELECT trace_id FROM execution_job WHERE resource_id=%s AND kind='PARSE' ORDER BY created_at LIMIT 1",
                            (r["processing_id"],),
                        ).fetchone()
                        event = c.execute(
                            "SELECT id FROM outbox_event WHERE aggregate_id=%s AND event_type='processing.completed' ORDER BY created_at DESC LIMIT 1",
                            (r["processing_id"],),
                        ).fetchone()
                        token = REQUEST_TRACE.set(origin["trace_id"] if origin else None)
                        cause = CAUSE_EVENT.set(event["id"] if event else None)
                        try:
                            advance(c, r)
                        finally:
                            REQUEST_TRACE.reset(token)
                            CAUSE_EVENT.reset(cause)
                except HTTPException as exc:
                    record(c, r, "BLOCKED", str(exc.detail))
    return bool(subscriptions)
