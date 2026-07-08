"""Device runtime — community bindings (BaaS-only, no ARCA).

The community distribution has no ARCA container runtime (that is the BaaS
team's, reached through the BaaS device provider), so the device *service* layer
is baas-only: no ``ArcaDeviceService``, no ``config_corp`` config, no ARCA
sandbox factory. The neutral pieces (repos, conn-info builders, the
device-filesystem resolver, the BaaS device service, ``NotifyBotLister``, the
rollout policy) come from the base ``DevicesModule`` installed for every profile;
this module only adds the community device accessor + a baas-only
``DeviceServiceRouter``.

Corp-free by construction: it imports only ``core`` + ``plugin_api``.
"""
from __future__ import annotations

from typing import cast  # noqa: UP035 - injector binding key must match provider side

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.core.bot_management.token_vault import TokenVault
from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.devices.protocols import BotQueryProtocol
from agentclaw.community.core.devices.repository.protocol import DeviceBindingRepository
from agentclaw.community.core.devices.services.baas_device_accessor import BaasDeviceAccessor
from agentclaw.community.core.devices.services.baas_device_service import BaasDeviceService
from agentclaw.community.core.devices.services.device_accessor import DeviceAccessor
from agentclaw.community.core.devices.services.device_service import (
    BAAS_DEVICE_PROVIDER,
    DeviceService,
)
from agentclaw.community.core.devices.services.device_service_router import DeviceServiceRouter
from agentclaw.community.core.service_bot.repository.bot_publish_repository import (
    BotPublishRepositoryProtocol,
)
from agentclaw.community.plugin_api.passport import PassportPlugin
from agentclaw.community.plugin_api.sandbox_runtime import SandboxRuntimeClient


class CommunityDevicesModule(Module):
    """community: BaaS-only device runtime wiring (no ARCA)."""

    def configure(self, binder: Binder) -> None:
        # Passthrough token vault: the community build has no Mist master key, so
        # ``encrypt`` returns plaintext. The base ``DevicesModule.baas_device_service``
        # @injects this ``TokenVault`` to build the BaaS device service.
        binder.bind(
            TokenVault,
            to=TokenVault(master_key=""),
            scope=singleton,
        )
        # The ``DeviceAccessor`` boundary → BaaS (community's only provider).
        # ``BaasDeviceAccessor`` has an ``@inject`` ctor (lazy BotService /
        # BaasService providers + WorkspacePathFactory), all base bindings.
        binder.bind(DeviceAccessor, to=BaasDeviceAccessor, scope=singleton)
        # No Moltis bot-to-bot gateway in the community build (it is a corp
        # runtime); bind the community noop so the ``DeviceConnectionManagerPlugin``
        # the approvals router injects resolves.
        from agentclaw.community.plugin_api.device_connection_manager import (
            DeviceConnectionManagerPlugin,
        )
        from agentclaw.community.plugins.community.device_connection_manager import (
            CommunityDeviceConnectionManager,
        )

        binder.bind(
            DeviceConnectionManagerPlugin,
            to=CommunityDeviceConnectionManager,
            scope=singleton,
        )

    @singleton
    @provider
    @inject
    def device_service(
        self,
        baas_device_service: BaasDeviceService,
        repository: DeviceBindingRepository,
        bot_repository: BotRepository,
        bot_publish_repo: BotPublishRepositoryProtocol,
        passport_plugin: PassportPlugin,
        sandbox_client: SandboxRuntimeClient,
    ) -> DeviceService:
        """BaaS-only ``DeviceServiceRouter`` — no ARCA provider, no corp config.

        Reuses the base ``BaasDeviceService`` (neutral deps + the passthrough
        vault bound above). Only the ``baas`` provider is registered and it is
        the default; historical binding/device-id lookups for an unknown
        provider fall back to that default. The *create-time* provider decision
        uses ``CommunityAllBaasRolloutPolicy`` (always BaaS) — NOT the corp
        ``ArcaBotCreateBaasRolloutPolicy`` (a DRM-driven ARCA→BaaS gate that,
        with no DRM center, always decides ``arca`` and would make the router
        raise ``unknown create provider 'arca'`` since ARCA isn't registered).
        No ``data_init_service_factory`` is threaded — community does not run the
        ARCA data-init trigger — so the service's ``_data_init_service_provider``
        stays ``None`` (``report_device_status`` handles ``None``).
        """
        from agentclaw.community.plugins.community.devices import CommunityAllBaasRolloutPolicy

        bot_query = cast(BotQueryProtocol, bot_repository)
        providers: dict[str, DeviceService] = {
            BAAS_DEVICE_PROVIDER: baas_device_service,
        }
        return DeviceServiceRouter(
            repository=repository,
            bot_query=bot_query,
            providers=providers,
            default_provider_key=BAAS_DEVICE_PROVIDER,
            arca_baas_rollout_policy=CommunityAllBaasRolloutPolicy(),
            passport_plugin=passport_plugin,
            sandbox_client=sandbox_client,
            publish_repo=bot_publish_repo,
            bot_repo=bot_repository,
        )
