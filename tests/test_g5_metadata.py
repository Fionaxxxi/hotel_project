import logging
from uuid import uuid4

import pytest
from fastapi import HTTPException

from infra.manage import uid
from services.core.g1.common import j
from services.core.g3 import transport
from services.core.g5 import operations
from services.core.g5.logging import Formatter
from tests.g1_helpers import auth


def test_tenant_metrics_do_not_expose_global_connection_counters(client, settings):
    response = client.get("/v5/operations/metrics", headers=auth(settings, "auditor", "B"))
    assert response.status_code == 200
    assert "api_pool" not in response.json()


def test_isolated_profile_cannot_redirect_normal_core(monkeypatch):
    monkeypatch.setenv("G5_ISOLATED_SIMULATORS", "true")
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused@127.0.0.1:55432/hotel_project")
    with pytest.raises(HTTPException) as exc:
        transport.request(uid("tenant-A"), "SIM-A", "GET", "/g3/health")
    assert exc.value.detail == "ISOLATED_SIMULATORS_REQUIRE_RESTORE_DATABASE"


def test_structured_formatter_removes_sensitive_fields():
    record = logging.LogRecord(
        "hotel",
        logging.INFO,
        "",
        1,
        '{"event":"test","authorization":"secret","nested":{"password":"secret"}}',
        (),
        None,
    )
    text = Formatter().format(record)
    assert "secret" not in text
    assert "[REDACTED]" in text and "timestamp" in text


def test_failed_collection_does_not_resolve_risk(client, settings, monkeypatch):
    from tests.g3_helpers import drain, verified

    _, plan = verified(client, settings)
    tenant = uid("tenant-A")
    aid = uuid4()
    with client.app.state.db.transaction(tenant) as c:
        c.execute(
            "INSERT INTO g5_alert(id,tenant_id,kind,fingerprint,threshold_seconds,detail) VALUES(%s,%s,'TEST_G5_COLLECTION',%s,1,%s)",
            (aid, tenant, str(aid), j({})),
        )

    def failed(*args):
        raise RuntimeError("SYNTHETIC_COLLECTION_FAILURE")

    monkeypatch.setattr(operations, "risks", failed)
    response = client.post("/v5/operations/collect", headers=auth(settings))
    assert response.status_code == 500
    assert client.get("/health/live").status_code == 200
    for branch in plan["branches"]:
        li = client.get("/v3/listings/" + branch["listing_id"], headers=auth(settings)).json()
        result = client.post(
            "/v3/listings/" + li["id"] + "/stop",
            headers={**auth(settings, "tenant_admin"), "Idempotency-Key": str(uuid4())},
            json={"expected_version": li["version"], "dates": sorted({x["date"] for x in li["intent"]["payload"]["cells"]}), "reason": "Emergency close while operations collector is failing"},
        )
        assert result.status_code == 202
    drain(client, settings, lambda: all(client.get("/v3/listings/" + b["listing_id"], headers=auth(settings)).json()["state"] == "STOPPED" for b in plan["branches"]))
    with client.app.state.db.transaction(tenant) as c:
        assert (
            c.execute("SELECT state FROM g5_alert WHERE id=%s", (aid,)).fetchone()["state"]
            == "OPEN"
        )
