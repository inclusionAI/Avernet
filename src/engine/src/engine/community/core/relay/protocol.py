"""
RelayService Protocol — optional transparent-passthrough to an engine's upstream.

The web layer advertises a fixed set of methods on its WebSocket handshake
(`chat.send`, `chat.abort`, `sessions.reset`, …). For every other inbound
method / raw frame the engine may want to offer transparent relay to its
upstream process — OpenClaw's gateway supports methods like `sessions.list`,
`chat.history`, `exec.approvals.get` that the server has no explicit handler
for, and simply forwards.

This Protocol formalises that escape hatch. Engines that offer passthrough
return a `RelayService` from `Engine.relay`; engines that don't return `None`
and the server 501s on unknown methods.

Two orthogonal operations:
  - `forward_request`: frontend sent `{type: "req", method: <x>, params: …}`
    with a method the server didn't recognise. Plugin forwards, returns the
    upstream ResponseFrame.
  - `forward_raw_frame`: frontend sent a frame whose `type` isn't `"req"`
    (e.g. a client-originated event). Plugin forwards it raw and returns
    nothing — there's no response frame.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from engine.community.core.engine.context import AuthContext
from engine.community.kernel.frames import ResponseFrame


@runtime_checkable
class RelayService(Protocol):
    """Transparent-relay plugin for engines that front an upstream process."""

    async def forward_request(
        self,
        request_id: str,
        method: str,
        params: dict[str, Any] | None,
        auth: AuthContext | None = None,
        timeout: float = 30.0,
    ) -> ResponseFrame:
        """Forward an unknown `req` frame upstream and return the response.

        Plugins should propagate `request_id` so the frontend can match it to
        its pending request table. Exceptions propagate to the caller — the
        server wraps them into an error ResponseFrame.
        """
        ...

    async def forward_raw_frame(
        self,
        frame: dict[str, Any],
        auth: AuthContext | None = None,
    ) -> None:
        """Forward a raw (non-req) frame upstream.

        No response shape is defined — if the upstream replies, it arrives as
        an event on the plugin's internal WebSocket and is dispatched through
        the normal event-listener chain.
        """
        ...


__all__ = ["RelayService"]
