"""Optional process-local external socket denial; never modifies host networking."""

import ipaddress
import os
import sys
from urllib.parse import parse_qs, urlparse

_installed = False


def loopback(host):
    if isinstance(host, bytes):
        host = host.decode("ascii", errors="replace")
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def install():
    global _installed
    if _installed or os.environ.get("G5_OFFLINE_ONLY") != "true":
        return
    for key, value in os.environ.items():
        if key.endswith(("DATABASE_URL", "MIGRATION_URL")) and value:
            parsed = urlparse(value)
            query = parse_qs(parsed.query)
            if (
                parsed.scheme not in {"postgresql", "postgres"}
                or not loopback(parsed.hostname)
                or parsed.port != 55432
                or any(not loopback(x) for x in query.get("hostaddr", []))
                or any(not loopback(x) for x in query.get("host", []))
            ):
                raise ValueError("OFFLINE_DATABASE_MUST_USE_PROJECT_LOOPBACK:" + key)

    def guard(event, args):
        if event == "socket.connect":
            address = args[1]
            if not isinstance(address, tuple) or not loopback(address[0]):
                raise PermissionError("G5_EXTERNAL_NETWORK_DISABLED")
        elif event == "socket.getaddrinfo" and args[0] is not None and not loopback(args[0]):
            raise PermissionError("G5_EXTERNAL_NETWORK_DISABLED")

    sys.addaudithook(guard)
    _installed = True
