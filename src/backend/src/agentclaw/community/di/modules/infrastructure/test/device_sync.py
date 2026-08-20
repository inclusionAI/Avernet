"""Device-sync dispatcher — test / corp_test binding.

Binds the **prod** selection-only ``ProdDeviceSyncDispatcher`` for ``corp_test``:
it holds DI-injected ``Callable[[DeviceContext], DeviceSync]`` service factories
that construct the Corp Core services (Arca/BaaS/Teclaw) over the already-bound
``Annotated[HttpClient, QUALIFIER_GENERAL]`` (``LocalHttpClient`` no-network in
corp_test) and the existing doubles. The community column instead binds the
no-op ``CommunityDeviceSyncDispatcher`` via ``CommunityDeviceSyncModule``.

``DeployProfile.TEST`` uses ``CommunityDeviceSyncModule`` (NOT this module); this
module serves ``corp_test`` only.
"""
from __future__ import annotations

from typing import Annotated, Callable

from injector import Binder, Injector, Module, inject, provider, singleton

from agentclaw.community.core.devices.services.device_context import DeviceContext
from agentclaw.community.core.devices.services.device_sync import DeviceSync
from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.plugin_api.device_sync_dispatcher import DeviceSyncDispatcher
from agentclaw.community.plugin_api.http_client import QUALIFIER_GENERAL, HttpClient


class TestDeviceSyncModule(Module):
    """corp_test: prod ``DeviceSyncDispatcher`` (selection-only) under the plugin_api seam key."""

    def configure(self, binder: Binder) -> None:  # noqa: D102 - provider below
        pass

    @singleton
    @provider
    @inject
    def device_sync_dispatcher(
        self,
        baas_service: BaasService,
        injector: Injector,
        general_http_client: Annotated[HttpClient, QUALIFIER_GENERAL],
    ) -> DeviceSyncDispatcher:
        from agentclaw.community.core.config_compose.services.config_composer import (
            ConfigComposer,
        )
        from agentclaw.community.core.devices.services.baas_invoke_transport import (
            BaasInvokeTransport,
            DesktopBaasInvokeTransport,
        )
        from agentclaw.community.core.service_bot.services.bot_publish_service import (
            BotPublishService,
        )
        from agentclaw.corp.core.devices.services.arca_device_sync import (
            ArcaDeviceSyncService,
        )
        from agentclaw.corp.core.devices.services.baas_device_sync import (
            BaasDeviceSyncService,
        )
        from agentclaw.corp.core.devices.services.teclaw_device_sync import (
            TeclawDeviceSyncService,
        )
        from agentclaw.corp.plugins.prod.device_sync_dispatcher import (
            ProdDeviceSyncDispatcher,
        )
        from agentclaw.corp.plugins.prod.mcp_device_transport import (
            HttpxBaseUrlTransport,
        )

        composer_provider: Callable[[], ConfigComposer] = lambda: injector.get(
            ConfigComposer
        )
        publish_service_provider: Callable[[], BotPublishService] = lambda: injector.get(
            BotPublishService
        )

        def arca_service_factory(ctx: DeviceContext) -> DeviceSync:
            conn_info = ctx.conn_info
            engine_type = conn_info.get("engine_type") or "openclaw"
            mcp_headers = dict(conn_info.get("headers") or {})
            mcp_transport = HttpxBaseUrlTransport(conn_info["url"], mcp_headers)
            return ArcaDeviceSyncService(
                conn_info=conn_info,
                engine_type=engine_type,
                http_client=general_http_client,
                mcp_transport=mcp_transport,
            )

        def baas_service_factory(ctx: DeviceContext) -> DeviceSync:
            conn_info = ctx.conn_info
            bot_type = ctx.bot_type
            engine_port = conn_info["engine_port"]
            tenant = conn_info.get("tenant", "team_claw")
            if bot_type == "desktop":
                transport = DesktopBaasInvokeTransport(
                    baas_base_url=conn_info["baas_base_url"],
                    tenant=tenant,
                    bot_uuid=conn_info["paas_device_id"],
                    engine_port=engine_port,
                    headers=conn_info.get("headers", {}),
                )
            else:
                transport = BaasInvokeTransport(
                    bind_id=conn_info["bind_id"],
                    engine_port=engine_port,
                    tenant=tenant,
                    baas_service=baas_service,
                )
            return BaasDeviceSyncService(transport=transport, conn_info=conn_info)

        def teclaw_service_factory(ctx: DeviceContext) -> DeviceSync:
            conn_info = ctx.conn_info
            engine_type = conn_info.get("engine_type") or "openclaw"
            # corp_test has no bot_repository — default bot_name (matches the old
            # ``ProdDeviceSyncDispatcher`` teclaw branch with bot_repository=None).
            return TeclawDeviceSyncService(
                conn_info=conn_info,
                bot_id=ctx.bot_id,
                bot_name="default",
                user_id=ctx.user_id,
                owner_id=None,
                engine_type=engine_type,
                composer_provider=composer_provider,
                baas_service=baas_service,
                http_client=general_http_client,
                draft_recorder=publish_service_provider,
            )

        return ProdDeviceSyncDispatcher(
            arca_service_factory=arca_service_factory,
            baas_service_factory=baas_service_factory,
            teclaw_service_factory=teclaw_service_factory,
        )