"""Consumer contract for Todo's B-side collaboration Plugin API."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from injector import singleton

from agentclaw.community.api.task.todo_service import TodoServiceProtocol
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.task.todos import (
    TodoRepositoryProtocol,
)
from agentclaw.community.core.spaces.protocols import SpaceAccessServiceProtocol
from agentclaw.community.core.spaces.space_service_protocol import (
    SpaceMemberServiceProtocol,
)
from agentclaw.community.core.task.todos.errors import TodoStateConflictError
from agentclaw.community.core.task.todos.models import (
    TodoRecord,
    TodoStatus,
)
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
            status=TodoStatus.DRAFT,
            collab_context_id=None,
            created_by="user_1",
            created_at=NOW,
            updated_at=NOW,
        )

    def create_todo(self, **kwargs: Any) -> TodoRecord:
        return self.todo

    def get_todo(self, **kwargs: Any) -> TodoRecord:
        return self.todo

    def list_todos(self, **kwargs: Any):
        raise AssertionError("not used by this contract suite")

    def update_draft(self, **kwargs: Any) -> TodoRecord:
        return self.todo

    def start_if_draft(self, **kwargs: Any) -> TodoRecord:
        from dataclasses import replace

        assert kwargs["collab_context_id"] == "context_todo_contract"
        self.todo = replace(
            self.todo,
            status=TodoStatus.IN_PROGRESS,
            collab_context_id=kwargs["collab_context_id"],
        )
        return self.todo

    def mark_running_if_pending_acceptance(self, **kwargs: Any) -> TodoRecord:
        assert kwargs["todo_id"] == self.todo.todo_id
        self.todo = _replace_status(self.todo, TodoStatus.IN_PROGRESS)
        return self.todo

    def mark_pending_acceptance_if_in_progress(self, **kwargs: Any) -> TodoRecord:
        self.todo = _replace_status(self.todo, TodoStatus.PENDING_ACCEPTANCE)
        return self.todo

    def accept_if_pending(self, **kwargs: Any) -> TodoRecord:
        self.todo = _replace_status(self.todo, TodoStatus.COMPLETED)
        return self.todo


class FakeSpaceAccess:
    def require_space_member(self, **kwargs):
        return (
            SimpleNamespace(
                id=kwargs["space_id"],
                space_type="TEAM",
                personal_owner_id=None,
            ),
            SimpleNamespace(user_id=kwargs["user_id"]),
        )


class FakeSpaceMembers:
    def list_members(self, **kwargs):
        return 1, [SimpleNamespace(member=SimpleNamespace(user_id="user_2"))]


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
        self.append_calls = 0

    def create_context(self, request):
        self.context_calls += 1
        return super().create_context(request)

    def append_event(self, **kwargs):
        self.append_calls += 1
        return super().append_event(**kwargs)


def _replace_status(todo: TodoRecord, status: TodoStatus) -> TodoRecord:
    from dataclasses import replace

    return replace(todo, status=status)


def bind_consumer_contract(world) -> tuple[TodoServiceProtocol, FakeTodoRepository, RecordingLocalCollaboration]:
    repository = FakeTodoRepository()
    collaboration = RecordingLocalCollaboration()
    binder = world.injector.binder
    binder.bind(TodoRepositoryProtocol, to=repository, scope=singleton)
    binder.bind(SpaceAccessServiceProtocol, to=FakeSpaceAccess(), scope=singleton)
    binder.bind(SpaceMemberServiceProtocol, to=FakeSpaceMembers(), scope=singleton)
    binder.bind(BotRepository, to=FakeBotRepository(), scope=singleton)
    binder.bind(TodoCollaborationPlugin, to=collaboration, scope=singleton)
    return world.get(TodoServiceProtocol), repository, collaboration


def test_todo_start_and_interaction_route_through_collaboration_plugin(world):
    service, repository, collaboration = bind_consumer_contract(world)

    created = service.create_todo(
        space_id=7,
        actor_id="user_1",
        title="Build contract test",
        description="Exercise B boundary",
        bot_id="bot_1",
    )
    started = service.start_todo(todo_id=created.todo_id, actor_id="user_1")

    # Consumer-level assertion: A persists only the B context identity.
    assert started.status is TodoStatus.IN_PROGRESS
    assert started.collab_context_id == "context_todo_contract"

    # Service-enforced interaction state changes the consumer's mutable fake.
    repository.todo = _replace_status(
        repository.todo, TodoStatus.PENDING_ACCEPTANCE
    )
    event = service.create_interaction(
        todo_id=started.todo_id,
        actor_id="user_1",
        author_name=None,
        message="Please revise section 2",
    )

    # Plugin-hit assertion: B recorded task creation and the Human event.
    assert collaboration.context_calls == 1
    assert collaboration.append_calls == 1
    assert event.event_type == "human_interaction"
    assert "context_todo_contract" in collaboration._contexts


def test_todo_interaction_state_failure_does_not_reach_collaboration_plugin(world):
    service, repository, collaboration = bind_consumer_contract(world)

    started = service.start_todo(todo_id=repository.todo.todo_id, actor_id="user_1")
    before = list(collaboration._contexts[started.collab_context_id])

    try:
        service.create_interaction(
            todo_id=started.todo_id,
            actor_id="user_1",
            author_name=None,
            message="not allowed",
        )
    except TodoStateConflictError:
        pass
    else:
        raise AssertionError("interaction outside pending acceptance must fail")

    # Failure is consumer policy; no event may be written to B.
    assert collaboration.append_calls == 0
    assert list(collaboration._contexts[started.collab_context_id]) == before
