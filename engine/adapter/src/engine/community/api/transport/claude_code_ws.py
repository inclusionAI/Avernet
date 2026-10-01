"""claude_code WS server port (delivery-layer Protocol).

The claude_code ``/api/claude_code/ws`` router is identical across profiles;
only the backing WS server differs (corp: the dedicated ``ClaudeCodeWSServer``;
community: the generic engine-agnostic ``EngineWebSocketServer``). That
divergence lives behind this port, bound per profile in DI — so the router
itself never branches on mode.

This port lives in the delivery layer (not ``plugin_api``/``core``) because a
WS handler intrinsically takes a starlette ``WebSocket``, and the constitution's
``no-http-in-core`` contract forbids HTTP-framework types in core/plugin_api.
"""
from __future__ import annotations

from typing import Protocol

from fastapi import WebSocket

from engine.community.plugin_api.auth_gate.protocol import AuthGateService


class ClaudeCodeWsServer(Protocol):
    """Handles one claude_code WebSocket client connection end to end."""

    async def handle_connection(
        self,
        websocket: WebSocket,
        *,
        auth_gate_service: AuthGateService | None = None,
    ) -> None:
        """Accept, handshake, and run the message loop for one client."""
        ...
