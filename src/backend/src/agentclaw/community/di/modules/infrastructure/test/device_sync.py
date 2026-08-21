"""Device-sync dispatcher — test / corp_test binding.

Binds the **prod** selection-only ``ProdDeviceSyncDispatcher`` for ``corp_test``:
it holds DI-injected ``Callable[[DeviceContext], DeviceSync]`` service factories
that construct the Corp Core services (Arca/BaaS/Teclaw) over the already-bound
no-network ``Annotated[HttpClient, QUALIFIER_GENERAL]`` test double. The
community column instead binds the no-op ``CommunityDeviceSyncDispatcher``.

``DeployProfile.TEST`` uses ``CommunityDeviceSyncModule`` (NOT this module); this
module serves ``corp_test`` only.
"""
from __future__ import annotations

from typing import Annotated

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

        def composer_provider() -> ConfigComposer:
            return injector.get(ConfigComposer)

        def publish_service_provider() -> BotPublishService:
            return injector.get(BotPublishService)

        def arca_service_factory(ctx: DeviceContext) -> DeviceSync:
            conn_info = ctx.conn_info
            engine_type = conn_info.get("engine_type") or "openclaw"
            return ArcaDeviceSyncService(
                conn_info=conn_info,
                engine_type=engine_type,
                http_client=general_http_client,
                mcp_transport=general_http_client,
            )

        def baas_service_factory(ctx: DeviceContext) -> DeviceSync:
            # corp_test exercises the real Core service with the injected
            # no-network HttpClient as its transport. Production transport
            # selection remains in CorpDeviceSyncModule.
            return BaasDeviceSyncService(
                transport=general_http_client,
                conn_info=ctx.conn_info,
            )

        def teclaw_service_factory(ctx: DeviceContext) -> DeviceSync:
            conn_info = ctx.conn_info
            engine_type = conn_info.get("engine_type") or "openclaw"
            # corp_test has no BotRepository binding for this factory, so use
            # the stable fallback name expected by config composition.
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