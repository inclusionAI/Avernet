"""Consumer contract for Todo's B-side collaboration Plugin API."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from injector import singleton

from agentclaw.community.api.task.todo_service import TodoServiceProtocol
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.task.todos import (
    TodoRepositoryProtocol,
)
from agentclaw.community.core.spaces.protocols import SpaceAccessServiceProtocol
from agentclaw.community.core.task.todos.errors import TodoStateConflictError
from agentclaw.community.core.task.todos.execution_protocol import (
    TodoExecutionServiceProtocol,
)
from agentclaw.community.core.task.todos.models import TodoRecord, TodoStatus
from agentclaw.community.plugin_api.todo_collaboration import TodoCollaborationPlugin
from agentclaw.community.plugins.local.todo_collaboration import (
    LocalTodoCollaborationPlugin,
)

NOW = datetime(2026, 11, 10, tzinfo=timezone.utc)


class FakeTodoRepository:
    def __init__(self) -> None:
        self.todo = TodoRecord(
            todo_id="todo_contract",
            space_id=7,
            title="Build contract test",
            description="Exercise B boundary",
            bot_id="bot_1",
            bot_name="Contract Bot",
            status=TodoStatus.PENDING,
            collab_context_id=None,
            created_by="user_1",
            created_at=NOW,
            updated_at=NOW,
        )

    def create_todo(self, **_kwargs: Any) -> TodoRecord:
        return self.todo

    def get_todo(self, **_kwargs: Any) -> TodoRecord:
        return self.todo

    def list_todos(self, **_kwargs: Any):
        return type("Page", (), {"total": 1, "items": (self.todo,)})()

    def list_todos_by_status(self, **_kwargs: Any) -> tuple[TodoRecord, ...]:
        return (self.todo,)

    def bind_context_if_pending(self, **kwargs: Any) -> TodoRecord:
        assert kwargs["collab_context_id"] == "context_todo_contract"
        self.todo = replace(
            self.todo,
            collab_context_id=kwargs["collab_context_id"],
        )
        return self.todo

    def transition_status(self, **kwargs: Any) -> TodoRecord:
        assert self.todo.status is kwargs["expected_status"]
        self.todo = replace(self.todo, status=kwargs["new_status"])
        return self.todo

    def record_acceptance(self, **_kwargs: Any) -> TodoRecord:
        self.todo = replace(self.todo, status=TodoStatus.COMPLETED)
        return self.todo


class FakeSpaceAccess:
    def require_space_member(self, **kwargs):
        return (
            SimpleNamespace(
                id=kwargs["space_id"],
                env="",
                space_type="TEAM",
                personal_owner_id=None,
            ),
            SimpleNamespace(user_id=kwargs["user_id"]),
        )


class FakeBotRepository:
    def list_live_by_bot_id(self, bot_id: str):
        return [
            {
                "bot_id": bot_id,
                "bot_name": "Contract Bot",
                "space_id": 7,
                "owner_id": "another_user",
            }
        ]


class RecordingLocalCollaboration(LocalTodoCollaborationPlugin):
    """Local plugin double with observable call history for the contract."""

    def __init__(self) -> None:
        super().__init__()
        self.context_calls = 0
        self.run_calls = 0
        self.resume_calls = 0
        self.append_calls = 0

    def create_context(self, request):
        self.context_calls += 1
        return super().create_context(request)

    async def run_context(self, **kwargs):
        self.run_calls += 1
        return await super().run_context(**kwargs)

    async def resume_context(self, **kwargs):
        self.resume_calls += 1
        return await super().resume_context(**kwargs)

    def append_event(self, **kwargs):
        self.append_calls += 1
        return super().append_event(**kwargs)


def bind_consumer_contract(
    world,
) -> tuple[TodoServiceProtocol, FakeTodoRepository, RecordingLocalCollaboration]:
    repository = FakeTodoRepository()
    collaboration = RecordingLocalCollaboration()
    binder = world.injector.binder
    binder.bind(TodoRepositoryProtocol, to=repository, scope=singleton)
    binder.bind(SpaceAccessServiceProtocol, to=FakeSpaceAccess(), scope=singleton)
    binder.bind(BotRepository, to=FakeBotRepository(), scope=singleton)
    binder.bind(TodoCollaborationPlugin, to=collaboration, scope=singleton)
    return world.get(TodoServiceProtocol), repository, collaboration


@pytest.mark.asyncio
async def test_todo_create_run_and_interaction_route_through_collaboration_plugin(
    world,
):
    service, repository, collaboration = bind_consumer_contract(world)

    created = service.create_todo(
        space_id=7,
        actor_id="user_1",
        title="Build contract test",
        description="Exercise B boundary",
        bot_id="bot_1",
    )
    started = await service.start_todo(
        todo_id=created.todo_id,
        actor_id="user_1",
    )
    collaboration.complete_context(started.collab_context_id)
    execution = world.get(TodoExecutionServiceProtocol)
    await execution.refresh_in_progress(todo_id=started.todo_id)
    pending = service.get_todo(todo_id=started.todo_id, actor_id="user_1")
    event = await service.create_interaction(
        todo_id=started.todo_id,
        actor_id="user_1",
        author_name=None,
        message="Please revise section 2",
    )

    assert repository.todo.collab_context_id == "context_todo_contract"
    assert created.status is TodoStatus.PENDING
    assert started.status is TodoStatus.IN_PROGRESS
    assert pending.status is TodoStatus.PENDING_ACCEPTANCE
    assert repository.todo.status is TodoStatus.IN_PROGRESS
    assert collaboration.context_calls == 1
    assert collaboration.run_calls == 1
    assert collaboration.resume_calls == 1
    assert collaboration.append_calls == 2
    assert event.event_type == "user_interaction.posted"
    resumed_context = collaboration.get_context(
        collab_context_id=started.collab_context_id,
        actor_id="user_1",
    )
    assert resumed_context.events[-1].event_type == "task.started"


@pytest.mark.asyncio
async def test_todo_interaction_state_failure_does_not_reach_collaboration_plugin(
    world,
):
    service, _repository, collaboration = bind_consumer_contract(world)
    created = service.create_todo(
        space_id=7,
        actor_id="user_1",
        title="Build contract test",
        description="Exercise B boundary",
        bot_id="bot_1",
    )
    started = await service.start_todo(
        todo_id=created.todo_id,
        actor_id="user_1",
    )
    before = collaboration.get_context(
        collab_context_id=started.collab_context_id,
        actor_id="user_1",
    ).events

    with pytest.raises(TodoStateConflictError):
        await service.create_interaction(
            todo_id=started.todo_id,
            actor_id="user_1",
            author_name=None,
            message="not allowed",
        )

    after = collaboration.get_context(
        collab_context_id=started.collab_context_id,
        actor_id="user_1",
    ).events
    assert collaboration.append_calls == 0
    assert after == before
