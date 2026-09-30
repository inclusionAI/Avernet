"""OpenClaw /client WS proxy port (delivery-layer Protocol)."""
from __future__ import annotations

from typing import Protocol

from fastapi import WebSocket


class OpenClawClientProxy(Protocol):
    """Handles one /api/openclaw/client proxy WebSocket connection."""

    async def handle_connection(self, websocket: WebSocket) -> None:
        """Proxy the client WebSocket to the configured OpenClaw upstream."""
        ...
