from __future__ import annotations

from injector import Module, provider, singleton

from engine.community.openclaw.gateway_service_impl import OpenClawGatewayServiceImpl
from engine.community.plugin_api.openclaw.gateway_service import OpenClawGatewayService


class OpenClawGatewayModule(Module):
    """Shared OpenClaw gateway DI binding used by all profiles."""

    @singleton
    @provider
    def openclaw_gateway(self) -> OpenClawGatewayService:
        return OpenClawGatewayServiceImpl()
