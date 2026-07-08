"""OpenClawRelayPort — native port for transparent relay operations.

Relay is pooled (client+pool), so port methods take `token: str | None = None`
for per-token routing.  Returns `kernel.frames.ResponseFrame` directly — relay
is a near-passthrough and the frame is a kernel type (below both `core` and
`plugins`), so it crosses the boundary without violating the leaf rule.
"""
from __future__ import annotations

from typing import Any, Protocol

from engine.community.kernel.frames import ResponseFrame


class OpenClawRelayPort(Protocol):
    """Native relay operations over the OpenClaw gateway."""

    async def forward_request(
        self,
        request_id: str,
        method: str,
        params: dict[str, Any] | None = None,
        token: str | None = None,
        timeout: float = 30.0,
    ) -> ResponseFrame:
        """Forward an unknown `req` frame upstream and return the raw
        ResponseFrame.  Exceptions propagate to the caller."""
        ...

    async def forward_raw_frame(
        self,
        frame: dict[str, Any],
        token: str | None = None,
    ) -> None:
        """Forward a raw (non-req) frame upstream; no response is expected."""
        ...


__all__ = ["OpenClawRelayPort"]
