"""Shared claude_code WebSocket router — mounted on every profile.

Engine-guard + delegation only. The actual WS server is injected via the
``ClaudeCodeWsServer`` port (corp/community bind different impls in DI), so this
router carries no profile branching. Preserves the ``/api/claude_code/ws`` path,
the active-engine guard, and the 4001 close code from the previous per-profile
routers.
"""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, WebSocket

from engine.community.api.transport.claude_code_ws import ClaudeCodeWsServer
from engine.community.di import Injected
from engine.community.plugin_api.auth_gate.protocol import AuthGateService

log = logging.getLogger("claude-code-router")

router = APIRouter(prefix="/api/claude_code", tags=["claude_code"])


@router.websocket("/ws")
async def claude_code_ws(
    websocket: WebSocket,
    server: ClaudeCodeWsServer = Injected(ClaudeCodeWsServer),
    auth_gate_service: AuthGateService = Injected(AuthGateService),
) -> None:
    """claude_code WebSocket endpoint.

    Rejects with code 4001 when the active engine is not ``claude_code``
    (mirrors the ``/api/{engine}/ws`` guard), then delegates to the injected
    WS server.
    """
    from engine.community.manager import EngineManager

    manager = EngineManager.get_instance()
    if manager.engine != "claude_code":
        log.warning(
            "[ws] rejected connection: engine='claude_code' not active, current='%s'",
            manager.engine,
        )
        await websocket.accept()
        await websocket.close(
            code=4001,
            reason=f"Engine 'claude_code' not active, current: '{manager.engine}'",
        )
        return
    t_start = time.monotonic()
    log.info("[ws] claude_code websocket connection accepted")
    await server.handle_connection(websocket, auth_gate_service=auth_gate_service)
    latency_ms = int((time.monotonic() - t_start) * 1000)
    log.info("[ws] claude_code websocket connection closed: latency=%dms", latency_ms)
