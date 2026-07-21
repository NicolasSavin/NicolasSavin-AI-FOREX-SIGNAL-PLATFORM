from __future__ import annotations

import ipaddress
import socket
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def block_unexpected_network(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest):
    if request.node.get_closest_marker("external"):
        return
    original_connect = socket.socket.connect

    def guarded_connect(self: socket.socket, address: Any):
        host = address[0] if isinstance(address, tuple) and address else address
        try:
            ip = ipaddress.ip_address(str(host))
            if ip.is_loopback or ip.is_private or ip.is_link_local:
                return original_connect(self, address)
        except ValueError:
            if host in {"localhost"}:
                return original_connect(self, address)
        raise AssertionError(f"Unexpected public outbound network access during deterministic tests: {address!r}")

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
