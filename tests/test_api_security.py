from dataclasses import replace

import psycopg
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from infra.manage import uid
from services.core.auth import issue_token, require_role
from services.core.main import create_app
from services.core.version import SCHEMA_VERSION


def headers(settings, subject="a.operator", tenant="A"):
    return {
        "Authorization": "Bearer " + issue_token(subject, settings.jwt_secret),
        "X-Tenant-ID": str(uid(f"tenant-{tenant}")),
    }


def test_health_identity_and_openapi(client, settings):
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").json()["migration"] == SCHEMA_VERSION
    response = client.get("/v1/me", headers=headers(settings))
    assert response.status_code == 200
    assert response.json()["subject"] == "a.operator"
    assert len(response.json()["memberships"]) == 1
    assert client.get("/openapi.json").status_code == 200


@pytest.mark.parametrize("token", [None, "bad", "expired", "wrong-signature", "unknown-user"])
def test_reject_credentials(client, settings, token):
    if token == "expired":
        token = issue_token("a.operator", settings.jwt_secret, -1)
    elif token == "wrong-signature":
        token = issue_token("a.operator", "x" * 48)
    elif token == "unknown-user":
        token = issue_token("not-a-member", settings.jwt_secret)
    response = client.get("/v1/me", headers={"Authorization": f"Bearer {token}"} if token else {})
    assert response.status_code in {401, 403}
    assert response.json()["trace_id"] == response.headers["X-Trace-ID"]
    assert "token" not in response.json()["details"]


def test_cross_tenant_api_and_pool_reuse(client, settings):
    for tenant in ["A", "B"] * 6:
        response = client.get(
            "/v1/suppliers", headers=headers(settings, f"{tenant.lower()}.operator", tenant)
        )
        assert response.status_code == 200
        assert len(response.json()["items"]) == (2 if tenant == "A" else 1)
        assert all(tenant + "/" in item["name"] for item in response.json()["items"])
    assert (
        client.get(f"/v1/properties/{uid('property-B')}", headers=headers(settings)).status_code
        == 404
    )
    assert (
        client.get(f"/v1/properties/{uid('property-A')}", headers=headers(settings)).status_code
        == 200
    )
    assert client.get("/v1/me", headers=headers(settings, tenant="B")).status_code == 403
    assert (
        client.get("/v1/me", headers={**headers(settings), "X-Tenant-ID": "invalid"}).status_code
        == 422
    )
    assert client.get("/v1/suppliers?limit=101", headers=headers(settings)).status_code == 422


def test_disabled_membership(client, settings):
    # Owner creates a temporary fixture, rolls it back after the API request.
    with psycopg.connect(settings.migration_url, autocommit=True) as admin:
        admin.execute(
            "CREATE POLICY test_membership ON membership TO hotel_migrator USING (true) WITH CHECK (true)"
        )
        try:
            admin.execute(
                "UPDATE membership SET status='disabled' WHERE user_id=%s", (uid("a.operator"),)
            )
            assert client.get("/v1/me", headers=headers(settings)).status_code == 403
        finally:
            admin.execute(
                "UPDATE membership SET status='active' WHERE user_id=%s", (uid("a.operator"),)
            )
            admin.execute("DROP POLICY test_membership ON membership")


def test_development_login(client, settings, monkeypatch):
    monkeypatch.setenv("DEV_LOGIN_PASSWORD", "local-test-password")
    assert (
        client.post("/dev/token", json={"subject": "a.operator", "password": "wrong"}).status_code
        == 401
    )
    response = client.post(
        "/dev/token", json={"subject": "a.operator", "password": "local-test-password"}
    )
    assert response.status_code == 200
    assert (
        client.get(
            "/v1/me", headers={"Authorization": "Bearer " + response.json()["access_token"]}
        ).status_code
        == 200
    )


def test_role_guard():
    require_role("tenant_admin", {"tenant_admin"})
    with pytest.raises(HTTPException) as exc:
        require_role("auditor", {"tenant_admin"})
    assert exc.value.status_code == 403


def test_disabled_tenant(client, settings):
    with psycopg.connect(settings.migration_url, autocommit=True) as admin:
        admin.execute(
            "CREATE POLICY test_tenant ON tenant TO hotel_migrator USING (true) WITH CHECK (true)"
        )
        try:
            admin.execute("UPDATE tenant SET status='disabled' WHERE id=%s", (uid("tenant-A"),))
            assert client.get("/v1/me", headers=headers(settings)).status_code == 403
        finally:
            admin.execute("UPDATE tenant SET status='active' WHERE id=%s", (uid("tenant-A"),))
            admin.execute("DROP POLICY test_tenant ON tenant")


def test_stale_migration(client, settings):
    with psycopg.connect(settings.migration_url, autocommit=True) as admin:
        try:
            admin.execute("UPDATE schema_revision SET version='000'")
            assert client.get("/health/ready").status_code == 503
            assert client.get("/health/live").status_code == 200
        finally:
            admin.execute("UPDATE schema_revision SET version=%s", (SCHEMA_VERSION,))


def test_unready_and_no_dev_endpoint(settings):
    unavailable = replace(
        settings, database_url="postgresql://invalid:invalid@127.0.0.1:1/invalid", dev_auth=False
    )
    with TestClient(create_app(unavailable)) as c:
        assert c.get("/health/live").status_code == 200
        response = c.get("/health/ready")
        assert response.status_code == 503
        assert "invalid:" not in response.text
        assert c.post("/dev/token", json={}).status_code == 404
