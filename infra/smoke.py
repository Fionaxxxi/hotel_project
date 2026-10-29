"""Real HTTP smoke test against the locally running API; never prints credentials."""

import os

import httpx
from dotenv import load_dotenv

from infra.manage import ROOT, uid
from services.core.version import SCHEMA_VERSION

load_dotenv(ROOT / ".env")
with httpx.Client(base_url="http://127.0.0.1:8000", timeout=10, trust_env=False) as client:
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").json()["migration"] == SCHEMA_VERSION
    assert client.get("/v1/suppliers").status_code == 401
    assert client.get("/openapi.json").status_code == 200
    for label, expected in [("A", 2), ("B", 1)]:
        response = client.post(
            "/dev/token",
            json={
                "subject": f"{label.lower()}.operator",
                "password": os.environ["DEV_LOGIN_PASSWORD"],
            },
        )
        assert response.status_code == 200
        headers = {
            "Authorization": "Bearer " + response.json()["access_token"],
            "X-Tenant-ID": str(uid(f"tenant-{label}")),
        }
        items = client.get("/v1/suppliers", headers=headers).json()["items"]
        assert len(items) == expected
        other = "B" if label == "A" else "A"
        assert (
            client.get(f"/v1/properties/{uid(f'property-{other}')}", headers=headers).status_code
            == 404
        )
print("HTTP smoke passed: health, OpenAPI, login, two tenants, cross-tenant denial")
