"""Community claude_code WS binding — the generic engine-agnostic server."""
from __future__ import annotations

from fastapi import WebSocket
from injector import Module, provider, singleton

from engine.community.api.transport.claude_code_ws import ClaudeCodeWsServer
from engine.community.plugin_api.auth_gate.protocol import AuthGateService


class CommunityClaudeCodeWsServer:
    """Serves claude_code /ws via the generic ``EngineWebSocketServer``.

    The community engine is ACL-assembled over the generic dispatch, so there is
    no dedicated corp server here. ``auth_gate_service`` is forwarded because the
    generic server requires it.
    """

    async def handle_connection(
        self,
        websocket: WebSocket,
        *,
        auth_gate_service: AuthGateService | None = None,
    ) -> None:
        from engine.community.api.transport.ws_server import get_server

        await get_server().handle_connection(
            websocket, auth_gate_service=auth_gate_service
        )


class CommunityClaudeCodeWsModule(Module):
    @singleton
    @provider
    def claude_code_ws(self) -> ClaudeCodeWsServer:
        return CommunityClaudeCodeWsServer()
