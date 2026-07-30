"""EngineRuntimeModule — production singleton for the engine-runtime relay.

``EngineRuntimeRelay.__init__`` carries ``@inject`` and takes ``BotService``,
``DeviceContextResolver`` and ``DeviceAdapterTransport``, so a ``configure``
self-binding plus the Protocol alias is all that is needed.

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
