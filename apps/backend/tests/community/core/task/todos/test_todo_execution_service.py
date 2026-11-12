from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from agentclaw.community.core.spaces.models import SpaceType
from agentclaw.community.core.task.todos.errors import (
    TodoBotScopeError,
    TodoStateConflictError,
)
from agentclaw.community.core.task.todos.models import TodoRecord, TodoStatus
from agentclaw.community.core.task.todos.services.todo_execution_service import (
    TodoExecutionService,
)
from agentclaw.community.plugin_api.todo_collaboration import (
    TodoCollaborationContext,
)

NOW = datetime(2026, 11, 12, tzinfo=timezone.utc)


def _todo(**overrides: Any) -> TodoRecord:
    values: dict[str, Any] = {
        "todo_id": "todo_1",
        "space_id": 7,
        "title": "Ship feature",
        "description": "Detailed objective",
        "bot_id": "bot_1",
        "bot_name": "Bot One",
        "status": TodoStatus.PENDING,
        "collab_context_id": None,
        "created_by": "creator_1",
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return TodoRecord(**values)


def _space(
    *,
    space_type: SpaceType = SpaceType.TEAM,
    owner_id: str | None = None,
):
    return SimpleNamespace(
        id=7,
        env="pre",
        space_type=space_type,
        personal_owner_id=owner_id,
    )


def _context(context_id: str, status: str) -> TodoCollaborationContext:
    return TodoCollaborationContext(
        collab_context_id=context_id,
        status=status,
        context_version=1,
        current_event_id=None,
    )


class _Repository:
    def __init__(self, todo: TodoRecord) -> None:
        self.todo = todo
        self.calls: list[tuple[str, Any]] = []
        self.bind_race_context_id: str | None = None
        self.bind_error: Exception | None = None
        self.transition_conflict_status: TodoStatus | None = None

    def get_todo(self, *, todo_id: str) -> TodoRecord:
        self.calls.append(("get", todo_id))
        return self.todo

    def bind_context_if_pending(
        self, *, todo_id: str, collab_context_id: str
    ) -> TodoRecord:
        self.calls.append(("bind", todo_id, collab_context_id))
        if self.bind_error is not None:
            raise self.bind_error
        if self.bind_race_context_id is not None:
            self.todo = replace(
                self.todo,
                collab_context_id=self.bind_race_context_id,
            )
            raise TodoStateConflictError("concurrent context binding")
        self.todo = replace(self.todo, collab_context_id=collab_context_id)
        return self.todo

    def transition_status(
        self,
        *,
        todo_id: str,
        expected_status: TodoStatus,
        new_status: TodoStatus,
    ) -> TodoRecord:
        self.calls.append(("transition", todo_id, expected_status, new_status))
        if self.transition_conflict_status is not None:
            self.todo = replace(self.todo, status=self.transition_conflict_status)
            raise TodoStateConflictError("concurrent status transition")
        if self.todo.status is not expected_status:
            raise TodoStateConflictError("concurrent status transition")
        self.todo = replace(self.todo, status=new_status)
        return self.todo


class _SpaceAccess:
    def __init__(self, space=None) -> None:
        self.space = space or _space()
        self.calls: list[dict[str, Any]] = []

    def require_space_member(self, **kwargs):
        self.calls.append(kwargs)
        return self.space, SimpleNamespace(user_id=kwargs["user_id"])


class _Bots:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[str] = []

    def list_live_by_bot_id(self, bot_id: str):
        self.calls.append(bot_id)
        return self.rows


class _Collaboration:
    def __init__(
        self,
        *,
        contexts: tuple[TodoCollaborationContext, ...] = (),
        created_context: TodoCollaborationContext | None = None,
        refreshed_context: TodoCollaborationContext | None = None,
    ) -> None:
        self.contexts = {item.collab_context_id: item for item in contexts}
        self.created_context = created_context or _context("context_created", "created")
        self.refreshed_context = refreshed_context
        self.create_calls: list[Any] = []
        self.get_calls: list[tuple[str, str]] = []
        self.run_calls: list[tuple[str, str]] = []
        self.refresh_calls: list[tuple[str, str]] = []

    def create_context(self, request):
        self.create_calls.append(request)
        self.contexts[self.created_context.collab_context_id] = self.created_context
        return self.created_context

    def get_context(self, *, collab_context_id: str, actor_id: str):
        self.get_calls.append((collab_context_id, actor_id))
        return self.contexts[collab_context_id]

    async def run_context(self, *, collab_context_id: str, actor_id: str):
        self.run_calls.append((collab_context_id, actor_id))
        context = replace(self.contexts[collab_context_id], status="in_progress")
        self.contexts[collab_context_id] = context
        return context

    async def refresh_context_run(self, *, collab_context_id: str, actor_id: str):
        self.refresh_calls.append((collab_context_id, actor_id))
        if self.refreshed_context is not None:
            self.contexts[collab_context_id] = self.refreshed_context
        return self.contexts[collab_context_id]


def _service(
    *,
    todo: TodoRecord | None = None,
    rows: list[dict[str, Any]] | None = None,
    space=None,
    contexts: tuple[TodoCollaborationContext, ...] = (),
    created_context: TodoCollaborationContext | None = None,
    refreshed_context: TodoCollaborationContext | None = None,
):
    repository = _Repository(todo or _todo())
    access = _SpaceAccess(space)
    bots = _Bots(
        rows
        if rows is not None
        else [
            {
                "bot_id": "bot_1",
                "bot_name": "Bot One",
                "space_id": 7,
                "owner_id": "bot_owner_1",
            }
        ]
    )
    collaboration = _Collaboration(
        contexts=contexts,
        created_context=created_context,
        refreshed_context=refreshed_context,
    )
    service = TodoExecutionService(repository, access, bots, collaboration)
    return service, repository, access, bots, collaboration


@pytest.mark.asyncio
async def test_dispatch_creates_binds_runs_and_transitions_with_creator_identity():
    service, repository, access, bots, collaboration = _service()

    result = await service.dispatch_pending(todo_id="todo_1", env="pre")

    assert result.status is TodoStatus.IN_PROGRESS
    assert result.collab_context_id == "context_created"
    assert access.calls == [
        {"space_id": 7, "user_id": "creator_1", "env": "pre"}
    ]
    assert bots.calls == ["bot_1"]
    request = collaboration.create_calls[0]
    assert request.space_id == "7"
    assert request.environment == "pre"
    assert request.principal_id == "creator_1"
    assert request.product_kind == "team_todo"
    assert request.workflow_kind == "team_todo_execution"
    assert request.method_kind == "Single"
    assert request.executor_id == "bot_1"
    assert request.executor_owner_id == "bot_owner_1"
    assert request.objective == "Detailed objective"
    assert request.todo_id == "todo_1"
    assert collaboration.run_calls == [("context_created", "creator_1")]
    assert repository.calls[-1] == (
        "transition",
        "todo_1",
        TodoStatus.PENDING,
        TodoStatus.IN_PROGRESS,
    )


@pytest.mark.asyncio
async def test_dispatch_rejects_bot_without_real_owner_identity():
    service, repository, _, _, collaboration = _service(
        rows=[{"bot_id": "bot_1", "space_id": 7, "owner_id": "  "}]
    )

    with pytest.raises(TodoBotScopeError):
        await service.dispatch_pending(todo_id="todo_1")

    assert collaboration.create_calls == []
    assert all(call[0] != "bind" for call in repository.calls)


@pytest.mark.asyncio
async def test_dispatch_reuses_context_bound_by_concurrent_worker():
    winner = _context("context_winner", "in_progress")
    service, repository, _, _, collaboration = _service(contexts=(winner,))
    repository.bind_race_context_id = winner.collab_context_id

    result = await service.dispatch_pending(todo_id="todo_1")

    assert result.status is TodoStatus.IN_PROGRESS
    assert result.collab_context_id == "context_winner"
    assert collaboration.create_calls
    assert collaboration.get_calls == [("context_winner", "creator_1")]
    assert collaboration.run_calls == []


@pytest.mark.asyncio
async def test_dispatch_propagates_non_conflict_binding_failure():
    service, repository, _, _, _ = _service()
    repository.bind_error = RuntimeError("database unavailable")

    with pytest.raises(RuntimeError, match="database unavailable"):
        await service.dispatch_pending(todo_id="todo_1")


@pytest.mark.asyncio
@pytest.mark.parametrize("context_status", ["in_progress", "done"])
async def test_dispatch_compensates_todo_for_already_started_context(context_status):
    context = _context("context_existing", context_status)
    service, _, _, _, collaboration = _service(
        todo=_todo(collab_context_id=context.collab_context_id),
        contexts=(context,),
    )

    result = await service.dispatch_pending(todo_id="todo_1")

    assert result.status is TodoStatus.IN_PROGRESS
    assert collaboration.run_calls == []


@pytest.mark.asyncio
async def test_dispatch_is_idempotent_after_another_worker_advanced_todo():
    todo = _todo(status=TodoStatus.IN_PROGRESS, collab_context_id="context_1")
    service, repository, access, bots, collaboration = _service(todo=todo)

    result = await service.dispatch_pending(todo_id="todo_1")

    assert result is todo
    assert repository.calls == [("get", "todo_1")]
    assert access.calls == []
    assert bots.calls == []
    assert collaboration.get_calls == []


@pytest.mark.asyncio
async def test_refresh_moves_done_context_to_pending_acceptance_without_runtime_call():
    context = _context("context_1", "done")
    service, _, _, _, collaboration = _service(
        todo=_todo(
            status=TodoStatus.IN_PROGRESS,
            collab_context_id=context.collab_context_id,
        ),
        contexts=(context,),
    )

    result = await service.refresh_in_progress(todo_id="todo_1")

    assert result.status is TodoStatus.PENDING_ACCEPTANCE
    assert collaboration.refresh_calls == []


@pytest.mark.asyncio
async def test_refresh_projects_runtime_completion_to_pending_acceptance():
    context = _context("context_1", "in_progress")
    service, _, _, _, collaboration = _service(
        todo=_todo(
            status=TodoStatus.IN_PROGRESS,
            collab_context_id=context.collab_context_id,
        ),
        contexts=(context,),
        refreshed_context=replace(context, status="done"),
    )

    result = await service.refresh_in_progress(todo_id="todo_1")

    assert result.status is TodoStatus.PENDING_ACCEPTANCE
    assert collaboration.refresh_calls == [("context_1", "creator_1")]


@pytest.mark.asyncio
async def test_refresh_keeps_failed_or_running_runtime_in_progress():
    context = _context("context_1", "in_progress")
    service, repository, _, _, collaboration = _service(
        todo=_todo(
            status=TodoStatus.IN_PROGRESS,
            collab_context_id=context.collab_context_id,
        ),
        contexts=(context,),
        refreshed_context=context,
    )

    result = await service.refresh_in_progress(todo_id="todo_1")

    assert result.status is TodoStatus.IN_PROGRESS
    assert all(call[0] != "transition" for call in repository.calls)
    assert collaboration.refresh_calls == [("context_1", "creator_1")]


@pytest.mark.asyncio
async def test_refresh_fails_closed_for_inconsistent_context_state():
    context = _context("context_1", "created")
    service, _, _, _, collaboration = _service(
        todo=_todo(
            status=TodoStatus.IN_PROGRESS,
            collab_context_id=context.collab_context_id,
        ),
        contexts=(context,),
    )

    with pytest.raises(TodoStateConflictError, match="created"):
        await service.refresh_in_progress(todo_id="todo_1")

    assert collaboration.refresh_calls == []


@pytest.mark.asyncio
async def test_dispatch_accepts_concurrent_forward_transition_past_in_progress():
    service, repository, _, _, _ = _service()
    repository.transition_conflict_status = TodoStatus.PENDING_ACCEPTANCE

    result = await service.dispatch_pending(todo_id="todo_1")

    assert result.status is TodoStatus.PENDING_ACCEPTANCE


@pytest.mark.asyncio
async def test_refresh_accepts_concurrent_completion_after_runtime_done():
    context = _context("context_1", "done")
    service, repository, _, _, _ = _service(
        todo=_todo(
            status=TodoStatus.IN_PROGRESS,
            collab_context_id=context.collab_context_id,
        ),
        contexts=(context,),
    )
    repository.transition_conflict_status = TodoStatus.COMPLETED

    result = await service.refresh_in_progress(todo_id="todo_1")

    assert result.status is TodoStatus.COMPLETED


@pytest.mark.asyncio
async def test_refresh_rejects_concurrent_status_regression():
    context = _context("context_1", "done")
    service, repository, _, _, _ = _service(
        todo=_todo(
            status=TodoStatus.IN_PROGRESS,
            collab_context_id=context.collab_context_id,
        ),
        contexts=(context,),
    )
    repository.transition_conflict_status = TodoStatus.PENDING

    with pytest.raises(TodoStateConflictError, match="invalid successor"):
        await service.refresh_in_progress(todo_id="todo_1")
