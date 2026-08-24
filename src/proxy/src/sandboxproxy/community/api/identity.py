"""Runtime identity helpers — instance id and worker pid for relay route writes."""

from __future__ import annotations

import os
import socket

from sandboxproxy.community.logger import get_logger

logger = get_logger("identity")


def _outbound_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return str(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        return "127.0.0.1"


def resolve_instance_id() -> str:
    """Resolve instance identity via env → local outbound IP → loopback."""
    for key in ("CONNECTED_SERVER_INSTANCE", "INSTANCE_ID", "HOSTNAME"):
        val = os.getenv(key, "").strip()
        if val:
            return val
    return _outbound_ip()


def resolve_worker_pid() -> int:
    return os.getpid()
