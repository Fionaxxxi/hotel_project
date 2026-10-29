import os
from urllib.parse import quote

import httpx

from infra.manage import uid
from services.core.g1.common import REQUEST_TRACE, fail
from services.core.g3.contract import PROFILES


def headers(tenant, channel):
    label = next((x for x in ["A", "B"] if str(uid("tenant-" + x)) == str(tenant)), None)
    if not label or channel not in PROFILES:
        fail("CHANNEL_ACCOUNT_NOT_CONFIGURED")
    return {
        "Authorization": "Bearer "
        + os.environ[f"G3_{channel.replace('-', '_')}_TENANT_{label}_TOKEN"]
    }


def request(tenant, channel, method, path, body=None, key=None):
    port = PROFILES[channel]["port"]
    if os.environ.get("G5_ISOLATED_SIMULATORS") == "true":
        from urllib.parse import urlparse

        if not urlparse(os.environ.get("DATABASE_URL", "")).path.startswith(
            "/hotel_project_g5_restore_"
        ):
            fail("ISOLATED_SIMULATORS_REQUIRE_RESTORE_DATABASE", 503)
        port = {"SIM-A": 8006, "SIM-B": 8007}[channel]
    # Registry owned by the server; never follow redirects or accept user endpoints.
    with httpx.Client(
        base_url=f"http://127.0.0.1:{port}",
        trust_env=False,
        follow_redirects=False,
        timeout=0.7,
    ) as h:
        auth = headers(tenant, channel)
        if REQUEST_TRACE.get():
            auth["X-Correlation-ID"] = str(REQUEST_TRACE.get())
        if key:
            auth["Idempotency-Key"] = key
        return h.request(method, path, headers=auth, json=body)


def listing_path(key):
    return "/g3/listings/" + quote(key, safe="")
