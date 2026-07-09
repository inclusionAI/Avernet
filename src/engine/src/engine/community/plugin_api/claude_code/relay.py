"""ClaudeCodeRelayPort — native port for transparent relay forwarding.

Relay is pooled (client + pool), so port methods take
``token: str | None = None`` for per-token routing. Returns raw dicts —
relay forwarding is a near-passthrough and the dict is a native shape that
crosses the boundary without violating the leaf rule.
"""
from __future__ import annotations

from typing import Protocol


class ClaudeCodeRelayPort(Protocol):
    """Native transparent relay forwarding over the claude_code gateway (vendored Node relay)."""

    async def relay_forward_request(
        self,
        method: str,
        params: dict | None = None,
        request_id: str | None = None,
        token: str | None = None,
    ) -> dict:
        """Forward an unknown ``req`` frame upstream transparently.

        Used for methods the engine does not natively handle — the frame is
        passed through to the relay and the raw response dict is returned.

        Args:
            method: The RPC method name on the wire.
            params: Optional params dict for the request.
            request_id: Optional request id; None -> relay generates one.
            token: MCP token for per-token pool routing; None -> default client.

        Returns:
            Raw response dict from the relay.
        """
        ...

    async def relay_forward_raw_frame(
        self,
        frame: dict,
        token: str | None = None,
    ) -> dict:
        """Forward a raw (non-req) frame upstream transparently.

        Used for event/notify frames that are not request/response — the
        frame is passed through and the raw relay ack dict is returned.

        Args:
            frame: Raw frame dict to forward.
            token: MCP token for per-token pool routing; None -> default client.

        Returns:
            Raw relay ack dict.
        """
        ...


__all__ = ["ClaudeCodeRelayPort"]
