"""EngineRuntimeModule — production singleton for the engine-runtime relay.

``EngineRuntimeRelay.__init__`` carries ``@inject`` and takes ``BotService``,
``DeviceContextResolver``, ``DeviceAdapterTransport`` and
``BotPublishRepositoryProtocol`` (a service bot's published runtime binding),
so a ``configure`` self-binding plus the Protocol alias is all that is needed.
The publish repository is bound database-mode-keyed by ``ServiceBotModule``,
the same binding ``CronRelayService`` resolves.

``DeviceAdapterTransport`` is bound per-profile by the device column, not here:
corp binds the HTTP transport, test binds the in-memory one, and community
leaves it as the no-op (no container runtime). Same arrangement
``CronRelayService`` already relies on.
"""

from __future__ import annotations

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.api.engine_connection_service import (
    EngineConnectionServiceProtocol,
)
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.core.engine_runtime.connection import EngineConnectionService
from agentclaw.community.core.engine_runtime.relay import EngineRuntimeRelay
from agentclaw.community.core.runtime_binding.service import RuntimeBindingResolutionService
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.devices import DeviceBindingRepository
from agentclaw.community.core.repository.protocols.publishing import (
    BotPublishRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.chat import (
    ExpertChatInstanceRepository,
)


class EngineRuntimeModule(Module):
    """Production bindings for the engine-runtime relay."""

    def configure(self, binder: Binder) -> None:
        binder.bind(EngineRuntimeRelay, to=EngineRuntimeRelay, scope=singleton)
        binder.bind(
            EngineConnectionService, to=EngineConnectionService, scope=singleton
        )

    @singleton
    @provider
    @inject
    def runtime_binding_resolution_service(
        self,
        bot_repository: BotRepository,
        publish_repository: BotPublishRepositoryProtocol,
        binding_repository: DeviceBindingRepository,
        caller_instance_repository: ExpertChatInstanceRepository,
    ) -> RuntimeBindingResolutionService:
        return RuntimeBindingResolutionService(
            bot_repository=bot_repository,
            publish_repository=publish_repository,
            binding_repository=binding_repository,
            caller_instance_repository=caller_instance_repository,
        )

    @singleton
    @provider
    @inject
    def _engine_runtime_relay_protocol(
        self, svc: EngineRuntimeRelay
    ) -> EngineRuntimeRelayProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _engine_connection_service_protocol(
        self, svc: EngineConnectionService
    ) -> EngineConnectionServiceProtocol:
        return svc
