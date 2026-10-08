"""OpenClaw relay ACL adapter.

Implements the core `RelayService` by delegating to an injected
`OpenClawRelayPort`.  Relay is a near-passthrough: the adapter extracts
`auth.token`, calls the port, and returns the `ResponseFrame` / `None` as-is
(kernel types, so no DTO build is needed).
"""
from __future__ import annotations

import logging
from typing import Any

from engine.community.core.engine.context import AuthContext
from engine.community.core.relay.protocol import RelayService
from engine.community.kernel.frames import ResponseFrame
from engine.community.plugin_api.openclaw.relay import OpenClawRelayPort

log = logging.getLogger("openclaw-relay-adapter")


class OpenClawRelayAdapter(RelayService):
    """`RelayService` over the OpenClaw native port."""

    def __init__(self, port: OpenClawRelayPort) -> None:
        self._port = port

    async def forward_request(
        self,
        request_id: str,
        method: str,
        params: dict[str, Any] | None,
        auth: AuthContext | None = None,
        timeout: float = 30.0,
    ) -> ResponseFrame:
        """Forward an unknown `req` frame upstream and return the response."""
        token = auth.token if auth is not None else None
        log.debug(f"[forward_request] method={method} id={request_id}")
        return await self._port.forward_request(
            request_id=request_id,
            method=method,
            params=params,
            token=token,
            timeout=timeout,
        )

    async def forward_raw_frame(
        self,
        frame: dict[str, Any],
        auth: AuthContext | None = None,
    ) -> None:
        """Forward a raw (non-req) frame upstream."""
        token = auth.token if auth is not None else None
        log.debug(f"[forward_raw_frame] type={frame.get('type')}")
        await self._port.forward_raw_frame(frame=frame, token=token)


__all__ = ["OpenClawRelayAdapter"]
