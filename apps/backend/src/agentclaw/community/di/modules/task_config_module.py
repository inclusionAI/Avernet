"""TaskConfigModule — production singletons for ``task_config`` (spec 2026-11-02).

Mirrors ``SystemConfigModule``: the concrete repository + service are bound to
themselves (so ``@inject`` ctors resolve their already-bound deps), the
Service API Protocol is bound to the same service singleton via a
``@singleton @provider`` so HTTP routers resolve
``Injected(TaskConfigServiceProtocol)`` without naming the concrete service.
The repository Protocol is bound to the ORM repository (SQLite + OceanBase
parity via the injected ``DatabasePlugin``); no mode branching here.
"""

from __future__ import annotations

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.api.task.task_config_service import TaskConfigServiceProtocol
from agentclaw.community.core.task.task_config.repository import TaskConfigRepository
from agentclaw.community.core.task.task_config.repository_protocol import (
    TaskConfigRepositoryProtocol,
)
from agentclaw.community.core.task.task_config.service import TaskConfigService
from agentclaw.community.log import get_logger

logger = get_logger()


class TaskConfigModule(Module):
    """Production bindings for task_config."""

    def configure(self, binder: Binder) -> None:
        binder.bind(TaskConfigRepository, to=TaskConfigRepository, scope=singleton)
        binder.bind(
            TaskConfigRepositoryProtocol,
            to=TaskConfigRepository,
            scope=singleton,
        )
        # TaskConfigService has an @inject ctor taking TaskConfigRepositoryProtocol
        # (supplied above), so binding to itself resolves cleanly.
        binder.bind(TaskConfigService, to=TaskConfigService, scope=singleton)

    # Bind the Service API Protocol to the same singleton so HTTP routers (and
    # the two task surfaces — internal /api and public /openapi/v1) resolve
    # `Injected(TaskConfigServiceProtocol)` and share one service instance.
    @singleton
    @provider
    @inject
    def _task_config_service_protocol(
        self, svc: TaskConfigService
    ) -> TaskConfigServiceProtocol:
        return svc
