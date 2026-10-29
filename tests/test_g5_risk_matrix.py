"""Controlled database fault injection; no production timestamps or remote state changed."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from infra.manage import uid
from services.core.g5 import operations
from tests.g3_helpers import prepared, publish


def test_all_operational_risk_kinds_detected_without_unbounded_labels(client, settings, monkeypatch):
    bundle = prepared(client, settings)
    plan = publish(client, settings, bundle)
    tenant = uid("tenant-A")
    monkeypatch.setattr(operations, "business_now", lambda: datetime(2050, 1, 1, tzinfo=UTC))
    from services.core.g5 import backup_status

    monkeypatch.setattr(backup_status, "status", lambda: {"state": "STALE", "reason": "CONTROLLED_TEST"})
    with client.app.state.db.transaction(tenant) as c:
        # Roll back all age/state injections together. Normal stores are never involved.
        with c.transaction(force_rollback=True):
            old = datetime.now(UTC) - timedelta(minutes=10)
            event_id, job_id, intent_id, task_id, stop_id = [uuid4() for _ in range(5)]
            c.execute("INSERT INTO outbox_event(id,tenant_id,event_type,aggregate_id,aggregate_version,payload,trace_id,created_at) VALUES(%s,%s,'g5.test.risk',%s,1,'{}',%s,%s)", (event_id, tenant, uuid4(), uuid4(), old))
            c.execute("INSERT INTO execution_job(id,tenant_id,kind,resource_id,logical_key,state,owner,lease_until) VALUES(%s,%s,'G5_TEST',%s,%s,'RUNNING','g5-test',%s)", (job_id, tenant, uuid4(), str(uuid4()), old))
            listing = UUID(plan["branches"][0]["listing_id"])
            c.execute("INSERT INTO g3_intent(id,tenant_id,listing_id,snapshot_id,action,desired_revision,delivery_revision,stop_epoch,payload,payload_hash,manifest,manifest_hash,created_by,reason,created_at) SELECT %s,tenant_id,listing_id,snapshot_id,'PUBLISH',desired_revision+1000000000,delivery_revision+1000000000,stop_epoch,payload,payload_hash,manifest,manifest_hash,'g5.test','Controlled risk fixture',%s FROM g3_intent WHERE listing_id=%s ORDER BY created_at DESC LIMIT 1", (intent_id, old, listing))
            c.execute("INSERT INTO g3_task(id,tenant_id,intent_id,chunk_index,action,request_key,body,request_hash,state) VALUES(%s,%s,%s,0,'PUBLISH',%s,'{}','test','UNKNOWN')", (task_id, tenant, intent_id, str(uuid4())))
            c.execute("INSERT INTO g3_dead_letter(id,tenant_id,task_id,cycle,code) VALUES(%s,%s,%s,1,'G5_TEST')", (uuid4(), tenant, task_id))
            c.execute("INSERT INTO g3_stop(id,tenant_id,listing_id,command_key,request_hash,intent_id,reason,created_by,created_at) VALUES(%s,%s,%s,%s,'test',%s,'Controlled age fixture','g5.test',%s)", (stop_id, tenant, listing, str(uuid4()), intent_id, old))
            for _ in range(3):
                c.execute("INSERT INTO g3_receipt(id,tenant_id,task_id,phase,status,data) VALUES(%s,%s,%s,'TEST',429,'{}')", (uuid4(), tenant, task_id))
            c.execute("INSERT INTO g5_worker_heartbeat(tenant_id,worker_id,lane,slot,last_seen) VALUES(%s,%s,'test',%s,%s)", (tenant, uuid4(), 'g5-risk-' + uuid4().hex, old))
            found = operations.risks(c)
            expected = {"BACKUP_STALE", "SOURCE_EXPIRED", "RATE_LIMITED", "WORKER_STALE", "OUTBOX_LAG", "LEASE_EXPIRED", "DEAD_LETTER", "UNKNOWN", "STOP_PENDING"}
            assert expected <= {x["kind"] for x in found}
            assert all(len(x["detail"].get("sample_ids", [])) <= 10 for x in found)
            operations.collect(c, {"tenant_id": tenant, "subject": "a.operator", "role": "operator"})
            active = c.execute("SELECT kind FROM g5_alert WHERE state<>'RESOLVED'").fetchall()
            assert expected <= {x["kind"] for x in active}
