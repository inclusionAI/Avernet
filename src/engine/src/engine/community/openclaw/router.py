"""
OpenClaw FastAPI 路由

Corp-only remnants of the OpenClaw delivery surface. The HTTP endpoints
(``/test-connection``, ``/disconnect``, ``/config``) and the ``/ws`` endpoint
have moved to the neutral ``engine.community.api.routers`` (openclaw_http_router /
ws_router) and are mounted unconditionally on every profile — their deps are
injected via the ``OpenClawGatewayService`` Protocol (Noop on community).

What remains here is ``/api/openclaw/client`` — a layer-4 WS透传代理 to the
local OpenClaw gateway (``ws://127.0.0.1:18789/client``). It has no neutral
replacement yet and is corp-only (the OSS export does not run a local
OpenClaw gateway). The route is still mounted on every profile; profile or
runtime differences must be handled by the proxy/service implementation rather
than by hiding the route.
"""

import logging

from fastapi import APIRouter, WebSocket

from engine.community.api.transport.openclaw_client_proxy import OpenClawClientProxy
from engine.community.di import Injected

log = logging.getLogger("openclaw-router")

router = APIRouter(prefix="/api/openclaw", tags=["openclaw"])


@router.websocket("/client")
async def websocket_proxy_endpoint(
    websocket: WebSocket,
    proxy: OpenClawClientProxy = Injected(OpenClawClientProxy),
):
    """
    WebSocket 四层透传代理端点

    将 /api/openclaw/client 的流量透传到 ws://127.0.0.1:18789/client，
    实现将本地服务暴露到外部网络。

    可通过环境变量 OPENCLAW_WS_PROXY_UPSTREAM 配置上游地址。
    """
    await proxy.handle_connection(websocket)
