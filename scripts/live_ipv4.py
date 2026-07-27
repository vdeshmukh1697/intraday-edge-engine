#!/usr/bin/env python
"""Launch the CLI with IPv4-preferred DNS resolution.

Workaround for a broken-IPv6 network (e.g. iPhone Personal Hotspot, 172.20.10.x):
Dhan hosts that publish AAAA records make Python try IPv6 first, and the SYN hangs
(SYN_SENT) because v6 routing is dead on the hotspot — freezing the live feed at
startup. This filters getaddrinfo to IPv4 results when any exist, falling back to
the original list for genuinely v6-only hosts. Purely a transport shim; no app logic.

Usage:  .venv/bin/python scripts/live_ipv4.py live --persist
"""
from __future__ import annotations

import socket
import sys

_orig_getaddrinfo = socket.getaddrinfo


def _ipv4_first(host, port, family=0, *args, **kwargs):
    results = _orig_getaddrinfo(host, port, family, *args, **kwargs)
    v4 = [r for r in results if r[0] == socket.AF_INET]
    return v4 or results  # prefer v4; keep v6-only hosts working


socket.getaddrinfo = _ipv4_first

if __name__ == "__main__":
    from signal_engine.cli import main

    sys.exit(main())
