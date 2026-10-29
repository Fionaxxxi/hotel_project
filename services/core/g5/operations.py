"""Tenant-only bounded gauges and durable alerts. No business repair side effects."""

from uuid import uuid4

from psycopg import sql

from services.core.g1.common import audit, authorize, business_now, check_version, fail, get, j


def metrics(c):
    c.execute("SET LOCAL statement_timeout='2000ms'")
    result = {
        "schema": "g5-metrics-v1",
        "collected_at": c.execute("SELECT clock_timestamp() AS t").fetchone()["t"],
        "status": "CURRENT",
        "groups": {},
    }
    for table, column in [
        ("execution_job", "state"),
        ("g3_task", "state"),
        ("g4_run", "state"),
        ("workflow_run", "state"),
        ("g4_ai_task", "state"),
    ]:
        rows = c.execute(
            sql.SQL("SELECT {} AS state,count(*) AS count FROM {} GROUP BY {} ORDER BY {}").format(
                sql.Identifier(column),
                sql.Identifier(table),
                sql.Identifier(column),
                sql.Identifier(column),
            )
        ).fetchall()
        result["groups"][table] = rows
    result["queue"] = c.execute(
        "SELECT count(*) FILTER(WHERE state IN ('READY','RETRY_WAIT') AND next_run_at<=clock_timestamp()) AS due, count(*) FILTER(WHERE state='RUNNING' AND lease_until<clock_timestamp()) AS expired_lease, coalesce(max(extract(epoch FROM clock_timestamp()-greatest(next_run_at,created_at))) FILTER(WHERE state IN ('READY','RETRY_WAIT') AND next_run_at<=clock_timestamp()),0) AS oldest_due_seconds FROM execution_job"
    ).fetchone()
    result["outbox"] = c.execute(
        "SELECT count(*) AS pending,coalesce(max(extract(epoch FROM clock_timestamp()-created_at)),0) AS oldest_seconds FROM outbox_event WHERE delivered_at IS NULL"
    ).fetchone()
    result["dead_letters"] = c.execute(
        "SELECT count(*) AS count FROM g3_dead_letter WHERE state='OPEN'"
    ).fetchone()["count"]
    result["risk"] = c.execute(
        "SELECT count(*) FILTER(WHERE state='UNKNOWN') AS unknown_listings,count(*) FILTER(WHERE state='STOP_SELL_REQUESTED') AS stopping FROM g3_listing"
    ).fetchone()
    return result


def risks(c):
    """Thresholds are real UTC durations, not hotel business time."""
    found = []
    from services.core.g5.backup_status import status as backup_status

    backup = backup_status()
    if backup["state"] == "STALE":
        found.append(
            {
                "kind": "BACKUP_STALE",
                "fingerprint": "BACKUP_STALE",
                "threshold": 86400,
                "detail": backup,
            }
        )
    definitions = [
        (
            "SOURCE_EXPIRED",
            "SELECT p.id,min(i.observed_at) AS since FROM inventory_pool p JOIN inventory_cell i ON(i.tenant_id,i.pool_id)=(p.tenant_id,p.id) WHERE EXISTS(SELECT 1 FROM source_partition h WHERE h.activation_id=p.activation_id) GROUP BY p.id HAVING min(i.observed_at)<%s::timestamptz-interval '24 hours' ORDER BY since LIMIT 100",
            86400,
        ),
        (
            "RATE_LIMITED",
            "SELECT task_id AS id,min(created_at) AS since FROM g3_receipt WHERE status=429 AND created_at>clock_timestamp()-interval '60 seconds' GROUP BY task_id HAVING count(*)>=3 ORDER BY since LIMIT 100",
            60,
        ),
        (
            "WORKER_STALE",
            "SELECT worker_id AS id,last_seen AS since FROM g5_worker_heartbeat WHERE slot<>'' AND last_seen<clock_timestamp()-interval '30 seconds' ORDER BY last_seen LIMIT 100",
            30,
        ),
        (
            "OUTBOX_LAG",
            "SELECT id,created_at AS since FROM outbox_event WHERE delivered_at IS NULL AND created_at<clock_timestamp()-interval '60 seconds' ORDER BY created_at LIMIT 100",
            60,
        ),
        (
            "LEASE_EXPIRED",
            "SELECT id,lease_until AS since FROM execution_job WHERE state='RUNNING' AND lease_until<clock_timestamp() ORDER BY lease_until LIMIT 100",
            0,
        ),
        (
            "DEAD_LETTER",
            "SELECT id,created_at AS since FROM g3_dead_letter WHERE state='OPEN' ORDER BY created_at LIMIT 100",
            0,
        ),
        (
            "UNKNOWN",
            "SELECT t.id,i.created_at AS since FROM g3_task t JOIN g3_intent i ON(t.tenant_id,t.intent_id)=(i.tenant_id,i.id) WHERE t.state='UNKNOWN' AND i.created_at<clock_timestamp()-interval '60 seconds' ORDER BY i.created_at LIMIT 100",
            60,
        ),
        (
            "STOP_PENDING",
            "SELECT s.id,s.created_at AS since FROM g3_stop s JOIN g3_listing l ON(s.tenant_id,s.listing_id)=(l.tenant_id,l.id) WHERE l.state<>'STOPPED' AND s.created_at<clock_timestamp()-interval '5 minutes' ORDER BY s.created_at LIMIT 100",
            300,
        ),
    ]
    for kind, query, threshold in definitions:
        params = (business_now(),) if kind == "SOURCE_EXPIRED" else ()
        full = query.replace(" LIMIT 100", "")
        summary = c.execute(
            "SELECT count(*) AS count,min(since) AS since FROM (" + full + ") AS risks", params
        ).fetchone()
        if summary["count"]:
            rows = c.execute(query.replace(" LIMIT 100", " LIMIT 10"), params).fetchall()
            found.append(
                {
                    "kind": kind,
                    "fingerprint": kind,
                    "threshold": threshold,
                    "detail": {
                        "count": summary["count"],
                        "sample_ids": [str(row["id"]) for row in rows],
                        "since": summary["since"].isoformat(),
                    },
                }
            )
    return found


def collect(c, actor):
    c.execute("SET LOCAL statement_timeout='3000ms'")
    c.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        (str(actor["tenant_id"]) + ":g5-alerts",),
    )
    current = risks(c)
    # Aggregates count all risks; only object detail samples are capped.
    complete = True
    active = c.execute("SELECT * FROM g5_alert WHERE state<>'RESOLVED' FOR UPDATE").fetchall()
    seen = {(x["kind"], x["fingerprint"]) for x in current}
    for item in current:
        old = next(
            (
                x
                for x in active
                if (x["kind"], x["fingerprint"]) == (item["kind"], item["fingerprint"])
            ),
            None,
        )
        if old:
            c.execute(
                "UPDATE g5_alert SET last_seen=clock_timestamp(),detail=%s WHERE id=%s",
                (j(item["detail"]), old["id"]),
            )
        else:
            aid = uuid4()
            c.execute(
                "INSERT INTO g5_alert(id,tenant_id,kind,fingerprint,threshold_seconds,detail) VALUES(%s,%s,%s,%s,%s,%s)",
                (
                    aid,
                    actor["tenant_id"],
                    item["kind"],
                    item["fingerprint"],
                    item["threshold"],
                    j(item["detail"]),
                ),
            )
            event(c, actor, aid, "OPEN", "Observed by collector")
    if complete:
        for old in active:
            if (old["kind"], old["fingerprint"]) not in seen:
                c.execute(
                    "UPDATE g5_alert SET state='RESOLVED',resolved_at=clock_timestamp(),version=version+1 WHERE id=%s",
                    (old["id"],),
                )
                event(
                    c, actor, old["id"], "RESOLVED", "Risk absent in successful complete collection"
                )
    return {"status": "CURRENT", "complete": complete, "risk_count": len(current)}


def event(c, actor, aid, action, reason):
    c.execute(
        "INSERT INTO g5_alert_event(id,tenant_id,alert_id,action,actor,reason) VALUES(%s,%s,%s,%s,%s,%s)",
        (uuid4(), actor["tenant_id"], aid, action, actor["subject"], reason),
    )


def acknowledge(c, actor, aid, version, reason):
    authorize(actor)
    row = get(c, "g5_alert", aid, True)
    check_version(row, version)
    if row["state"] != "OPEN":
        fail("ALERT_NOT_OPEN")
    c.execute("UPDATE g5_alert SET state='ACKNOWLEDGED',version=version+1 WHERE id=%s", (aid,))
    event(c, actor, aid, "ACKNOWLEDGED", reason)
    audit(c, actor, "g5.alert.acknowledge", aid, reason)
    return get(c, "g5_alert", aid)
