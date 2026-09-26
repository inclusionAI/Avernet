"""Community OpenClaw /client WS proxy binding — unsupported implementation."""
from __future__ import annotations

from fastapi import WebSocket
from injector import Module, provider, singleton

from engine.community.api.transport.openclaw_client_proxy import OpenClawClientProxy


class CommunityOpenClawClientProxy:
    async def handle_connection(self, websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.close(code=4004, reason="OpenClaw client proxy is unavailable in this profile")


class CommunityOpenClawClientProxyModule(Module):
    @singleton
    @provider
    def openclaw_client_proxy(self) -> OpenClawClientProxy:
        return CommunityOpenClawClientProxy()
