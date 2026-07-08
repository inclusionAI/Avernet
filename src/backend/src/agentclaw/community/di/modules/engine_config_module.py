"""EngineConfigModule — production singleton for the engine-config service.

``EngineConfigService.__init__`` uses ``@inject`` to receive ``BotRepository``,
``DeviceContextResolver`` and ``DeviceFilesystemDispatcher``; a ``configure``
self-binding is enough for the injector to construct it.

The ``EngineConfigService`` import is deferred to ``configure`` time so that loading
this module file does not eagerly pull ``service_bot``/``skill_center`` into the early
DI bootstrap (mirrors ``IdentityModule``).
"""
from __future__ import annotations

from injector import Binder, Module, singleton


class EngineConfigModule(Module):
    """Production bindings for the engine-config service."""

    def configure(self, binder: Binder) -> None:
        from agentclaw.community.core.services.engine_config import EngineConfigService

        binder.bind(EngineConfigService, to=EngineConfigService, scope=singleton)
