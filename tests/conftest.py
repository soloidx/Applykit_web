"""Suite-wide guards for the AI module tests.

CI must fail if the test process attempts an unapproved outbound network
connection. Connections to loopback (the local HTTP server and PostgreSQL
fixtures) remain allowed; everything else raises immediately.
"""

import ipaddress
import socket

import pytest


def _is_loopback(address: object) -> bool:
    if isinstance(address, tuple | list) and address:
        host = address[0]
    else:
        return True
    if not isinstance(host, str):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in ("localhost", "testserver")


@pytest.fixture(autouse=True)
def block_unapproved_outbound_connections(monkeypatch: pytest.MonkeyPatch):
    real_connect = socket.socket.connect

    def guarded_connect(self: socket.socket, address: object) -> object:
        if not _is_loopback(address):
            raise AssertionError(f"unapproved outbound connection to {address!r}")
        return real_connect(self, address)  # type: ignore[arg-type]

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
