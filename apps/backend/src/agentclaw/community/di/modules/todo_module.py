"""Composition root and strict configuration for the Todo workbench."""

from __future__ import annotations

from dataclasses import fields
from typing import Any

from injector import Binder, Injector, Module, inject, provider, singleton

from agentclaw.community.core.repository.implementations.task.todos import (
    TodoArtifactRepository,
    TodoRepository,
)
from agentclaw.community.core.repository.protocols.task.todo_artifacts import (
    TodoArtifactRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.task.todos import (
    TodoRepositoryProtocol,
)
from agentclaw.community.core.spaces.protocols import SpaceAccessServiceProtocol
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
from agentclaw.community.core.task.todos.execution_protocol import (
    TodoExecutionServiceProtocol,
)
from agentclaw.community.core.task.todos.scheduler import TodoScheduler
from agentclaw.community.core.task.todos.service_protocol import TodoServiceProtocol
from agentclaw.community.core.task.todos.services.todo_artifact_service import (
    TodoArtifactService,
)
from agentclaw.community.core.task.todos.services.todo_execution_service import (
    TodoExecutionService,
)
from agentclaw.community.core.task.todos.services.todo_service import TodoService
from agentclaw.community.di.modules.config_module import _as_bool, _as_int, _block
from agentclaw.community.di.todo_config import TodoSchedulerConfig
from agentclaw.community.plugin_api.cache import CachePlugin
from agentclaw.community.plugin_api.todo_collaboration import (
    TodoCollaborationPlugin,
)
from agentclaw.community.plugins.community.todo_collaboration import (
    InProcessTodoCollaborationPlugin,
)

_TODO_KEYS = frozenset({"scheduler"})
_SCHEDULER_KEYS = frozenset(field.name for field in fields(TodoSchedulerConfig))


class TodoModule(Module):
    """Own Todo persistence, application services, runtime adapter and loop."""

    def configure(self, binder: Binder) -> None:
        binder.bind(TodoRepositoryProtocol, to=TodoRepository, scope=singleton)
        binder.bind(
            TodoArtifactRepositoryProtocol,
            to=TodoArtifactRepository,
            scope=singleton,
        )
        binder.bind(TodoServiceProtocol, to=TodoService, scope=singleton)
        binder.bind(
            TodoExecutionServiceProtocol,
            to=TodoExecutionService,
            scope=singleton,
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
            storage=storage,
            storage_config=storage_config,
        )

    @singleton
    @provider
    @inject
    def todo_collaboration_plugin(
        self,
        context_collab: ContextCollabServiceProtocol,
    ) -> TodoCollaborationPlugin:
        return InProcessTodoCollaborationPlugin(context_collab)

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
            f"unknown {where} key(s) "
            + ", ".join(repr(key) for key in unknown)
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


__all__ = ["TodoModule"]
