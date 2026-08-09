"""QualityModule — production singletons for the quality module.

Bindings:

- ``QualityTaskRepository`` — a single unified ORM implementation that
  runs on whichever ``DatabasePlugin`` is bound (ZDAS in prod, SQLite
  in local/test via ``TestingDatabaseModule``). No per-mode override.
- ``QualityTaskService`` — mode-agnostic ``@singleton`` self-binding;
  ``@inject`` resolves the repo via the injector.
- ``TaskProcessor`` — service for advancing task status.
"""
from __future__ import annotations

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.api.quality_service import QualityTaskServiceProtocol
from agentclaw.community.api.task_processor_service import TaskProcessorProtocol
from agentclaw.community.core.repository.protocols.platform import QualityTaskRepository
from agentclaw.community.core.quality.services.quality_task_service import QualityTaskService
from agentclaw.community.core.quality.services.task_processor import TaskProcessor
from agentclaw.community.plugins.quality_repository import (
    QualityTaskRepository as UnifiedQualityTaskRepository,
)


class QualityModule(Module):
    """Production bindings for the quality module."""

    def configure(self, binder: Binder) -> None:
        binder.bind(QualityTaskService, to=QualityTaskService, scope=singleton)
        binder.bind(TaskProcessor, to=TaskProcessor, scope=singleton)
        # Unified ORM repo (one body, ZDAS + SQLite). @inject ctor takes
        # the bound DatabasePlugin; prod vs test differ only by which
        # DatabasePlugin is bound (ZdasDB / SqliteDB).
        binder.bind(
            QualityTaskRepository, to=UnifiedQualityTaskRepository, scope=singleton
        )

    @singleton
    @provider
    @inject
    def _quality_task_service_protocol(
        self, svc: QualityTaskService
    ) -> QualityTaskServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _task_processor_protocol(self, svc: TaskProcessor) -> TaskProcessorProtocol:
        return svc
