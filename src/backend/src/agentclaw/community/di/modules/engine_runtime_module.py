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

The session-key codec registry is assembled here rather than in its own module
because selecting concrete implementations is the composition root's job: the
registry is the *mechanism* (an engine type to a wire form), and which engine
gets which codec is the decision. Today that is one entry — teclaw, whose proxy
refuses a session id's colons in a path segment — over a pass-through default
that leaves every other engine's ids exactly as they are.
"""

from __future__ import annotations

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.api.engine_connection_service import (
    EngineConnectionServiceProtocol,
)
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.core.engine_runtime.connection import EngineConnectionService
from agentclaw.community.core.engine_runtime.relay import EngineRuntimeRelay
from agentclaw.community.core.engine_runtime.session_key import (
    TECLAW_ENGINE_TYPE,
    Base64SessionKeyCodec,
    PassThroughSessionKeyCodec,
    SessionKeyCodecRegistry,
)
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
    def session_key_codec_registry(self) -> SessionKeyCodecRegistry:
        """The engine-to-wire-form registry the sessions handlers resolve from.

        Stateless codecs and a dict, built once per injector — a singleton so
        every handler resolves the same registrations rather than rebuilding
        them per request.
        """
        registry = SessionKeyCodecRegistry(PassThroughSessionKeyCodec())
        registry.register(TECLAW_ENGINE_TYPE, Base64SessionKeyCodec())
        return registry

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
