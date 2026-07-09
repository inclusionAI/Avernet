"""Engine-agnostic /ws router — thin guard + delegate to EngineWebSocketServer.

Only OpenClaw's /ws is mounted here unconditionally (both profiles use the
generic EngineWebSocketServer). The claude_code /ws is a single shared router
(``api/routers/claude_code_ws.py``) whose WS server is swapped via the
``ClaudeCodeWsServer`` port. AICoding is intentionally not mounted in this OSS
router surface.
"""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, WebSocket

from engine.community.api.transport.ws_server import get_server
from engine.community.di import Injected
from engine.community.plugin_api.auth_gate.protocol import AuthGateService

log = logging.getLogger("engine-ws-router")

router = APIRouter(tags=["ws"])


async def _handle(ws: WebSocket, engine_name: str,
                  auth_gate_service: AuthGateService) -> None:
    from engine.community.manager import EngineManager

    manager = EngineManager.get_instance()
    if manager.engine != engine_name:
        log.warning(
            "[ws] rejected connection: engine='%s' not active, current='%s'",
            engine_name,
            manager.engine,
        )
        await ws.accept()
        await ws.close(
            code=4001,
            reason=f"Engine '{engine_name}' not active, current: '{manager.engine}'",
        )
        return
    t_start = time.monotonic()
    log.info("[ws] %s connection accepted", engine_name)
    server = get_server()
    await server.handle_connection(ws, auth_gate_service=auth_gate_service)
    latency_ms = int((time.monotonic() - t_start) * 1000)
    log.info("[ws] %s closed: latency=%dms", engine_name, latency_ms)


@router.websocket("/api/openclaw/ws")
async def openclaw_ws(
    ws: WebSocket,
    auth_gate_service: AuthGateService = Injected(AuthGateService),
) -> None:
    await _handle(ws, "openclaw", auth_gate_service)


