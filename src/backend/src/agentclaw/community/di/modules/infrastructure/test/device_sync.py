"""Device-sync dispatcher — test / singlebox binding.

Binds the **prod** ``ProdDeviceSyncDispatcher`` (same as corp): it is pure
provider routing/construction with no external service to stub, and the test
suite exercises the real baas / arca / teclaw branches (the per-provider sync
plugins are driven by test doubles / real-HTTP seams). The community column
instead binds the no-op ``CommunityDeviceSyncDispatcher``.
"""
from __future__ import annotations

from injector import Binder, Injector, Module, inject, provider, singleton

from agentclaw.community.core.devices.services.device_sync_dispatcher import (
    DeviceSyncDispatcher,
)
from agentclaw.community.core.service_bot.services.baas_service import BaasService


class TestDeviceSyncModule(Module):
    """test / singlebox: prod ``DeviceSyncDispatcher`` under the core seam key."""

    def configure(self, binder: Binder) -> None:  # noqa: D102 - provider below
        pass

    @singleton
    @provider
    @inject
    def device_sync_dispatcher(
        self,
        baas_service: BaasService,
        injector: Injector,
    ) -> DeviceSyncDispatcher:
        from agentclaw.community.core.config_compose.services.config_composer import (
            ConfigComposer,
        )
        from agentclaw.community.core.service_bot.services.bot_publish_service import (
            BotPublishService,
        )
        from agentclaw.corp.plugins.prod.device_sync import ProdDeviceSyncDispatcher

        return ProdDeviceSyncDispatcher(
            baas_service=baas_service,
            composer_provider=lambda: injector.get(ConfigComposer),
            publish_service_provider=lambda: injector.get(BotPublishService),
        )
