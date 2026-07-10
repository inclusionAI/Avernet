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
from agentclaw.community.core.files.factory import BotFileServiceFactory
from agentclaw.community.core.files.repository.protocol import FileRepositoryProtocol
from agentclaw.community.core.resources.factory import ResourceServiceFactory
from agentclaw.community.core.resources.repository.protocol import ResourceRepositoryProtocol
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugins.file_repository import FileRepository
from agentclaw.community.plugins.resource_repository import (
    ResourceRepository as UnifiedResourceRepository,
)
from agentclaw.community.utils.singlebox_coverage_proxy import (
    wrap_for_singlebox_coverage,
)


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
        # Teclaw workspace-file metadata (ac_file): same unified-ORM pattern.
        binder.bind(
            FileRepositoryProtocol, to=FileRepository, scope=singleton
        )
        binder.bind(
            BotFileServiceFactory, to=BotFileServiceFactory, scope=singleton
        )

    @singleton
    @provider
    @inject
    def resource_repository(
        self, db: DatabasePlugin
    ) -> ResourceRepositoryProtocol:
        """Expose resource persistence evidence at the DI boundary."""
        return wrap_for_singlebox_coverage(
            UnifiedResourceRepository(db),
            {
                "create": "ResourceRepository.create",
                "list_resources": "ResourceRepository.list_resources",
                "get_by_id": "ResourceRepository.get_by_id",
                "update": "ResourceRepository.update",
                "delete": "ResourceRepository.delete",
            },
        )

    @singleton
    @provider
    @inject
    def _resource_service_factory_protocol(
        self, svc: ResourceServiceFactory
    ) -> ResourceServiceFactoryProtocol:
        return svc
