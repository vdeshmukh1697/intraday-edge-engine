"""Network transport hardening — IPv4-preferred DNS resolution.

Some networks (notably iPhone Personal Hotspot, 172.20.10.x) advertise IPv6 but have
dead v6 routing. Python's ``getaddrinfo`` returns AAAA records first, so connections to
dual-stack hosts (Dhan's CloudFront-backed endpoints) hang in SYN_SENT and freeze the
live feed / scheduler at startup — the 2026-07-13 outage.

:func:`prefer_ipv4` filters resolver results to IPv4 whenever any A record exists,
falling back to the full list for genuinely v6-only hosts. It is a no-op-shaped shim on
healthy networks (Dhan is reachable over v4 everywhere), so it is safe to enable always.
Disable with ``SE_PREFER_IPV4=0``.
"""

from __future__ import annotations

import os
import socket

_installed = False


def prefer_ipv4() -> bool:
    """Install the IPv4-first getaddrinfo shim once. Returns True if now active."""
    global _installed
    if _installed:
        return True
    if os.getenv("SE_PREFER_IPV4", "1") == "0":
        return False

    _orig = socket.getaddrinfo

    def _ipv4_first(host, port, family=0, *args, **kwargs):
        results = _orig(host, port, family, *args, **kwargs)
        v4 = [r for r in results if r[0] == socket.AF_INET]
        return v4 or results

    socket.getaddrinfo = _ipv4_first
    _installed = True
    return True
