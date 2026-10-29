from uuid import uuid4

import pytest

from infra.g5_recovery import bounded
from services.core.g5.audit import evidence_package, verify_package
from services.core.g5.security import password_hash, redact, safe_csv, verify_password
from tests.g1_helpers import auth


@pytest.mark.parametrize(
    "value", ["=SUM(1,2)", " +cmd", "\t@SUM(A1)", "\r-1", "\ufeff=HYPERLINK(1)", "\x00=1"]
)
def test_safe_formula_export(value):
    assert safe_csv(value) == "'" + value


def test_password_hash_and_malformed_values():
    encoded = password_hash("local-secret")
    assert verify_password("local-secret", encoded)
    assert not verify_password("wrong", encoded)
    assert not verify_password("local-secret", "bad")
    assert not verify_password("x", "pbkdf2_sha256$999999999$foo$bar")
    assert "local-secret" not in encoded


def test_redaction_and_integrity():
    assert redact(
        {"nested": {"authorization": "secret", "storage_key": "path", "name": "literal"}}
    )["nested"] == {"authorization": "[REDACTED]", "storage_key": "[REDACTED]", "name": "literal"}
    package = evidence_package({"complete": True, "nodes": []})
    assert verify_package(package)
    package["evidence"]["nodes"].append("tampered")
    assert not verify_package(package)


@pytest.mark.parametrize("relative", ["../outside", ".", "../../.env"])
def test_recovery_path_guard(tmp_path, relative):
    with pytest.raises(ValueError):
        bounded(tmp_path, tmp_path / relative)


def test_g5_endpoint_permissions(client, settings):
    assert client.get("/v5/operations/metrics").status_code == 401
    assert (
        client.get("/v5/operations/metrics", headers=auth(settings, "auditor")).status_code == 200
    )
    assert (
        client.post("/v5/operations/collect", headers=auth(settings, "auditor")).status_code == 403
    )
    assert (
        client.post(
            f"/v5/operations/alerts/{uuid4()}/acknowledge",
            headers=auth(settings, "auditor"),
            json={"expected_version": 1, "reason": "cannot write"},
        ).status_code
        == 403
    )
    assert client.get("/v5/operations/alerts?limit=1000", headers=auth(settings)).status_code == 422
    assert client.get(f"/v5/audit/invalid/{uuid4()}", headers=auth(settings)).status_code == 422
    assert (
        client.post(f"/v5/audit/batch/{uuid4()}/export", headers=auth(settings)).status_code == 403
    )


def test_login_rate_shared_database(client, settings, monkeypatch):
    from fastapi import HTTPException

    from services.core.g5.login import login_allowed

    peer = str(uuid4())
    subject = "g5-" + str(uuid4())
    for _ in range(20):
        login_allowed(client.app.state.db, peer, subject)
    with pytest.raises(HTTPException) as exc:
        login_allowed(client.app.state.db, peer, subject)
    assert exc.value.status_code == 429
    # New Database object/process reads the same committed buckets.
    from services.core.db import Database

    other = Database(settings.database_url)
    other.pool.open()
    try:
        with pytest.raises(HTTPException):
            login_allowed(other, peer, subject)
    finally:
        other.pool.close()
