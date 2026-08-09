"""ResourcesModule — production singletons + factory for the resources module.

Bindings:

- ``ResourceRepositoryProtocol`` — single unified ORM implementation
  that runs on whichever ``DatabasePlugin`` is bound (ZDAS in prod,
  SQLite in local/test via ``TestingDatabaseModule``). No per-mode
  override.
- ``ResourceServiceFactory`` — ``@singleton`` holding the injected
  repository. ``create(bot_id)`` mints a per-bot
  :class:`ResourceService` that shares the singleton repo. Mirrors
  the established factory pattern from skill_center / aicoding.

The legacy ``get_legacy_resource_service`` and ``get_file_service``
helpers in ``dependencies/resource.py`` are not migrated here — they
wrap legacy ``services/openclawserver/`` paths and live transitionally
inside the dependencies module. Task 23 cleanup decides their fate
once the legacy code is fully gone.
"""
from __future__ import annotations

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.api.resource_service import ResourceServiceFactoryProtocol
from agentclaw.community.core.resources.factory import ResourceServiceFactory
from agentclaw.community.core.repository.protocols.platform import ResourceRepositoryProtocol
from agentclaw.community.log import get_logger
from agentclaw.community.core.repository.implementations.platform.resource import ResourceRepository as UnifiedResourceRepository


logger = get_logger()


class ResourcesModule(Module):
    """Production bindings for the resources module."""

    def configure(self, binder: Binder) -> None:
        binder.bind(
            ResourceServiceFactory, to=ResourceServiceFactory, scope=singleton
        )
        # Provider-agnostic workspace-namespace file service (mirrors IdentityModule's
        # self-binding). Its @inject ctor pulls path_factory + repos + resolver +
        # device-fs dispatcher; deferred import keeps module load cheap and avoids the
        # service_bot import-cycle at bootstrap.
        from agentclaw.community.core.services.resource_file_service import (
            ResourceFileService,
        )
        binder.bind(ResourceFileService, to=ResourceFileService, scope=singleton)
        # Unified ORM repo (one body, ZDAS + SQLite). @inject ctor takes
        # the bound DatabasePlugin; prod vs test differ only by which
        # DatabasePlugin is bound (ZdasDB / SqliteDB).
        binder.bind(
            ResourceRepositoryProtocol,
            to=UnifiedResourceRepository,
            scope=singleton,
        )

    @singleton
    @provider
    @inject
    def _resource_service_factory_protocol(
        self, svc: ResourceServiceFactory
    ) -> ResourceServiceFactoryProtocol:
        return svc
