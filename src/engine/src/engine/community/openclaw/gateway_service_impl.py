"""Shared OpenClawGatewayService implementation.

Both community and corp profiles use the same in-tree OpenClaw gateway client
and public config accessors. Keep the implementation in one neutral module and
let profile-specific DI modules bind this class to the OpenClawGatewayService
Protocol.
"""
from __future__ import annotations

from engine.community.openclaw.client.gateway_client import close_client, get_client
from engine.community.openclaw.config import get_config


class OpenClawGatewayServiceImpl:
    """Real wrapper over the shared OpenClaw gateway client + config."""

    async def test_connection(self) -> dict:
        config = get_config()

        try:
            client = await get_client()
            hello = client.hello

            return {
                "success": True,
                "connected": client.connected,
                "gateway_url": config.gateway_url,
                "server": {
                    "version": hello.server.version if hello else None,
                    "conn_id": hello.server.conn_id if hello else None,
                    "host": hello.server.host if hello else None,
                } if hello else None,
                "protocol": hello.protocol if hello else None,
                "features": {
                    "methods": hello.features.methods if hello else [],
                    "events": hello.features.events if hello else [],
                } if hello else None,
            }
        except Exception as e:
            return {
                "success": False,
                "connected": False,
                "gateway_url": config.gateway_url,
                "error": str(e),
            }

    async def disconnect(self) -> None:
        await close_client()

    def get_config(self) -> dict:
        config = get_config()
        return {
            "gateway_url": config.gateway_url,
            "connection_timeout": config.connection_timeout,
        }
