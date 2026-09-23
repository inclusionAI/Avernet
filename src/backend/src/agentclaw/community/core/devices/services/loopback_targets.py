"""Loopback detection shared by singlebox device layers.

Singlebox routes locally-bound engine adapters directly and everything
else through the platform facade. Both the device-service projection
(``SingleboxBaasDeviceService``) and the local device-adapter transport
need the same answer for "is this device target loopback", so the
predicate lives here once instead of drifting per-copy.
"""
from __future__ import annotations

from urllib.parse import urlsplit


def is_loopback_target(target: str) -> bool:
    """True when ``target`` (``host:port``, bare host, or IPv6 forms)
    addresses a loopback host."""
    if not target:
        return False
    try:
        host = urlsplit(f"//{target}").hostname
    except ValueError:
        host = None
    if host is None and target.count(":") >= 2:
        host = target.rsplit(":", 1)[0]
        if host.startswith("[") and host.endswith("]"):
            host = host[1:-1]
    return host in {"localhost", "127.0.0.1", "::1"}
