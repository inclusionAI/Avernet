"""ClaudeCode relay ACL adapter.

Implements the core `RelayService` by delegating to an injected
`ClaudeCodeRelayPort`. Relay is a near-passthrough: the adapter extracts
`auth.token`, calls the port, and returns the `ResponseFrame` (from the port)
or ``None`` (forward_raw_frame) as-is. Kernel types cross the boundary
unchanged so no DTO build is needed.

Divergence from OpenClaw's relay adapter
----------------------------------------
The OpenClaw port's ``forward_request`` accepts a ``timeout`` kwarg; the
claude_code port's signature does NOT expose ``timeout`` (it forwards via the
relay client which has its own timeout). The adapter accepts ``timeout`` per
the core Protocol but does not forward it to the port — matching the corp
impl which ignored it as well.
"""
from __future__ import annotations

import logging
from typing import Any

from engine.community.core.engine.context import AuthContext
from engine.community.core.relay.protocol import RelayService
from engine.community.kernel.frames import ResponseFrame
from engine.community.plugin_api.claude_code.relay import ClaudeCodeRelayPort

log = logging.getLogger("claude-code-relay-adapter")


class ClaudeCodeRelayAdapter(RelayService):
    """`RelayService` over the claude_code native relay port."""

    def __init__(self, port: ClaudeCodeRelayPort) -> None:
        self._port = port

    async def forward_request(
        self,
        request_id: str,
        method: str,
        params: dict[str, Any] | None,
        auth: AuthContext | None = None,
        timeout: float = 30.0,
    ) -> ResponseFrame:
        """Forward an unknown `req` frame upstream and return the response.

        The claude_code port returns a raw dict; we wrap it into a
        ``ResponseFrame`` so the caller sees the kernel type the Protocol
        promises.
        """
        token = auth.token if auth is not None else None
        log.debug("[forward_request] method=%s id=%s", method, request_id)
        raw = await self._port.relay_forward_request(
            method=method,
            params=params,
            request_id=request_id,
            token=token,
        )
        return ResponseFrame.from_dict(
            {
                "id": raw.get("id", request_id),
                "ok": bool(raw.get("ok", raw.get("success", False))),
                "payload": raw.get("payload"),
                "error": raw.get("error"),
                "type": raw.get("type", "res"),
            }
        )

    async def forward_raw_frame(
        self,
        frame: dict[str, Any],
        auth: AuthContext | None = None,
    ) -> None:
        """Forward a raw (non-req) frame upstream.

        The port returns a raw ack dict; the core Protocol expects ``None``
        so we discard it. (The corp impl likewise did not surface a return
        value — raw frames are fire-and-forget.)
        """
        token = auth.token if auth is not None else None
        log.debug("[forward_raw_frame] type=%s", frame.get("type"))
        await self._port.relay_forward_raw_frame(frame=frame, token=token)


__all__ = ["ClaudeCodeRelayAdapter"]
