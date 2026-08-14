"""EngineConfigModule — production singleton for the engine-config service.

``EngineConfigService.__init__`` uses ``@inject`` to receive ``BotRepository``,
``DeviceContextResolver`` and ``DeviceFilesystemDispatcher``; a ``configure``
self-binding is enough for the injector to construct it.

The ``EngineConfigService`` import is deferred to ``configure`` time so that loading
this module file does not eagerly pull ``service_bot``/``skill_center`` into the early
DI bootstrap (mirrors ``IdentityModule``).
"""
from __future__ import annotations

from injector import Binder, Injector, Module, inject, provider, singleton

from agentclaw.community.api.engine_config_service import EngineConfigServiceProtocol


class EngineConfigModule(Module):
    """Production bindings for the engine-config service."""

    def configure(self, binder: Binder) -> None:
        from agentclaw.community.core.services.engine_config import EngineConfigService

        binder.bind(EngineConfigService, to=EngineConfigService, scope=singleton)

    @singleton
    @provider
    @inject
    def _engine_config_service_protocol(
        self, injector: Injector
    ) -> EngineConfigServiceProtocol:
        """Serve the Service API contract HTTP adapters inject.

        Resolves the same singleton as the concrete binding above — this only
        gives adapters a name from ``api/`` to depend on instead of the core
        class. The import stays inside the method for the same reason
        ``configure`` defers it: naming the class at module scope would pull
        ``service_bot``/``skill_center`` into the early DI bootstrap.
        """
        from agentclaw.community.core.services.engine_config import EngineConfigService

        return injector.get(EngineConfigService)
