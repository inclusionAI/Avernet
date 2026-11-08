"""Load the paired image release at bootstrap and assemble the read-only service."""

import os
from functools import partial
from agentclaw.community.adapters.engine_release_probe import probe_engine_release

from injector import Module, provider, singleton
from agentclaw.community.core.bot_inventory.protocols import ServiceLifecyclePort
from agentclaw.community.api.baas_service import BaasServiceProtocol
from agentclaw.community.api.engine_version_service import EngineVersionServiceProtocol
from agentclaw.community.core.bot_collaborator.protocols import (
    CollaboratorServiceProtocol,
)
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.core.bot_management.engine_version.models import (
    EngineVersionConfig,
)
from agentclaw.community.core.bot_management.engine_version.service import (
    EngineVersionService,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotRepository,
)
from agentclaw.community.core.repository.protocols.devices import (
    DeviceBindingRepository,
)
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterTransport,
)


class EngineVersionModule(Module):
    @singleton
    @provider
    def config(self) -> EngineVersionConfig:
        # Absence remains unavailable; no guessed release or development default.
        return EngineVersionConfig(
            target_release=os.environ.get("TARGET_ENGINE_VERSION", "")
        )

    @singleton
    @provider
    def service(
        self,
        bots: BotRepository,
        bindings: DeviceBindingRepository,
        resolver: DeviceContextResolver,
        transport: DeviceAdapterTransport,
        collaborators: CollaboratorServiceProtocol,
        baas: BaasServiceProtocol,
        config: EngineVersionConfig,
        lifecycle: ServiceLifecyclePort,
    ) -> EngineVersionServiceProtocol:
        return EngineVersionService(
            bots=bots,
            bindings=bindings,
            resolver=resolver,
            probe=partial(probe_engine_release, transport),
            collaborators=collaborators,
            list_devices=partial(baas.list_devices_by_bot_uuid, timeout=5.0),
            config=config,
            lifecycle=lifecycle,
        )
