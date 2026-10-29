import os
import subprocess
import sys

import pytest

from services.core.g5.offline import loopback


def test_loopback_addresses_only():
    assert loopback("127.0.0.1") and loopback("::1") and loopback("localhost")
    assert not loopback("example.com") and not loopback("192.168.1.1")


def test_external_socket_denied_before_dns_in_real_child():
    env = os.environ.copy()
    env["G5_OFFLINE_ONLY"] = "true"
    code = "from services.core.g5.offline import install; install(); import socket; socket.create_connection(('example.com',443),timeout=1)"
    result = subprocess.run(
        [sys.executable, "-s", "-c", code], env=env, capture_output=True, text=True, timeout=10
    )
    assert result.returncode != 0
    assert "G5_EXTERNAL_NETWORK_DISABLED" in result.stderr


@pytest.mark.parametrize(
    "url",
    ["host=8.8.8.8 dbname=unsafe", "postgresql://unused@127.0.0.1:55432/unused?hostaddr=8.8.8.8"],
)
def test_native_database_connections_cannot_bypass_offline_configuration(url):
    env = {**os.environ, "G5_OFFLINE_ONLY": "true", "DATABASE_URL": url}
    result = subprocess.run(
        [sys.executable, "-s", "-c", "from services.core.g5.offline import install; install()"],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert "OFFLINE_DATABASE_MUST_USE_PROJECT_LOOPBACK:DATABASE_URL" in result.stderr
