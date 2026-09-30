"""AuthContext — cross-cutting auth principal for plugin calls.

The web layer constructs an `AuthContext` per request and passes it to every
plugin method it invokes. Plugins route per-token (upstream gateway connection
pooling, tenant isolation, etc.) by reading `auth.token`; plugins that don't
care ignore it.

`None` is a valid default — it represents "no auth known at this call site."
OpenClaw falls back to a shared module-level upstream client in that case.

Kept deliberately small. Future fields (user_id, roles, trace_id) can be added
without breaking the Protocol because plugin signatures accept `AuthContext`
by type, not by positional fields.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AuthContext:
    """Auth principal for a single plugin call.

    Currently carries the MCP token the frontend presented during its WS
    handshake (or None for HTTP paths that don't propagate auth today). Plugins
    use this to scope upstream connections to the right tenant.
    """

    token: str | None = None


__all__ = ["AuthContext"]
