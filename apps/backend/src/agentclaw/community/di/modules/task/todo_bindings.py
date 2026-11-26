"""Task-owned dependency-injection bindings for the Todo workbench."""

from __future__ import annotations

from dataclasses import fields
import logging
from typing import Any

from injector import Binder, Injector, Module, inject, provider, singleton

from agentclaw.community.core.repository.implementations.task.todos import (
    TodoArtifactRepository,
    TodoAutoImportRepository,
    TodoRepository,
    TodoTemplateRepository,
)
from agentclaw.community.core.repository.protocols.task.todo_artifacts import (
    TodoArtifactRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.task.todo_auto_imports import (
    TodoAutoImportRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.task.todos import (
    TodoRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.task.todo_templates import (
    TodoTemplateRepositoryProtocol,
)
from agentclaw.community.core.spaces.protocols import SpaceAccessServiceProtocol
from agentclaw.community.core.task.context_collab.executor import (
    ContextCollabExecutor,
    UnavailableContextCollabExecutor,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.repository import (
    ContextCollabRepositoryProtocol,
)
from agentclaw.community.core.task.task_runner.client.ports import (
    OpenApiBotPort,
    BcsClientPort,
)
from agentclaw.community.core.task.task_runner.client.bcs_bot_token_provider import (
    BcsBotTokenProvider,
    NullBcsBotTokenProvider,
)
from agentclaw.community.core.task.todos.collaboration.context_runtime import (
    TodoContextRuntime,
)
from agentclaw.community.di.config import TaskDispatchConfig
from agentclaw.community.core.task.context_collab.service_api import (
    ContextCollabServiceProtocol,
)
from agentclaw.community.core.task.task_context.task_artifact.artifact_object_storage import (
    TaskArtifactObjectStoragePlugin,
)
from agentclaw.community.core.task.task_context.task_artifact.artifact_object_storage_config import (
    TaskArtifactStorageConfig,
)
from agentclaw.community.core.task.todos.artifact_service_protocol import (
    TodoArtifactServiceProtocol,
)
from agentclaw.community.core.task.todos.auto_import_service_protocol import (
    TodoAutoImportServiceProtocol,
)
from agentclaw.community.core.task.todos.collaboration.in_process import (
    InProcessTodoCollaborationAdapter,
)
from agentclaw.community.core.task.todos.collaboration.protocol import (
    TodoCollaborationPort,
)
from agentclaw.community.core.task.todos.execution_protocol import (
    TodoExecutionServiceProtocol,
)
from agentclaw.community.core.task.todos.scheduler import TodoScheduler
from agentclaw.community.core.task.todos.stage_outcome_protocol import (
    TodoStageServiceProtocol,
)
from agentclaw.community.core.task.todos.service_protocol import TodoServiceProtocol
from agentclaw.community.core.task.todos.source_client_protocol import (
    TodoSourceClientProtocol,
)
from agentclaw.community.core.task.todos.source_service_protocol import (
    TodoSourceServiceProtocol,
)
from agentclaw.community.core.task.todos.template_capability_protocol import (
    TodoTemplateCapabilityServiceProtocol,
)
from agentclaw.community.core.task.todos.template_creation_protocol import (
    TodoTemplateCreationServiceProtocol,
)
from agentclaw.community.core.task.todos.template_management_protocol import (
    TodoTemplateManagementServiceProtocol,
)
from agentclaw.community.core.task.todos.services.dima_source_client import (
    DimaSourceClient,
)
from agentclaw.community.core.task.todos.services.todo_artifact_service import (
    TodoArtifactService,
)
from agentclaw.community.core.task.todos.services.todo_auto_import_service import (
    TodoAutoImportService,
)
from agentclaw.community.core.task.todos.services.todo_execution_service import (
    TodoExecutionService,
)
from agentclaw.community.core.task.todos.services.todo_service import TodoService
from agentclaw.community.core.task.todos.services.todo_source_service import (
    TodoSourceService,
)
from agentclaw.community.core.task.todos.services.todo_stage_service import (
    TodoStageService,
)
from agentclaw.community.core.task.todos.services.todo_template_capability_service import (
    TodoTemplateCapabilityService,
)
from agentclaw.community.core.task.todos.services.todo_template_creation_service import (
    TodoTemplateCreationService,
)
from agentclaw.community.core.task.todos.services.todo_template_service import (
    TodoTemplateService,
)
from agentclaw.community.di.modules.config_module import _as_bool, _as_int, _block
from agentclaw.community.di.todo_config import TodoSchedulerConfig
from agentclaw.community.plugin_api.cache import CachePlugin

logger = logging.getLogger("task.todo")

_TODO_KEYS = frozenset({"scheduler"})
_SCHEDULER_KEYS = frozenset(field.name for field in fields(TodoSchedulerConfig))


class TaskTodoBindings(Module):
    """Compose Todo persistence, services, runtime adapter, and scheduler."""

    def configure(self, binder: Binder) -> None:
        binder.bind(TodoRepositoryProtocol, to=TodoRepository, scope=singleton)
        binder.bind(
            TodoAutoImportRepositoryProtocol,
            to=TodoAutoImportRepository,
            scope=singleton,
        )
        binder.bind(
            TodoTemplateRepositoryProtocol,
            to=TodoTemplateRepository,
            scope=singleton,
        )
        binder.bind(
            TodoArtifactRepositoryProtocol,
            to=TodoArtifactRepository,
            scope=singleton,
        )
        binder.bind(TodoServiceProtocol, to=TodoService, scope=singleton)
        binder.bind(
            TodoAutoImportServiceProtocol,
            to=TodoAutoImportService,
            scope=singleton,
        )
        binder.bind(TodoStageServiceProtocol, to=TodoStageService, scope=singleton)
        binder.bind(
            TodoTemplateCapabilityServiceProtocol,
            to=TodoTemplateCapabilityService,
            scope=singleton,
        )
        binder.bind(
            TodoTemplateManagementServiceProtocol,
            to=TodoTemplateService,
            scope=singleton,
        )
        binder.bind(
            TodoTemplateCreationServiceProtocol,
            to=TodoTemplateCreationService,
            scope=singleton,
        )
        binder.bind(
            TodoSourceClientProtocol,
            to=DimaSourceClient,
            scope=singleton,
        )
        binder.bind(
            TodoSourceServiceProtocol,
            to=TodoSourceService,
            scope=singleton,
        )
        binder.bind(
            TodoExecutionServiceProtocol,
            to=TodoExecutionService,
            scope=singleton,
        )

    @singleton
    @provider
    @inject
    def context_collab_executor(
        self,
        injector: Injector,
        task_dispatch: TaskDispatchConfig,
    ) -> ContextCollabExecutor:
        """Build direct Todo execution with optional legacy BCS compatibility."""
        try:
            openapi_bot = injector.get(OpenApiBotPort)
        except Exception as exc:  # noqa: BLE001 lightweight deployment
            logger.info(
                "[task][context-collab] OpenApiBotPort unavailable: %s",
                exc,
            )
            return UnavailableContextCollabExecutor(str(exc))
        if openapi_bot is None:
            return UnavailableContextCollabExecutor("OpenApiBotPort resolved to None")
        try:
            bcs = injector.get(BcsClientPort)
        except Exception as exc:  # noqa: BLE001 lightweight deployment
            logger.info(
                "[task][context-collab] BcsClientPort unavailable; "
                "legacy group-backed Todo resume disabled: %s",
                exc,
            )
            bcs = None
        try:
            bot_tokens = injector.get(BcsBotTokenProvider)
        except Exception as exc:  # noqa: BLE001 lightweight deployment
            logger.info(
                "[task][context-collab] BcsBotTokenProvider unavailable; "
                "legacy group-backed Todo resume disabled: %s",
                exc,
            )
            bot_tokens = NullBcsBotTokenProvider()
        return ContextCollabExecutor(
            openapi_bot,
            bcs=bcs,
            bot_tokens=bot_tokens,
            backend_url=task_dispatch.backend_url,
        )

    @singleton
    @provider
    def todo_scheduler_config(self) -> TodoSchedulerConfig:
        default = TodoSchedulerConfig()
        todo = _mapping(_block("todo"), where="todo")
        _reject_unknown(todo, where="todo", allowed=_TODO_KEYS)
        scheduler = _mapping(todo.get("scheduler", {}), where="todo.scheduler")
        _reject_unknown(
            scheduler,
            where="todo.scheduler",
            allowed=_SCHEDULER_KEYS,
        )

        config = TodoSchedulerConfig(
            enabled=_bool(
                scheduler,
                "enabled",
                default.enabled,
                "todo.scheduler",
            ),
            interval=_bounded_int(
                scheduler,
                "interval",
                default.interval,
                "todo.scheduler",
                minimum=1,
            ),
            batch_size=_bounded_int(
                scheduler,
                "batch_size",
                default.batch_size,
                "todo.scheduler",
                minimum=1,
            ),
            jitter=_bounded_int(
                scheduler,
                "jitter",
                default.jitter,
                "todo.scheduler",
                minimum=0,
            ),
            lock_ttl=_bounded_int(
                scheduler,
                "lock_ttl",
                default.lock_ttl,
                "todo.scheduler",
                minimum=1,
            ),
            heartbeat=_bounded_int(
                scheduler,
                "heartbeat",
                default.heartbeat,
                "todo.scheduler",
                minimum=1,
            ),
        )
        if config.heartbeat >= config.lock_ttl:
            raise ValueError(
                "todo.scheduler.heartbeat must be less than lock_ttl "
                f"({config.heartbeat} >= {config.lock_ttl})"
            )
        return config

    @singleton
    @provider
    @inject
    def todo_artifact_service(
        self,
        repository: TodoRepositoryProtocol,
        artifact_repository: TodoArtifactRepositoryProtocol,
        space_access: SpaceAccessServiceProtocol,
        collaboration: TodoCollaborationPort,
        storage_config: TaskArtifactStorageConfig,
        injector: Injector,
    ) -> TodoArtifactServiceProtocol:
        storage: TaskArtifactObjectStoragePlugin | None = None
        try:
            storage = injector.get(TaskArtifactObjectStoragePlugin)
        except Exception:  # noqa: BLE001 -- optional storage seam may be unbound
            storage = None
        return TodoArtifactService(
            repository=repository,
            artifact_repository=artifact_repository,
            space_access=space_access,
            collaboration=collaboration,
            storage=storage,
            storage_config=storage_config,
        )

    @singleton
    @provider
    @inject
    def todo_collaboration_port(
        self,
        context_collab: ContextCollabServiceProtocol,
        repository: ContextCollabRepositoryProtocol,
        executor: ContextCollabExecutor,
    ) -> TodoCollaborationPort:
        return InProcessTodoCollaborationAdapter(
            context_collab,
            TodoContextRuntime(context_collab, repository, executor),
        )

    @singleton
    @provider
    @inject
    def todo_scheduler(
        self,
        repository: TodoRepositoryProtocol,
        execution: TodoExecutionServiceProtocol,
        cache: CachePlugin,
        config: TodoSchedulerConfig,
    ) -> TodoScheduler:
        return TodoScheduler(repository, execution, cache, config)


def _mapping(value: Any, *, where: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{where} must be a mapping")
    return dict(value)


def _reject_unknown(
    block: dict[str, Any],
    *,
    where: str,
    allowed: frozenset[str],
) -> None:
    unknown = sorted(str(key) for key in block if str(key) not in allowed)
    if unknown:
        raise ValueError(
            f"unknown {where} key(s) " + ", ".join(repr(key) for key in unknown)
        )


def _bool(
    block: dict[str, Any],
    key: str,
    fallback: bool,
    where: str,
) -> bool:
    if key not in block:
        return fallback
    try:
        return _as_bool(block[key])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{where}.{key} must be a boolean") from exc


def _bounded_int(
    block: dict[str, Any],
    key: str,
    fallback: int,
    where: str,
    *,
    minimum: int,
) -> int:
    if key not in block:
        return fallback
    try:
        value = _as_int(block[key])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{where}.{key} must be an integer") from exc
    if value < minimum:
        raise ValueError(f"{where}.{key} must be at least {minimum}")
    return value


__all__ = ["TaskTodoBindings"]
