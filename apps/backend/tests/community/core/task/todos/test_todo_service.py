from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from agentclaw.community.core.errors import ValidationError
from agentclaw.community.core.spaces.models import SpaceType
from agentclaw.community.core.task.todos.errors import (
    TodoBotAmbiguousError,
    TodoBotScopeError,
    TodoStateConflictError,
)
from agentclaw.community.core.task.todos.models import (
    AcceptanceVerdict,
    TodoRecord,
    TodoStatus,
)
from agentclaw.community.core.task.todos.services.todo_service import TodoService
from agentclaw.community.plugin_api.todo_collaboration import (
    TodoCollaborationArtifact,
    TodoCollaborationContext,
    TodoCollaborationContextRequest,
    TodoCollaborationError,
    TodoCollaborationEvent,
    TodoCollaborationEventRequest,
)

NOW = datetime(2026, 11, 10, tzinfo=timezone.utc)


def _space(
    *,
    space_id: int = 7,
    space_type: SpaceType = SpaceType.TEAM,
    owner_id: str | None = None,
):
    return SimpleNamespace(
        id=space_id,
        env="",
        space_type=space_type,
        personal_owner_id=owner_id,
    )


def _todo(**overrides: Any) -> TodoRecord:
    values: dict[str, Any] = {
        "todo_id": "todo_1",
        "space_id": 7,
        "title": "original",
        "description": "details",
        "bot_id": "bot_1",
        "bot_name": "Bot One",
        "status": TodoStatus.DRAFT,
        "collab_context_id": "context_todo_1",
        "created_by": "user_1",
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return TodoRecord(**values)


def _event(seq: int, event_type: str, **overrides: Any) -> TodoCollaborationEvent:
    values: dict[str, Any] = {
        "event_id": f"event_{seq}",
        "event_seq": seq,
        "event_type": event_type,
        "actor_id": "user_1",
        "actor_type": "human",
        "content": "content",
        "execution_id": "execution_1",
        "context_version": seq,
        "status": "active",
        "artifacts": (),
        "created_at": "2026-11-10T08:00:00Z",
    }
    values.update(overrides)
    return TodoCollaborationEvent(**values)


def _context(
    *,
    context_id: str = "context_todo_1",
    status: str = "created",
    version: int = 1,
    events: tuple[TodoCollaborationEvent, ...] = (),
) -> TodoCollaborationContext:
    return TodoCollaborationContext(
        collab_context_id=context_id,
        status=status,
        context_version=version,
        current_event_id=events[-1].event_id if events else "event_1",
        events=events,
    )


class FakeSpaceAccess:
    def __init__(self, space=None) -> None:
        self.space = space or _space()
        self.calls: list[dict[str, Any]] = []

    def require_space_member(self, **kwargs):
        self.calls.append(kwargs)
        return self.space, SimpleNamespace(user_id=kwargs["user_id"])


class FakeBots:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[str] = []

    def list_live_by_bot_id(self, bot_id: str):
        self.calls.append(bot_id)
        return self.rows


class FakeTodos:
    def __init__(self, todos: tuple[TodoRecord, ...]) -> None:
        self.todos = {todo.todo_id: todo for todo in todos}
        self.calls: list[dict[str, Any]] = []

    def _record(self, operation: str, **kwargs: Any) -> None:
        self.calls.append({"operation": operation, **kwargs})

    def create_todo(self, **kwargs) -> TodoRecord:
        self._record("create_todo", **kwargs)
        todo = _todo(
            todo_id="todo_created",
            space_id=kwargs["space_id"],
            title=kwargs["title"],
            description=kwargs["description"],
            bot_id=kwargs["bot_id"],
            bot_name=kwargs["bot_name"],
            collab_context_id=None,
            created_by=kwargs["created_by"],
        )
        self.todos[todo.todo_id] = todo
        return todo

    def get_todo(self, *, todo_id: str) -> TodoRecord:
        self._record("get_todo", todo_id=todo_id)
        return self.todos[todo_id]

    def list_todo_candidates(self, **kwargs) -> tuple[TodoRecord, ...]:
        self._record("list_todo_candidates", **kwargs)
        return tuple(self.todos.values())

    def bind_context_if_draft(self, **kwargs) -> TodoRecord:
        self._record("bind_context_if_draft", **kwargs)
        todo = self.todos[kwargs["todo_id"]]
        todo = replace(todo, collab_context_id=kwargs["collab_context_id"])
        self.todos[todo.todo_id] = todo
        return todo

    def record_acceptance(self, **kwargs) -> TodoRecord:
        self._record("record_acceptance", **kwargs)
        todo = self.todos[kwargs["todo_id"]]
        target = (
            TodoStatus.COMPLETED
            if kwargs["verdict"] is AcceptanceVerdict.APPROVED
            else TodoStatus.PENDING_ACCEPTANCE
        )
        todo = replace(todo, status=target)
        self.todos[todo.todo_id] = todo
        return todo


class FakeCollaboration:
    def __init__(self, contexts: tuple[TodoCollaborationContext, ...] = ()) -> None:
        self.contexts = {context.collab_context_id: context for context in contexts}
        self.create_calls: list[TodoCollaborationContextRequest] = []
        self.get_calls: list[tuple[str, str]] = []
        self.run_calls: list[tuple[str, str]] = []
        self.append_calls: list[tuple[str, TodoCollaborationEventRequest]] = []

    def create_context(
        self, request: TodoCollaborationContextRequest
    ) -> TodoCollaborationContext:
        self.create_calls.append(request)
        context = _context(context_id=f"context_{request.todo_id}")
        self.contexts[context.collab_context_id] = context
        return context

    def get_context(
        self, *, collab_context_id: str, actor_id: str
    ) -> TodoCollaborationContext:
        self.get_calls.append((collab_context_id, actor_id))
        return self.contexts[collab_context_id]

    async def run_context(
        self, *, collab_context_id: str, actor_id: str
    ) -> TodoCollaborationContext:
        self.run_calls.append((collab_context_id, actor_id))
        current = self.contexts[collab_context_id]
        refreshed = replace(
            current,
            status="in_progress",
            context_version=current.context_version + 1,
        )
        self.contexts[collab_context_id] = refreshed
        return refreshed

    def append_event(
        self, *, collab_context_id: str, request: TodoCollaborationEventRequest
    ) -> TodoCollaborationEvent:
        self.append_calls.append((collab_context_id, request))
        current = self.contexts[collab_context_id]
        event = _event(
            len(current.events) + 1,
            request.event_kind,
            actor_id=request.actor_id,
            actor_type=request.actor_type,
            content=str(request.payload.get("content") or ""),
            context_version=current.context_version + 1,
        )
        self.contexts[collab_context_id] = replace(
            current,
            context_version=current.context_version + 1,
            current_event_id=event.event_id,
            events=(*current.events, event),
        )
        return event


def _service(
    *,
    rows: list[dict[str, Any]] | None = None,
    space=None,
    todos: tuple[TodoRecord, ...] | None = None,
    contexts: tuple[TodoCollaborationContext, ...] = (),
):
    repository = FakeTodos(todos or (_todo(),))
    access = FakeSpaceAccess(space)
    bots = FakeBots(rows or [])
    collaboration = FakeCollaboration(contexts)
    service = TodoService(repository, access, bots, collaboration)
    return service, repository, access, bots, collaboration


def _team_bot(name: str = "Team Bot") -> dict[str, Any]:
    return {
        "bot_id": "bot_1",
        "bot_name": name,
        "space_id": 7,
        "owner_id": "another_user",
    }


def test_create_todo_creates_and_binds_context_immediately():
    service, repository, access, bots, collaboration = _service(rows=[_team_bot()])

    created = service.create_todo(
        space_id=7,
        actor_id="user_1",
        title="  Ship feature  ",
        description="  Detailed request  ",
        bot_id="  bot_1  ",
        env="pre",
    )

    assert created.status is TodoStatus.DRAFT
    assert created.collab_context_id == "context_todo_created"
    assert access.calls == [{"space_id": 7, "user_id": "user_1", "env": "pre"}]
    assert bots.calls == ["bot_1"]
    request = collaboration.create_calls[0]
    assert request.space_id == "7"
    assert request.environment == ""
    assert request.principal_id == "user_1"
    assert request.executor_id == "bot_1"
    assert request.executor_owner_id == "another_user"
    assert request.objective == "Detailed request"
    assert request.todo_id == "todo_created"
    assert repository.calls[-1] == {
        "operation": "bind_context_if_draft",
        "todo_id": "todo_created",
        "collab_context_id": "context_todo_created",
    }


@pytest.mark.parametrize(
    "space,rows",
    [
        (_space(), [{**_team_bot(), "space_id": 8}]),
        (
            _space(space_type=SpaceType.PERSONAL, owner_id="user_1"),
            [{**_team_bot(), "space_id": None, "owner_id": "user_2"}],
        ),
    ],
)
def test_create_todo_rejects_bot_outside_space_scope(space, rows):
    service, repository, _, _, collaboration = _service(space=space, rows=rows)

    with pytest.raises(TodoBotScopeError):
        service.create_todo(
            space_id=7,
            actor_id="user_1",
            title="title",
            description="details",
            bot_id="bot_1",
        )

    assert repository.calls == []
    assert collaboration.create_calls == []


def test_create_todo_rejects_ambiguous_bot_after_scope_filter():
    service, _, _, _, _ = _service(rows=[_team_bot("One"), _team_bot("Two")])

    with pytest.raises(TodoBotAmbiguousError):
        service.create_todo(
            space_id=7,
            actor_id="user_1",
            title="title",
            description="details",
            bot_id="bot_1",
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"title": "   ", "description": "ok", "bot_id": "bot_1"},
        {"title": "ok", "description": 3, "bot_id": "bot_1"},
        {"title": "ok", "description": "ok", "bot_id": "   "},
    ],
)
def test_create_todo_rejects_invalid_text(kwargs):
    service, _, _, _, _ = _service()

    with pytest.raises(ValidationError):
        service.create_todo(space_id=7, actor_id="user_1", **kwargs)


def test_list_todos_projects_b_status_then_filters_and_paginates_without_writes():
    todos = (
        _todo(todo_id="todo_1", collab_context_id="context_1"),
        _todo(todo_id="todo_2", collab_context_id="context_2"),
        _todo(todo_id="todo_3", collab_context_id="context_3"),
    )
    contexts = (
        _context(context_id="context_1", status="created"),
        _context(context_id="context_2", status="done"),
        _context(context_id="context_3", status="done"),
    )
    service, repository, access, _, _ = _service(
        todos=todos,
        contexts=contexts,
    )

    page = service.list_todos(
        space_id=7,
        actor_id="user_1",
        keyword="  feature  ",
        status="pending_acceptance",
        bot_id="  bot_1  ",
        page=2,
        page_size=1,
    )

    assert page.total == 2
    assert [item.todo_id for item in page.items] == ["todo_3"]
    assert access.calls == [{"space_id": 7, "user_id": "user_1", "env": None}]
    assert repository.calls == [
        {
            "operation": "list_todo_candidates",
            "space_id": 7,
            "keyword": "feature",
            "bot_id": "bot_1",
        }
    ]


@pytest.mark.asyncio
async def test_start_todo_runs_existing_context_without_creating_another():
    service, repository, _, _, collaboration = _service(
        contexts=(_context(status="created"),)
    )

    started = await service.start_todo(todo_id="todo_1", actor_id="user_1")

    assert started.status is TodoStatus.IN_PROGRESS
    assert collaboration.run_calls == [("context_todo_1", "user_1")]
    assert collaboration.create_calls == []
    assert [call["operation"] for call in repository.calls] == ["get_todo"]


@pytest.mark.asyncio
async def test_start_todo_rejects_projected_non_draft_context():
    service, _, _, _, collaboration = _service(
        contexts=(_context(status="in_progress"),)
    )

    with pytest.raises(TodoStateConflictError):
        await service.start_todo(todo_id="todo_1", actor_id="user_1")

    assert collaboration.run_calls == []


def test_list_events_is_pure_read_and_maps_artifacts():
    event = _event(
        3,
        "task.completed",
        actor_type="bot",
        artifacts=(
            TodoCollaborationArtifact(
                artifact_id="artifact_1",
                name="report.docx",
                url="/files/report.docx",
            ),
        ),
    )
    service, repository, _, _, collaboration = _service(
        contexts=(_context(status="done", version=3, events=(event,)),)
    )

    result = service.list_events(todo_id="todo_1", actor_id="user_1")

    assert result[0].event_type == "task.completed"
    assert result[0].context_version == 3
    assert result[0].artifacts[0].name == "report.docx"
    assert [call["operation"] for call in repository.calls] == ["get_todo"]
    assert collaboration.get_calls == [("context_todo_1", "user_1")]


def test_create_interaction_appends_b_event_and_remains_pending_acceptance():
    service, repository, _, _, collaboration = _service(
        contexts=(_context(status="done", version=3),)
    )

    event = service.create_interaction(
        todo_id="todo_1",
        actor_id="user_1",
        author_name="User One",
        message="please revise section 2",
    )
    projected = service.get_todo(todo_id="todo_1", actor_id="user_1")

    assert event.event_type == "user_interaction.posted"
    assert projected.status is TodoStatus.PENDING_ACCEPTANCE
    context_id, request = collaboration.append_calls[0]
    assert context_id == "context_todo_1"
    assert request.expected_version == 3
    assert request.parent_event == "event_1"
    assert request.payload == {
        "content": "please revise section 2",
        "author_name": "User One",
    }
    assert all(call["operation"] != "record_acceptance" for call in repository.calls)


def test_create_interaction_rejects_state_other_than_pending_acceptance():
    service, _, _, _, collaboration = _service(
        contexts=(_context(status="in_progress"),)
    )

    with pytest.raises(TodoStateConflictError):
        service.create_interaction(
            todo_id="todo_1",
            actor_id="user_1",
            author_name=None,
            message="message",
        )

    assert collaboration.append_calls == []


@pytest.mark.parametrize(
    "verdict,target",
    [
        ("approved", TodoStatus.COMPLETED),
        ("rejected", TodoStatus.PENDING_ACCEPTANCE),
    ],
)
def test_submit_acceptance_writes_only_a_side(verdict, target):
    service, repository, _, _, collaboration = _service(
        contexts=(_context(status="done", version=3),)
    )

    updated = service.submit_acceptance(
        todo_id="todo_1",
        actor_id="user_1",
        verdict=verdict,
        message="looks good",
    )

    assert updated.status is target
    assert collaboration.append_calls == []
    assert repository.calls[-1] == {
        "operation": "record_acceptance",
        "todo_id": "todo_1",
        "verdict": AcceptanceVerdict(verdict),
        "message": "looks good",
        "author_id": "user_1",
    }


@pytest.mark.parametrize(
    "context_status,todo_status",
    [
        ("created", TodoStatus.DRAFT),
        ("in_progress", TodoStatus.IN_PROGRESS),
        ("reviewing", TodoStatus.IN_PROGRESS),
        ("blocked", TodoStatus.IN_PROGRESS),
        ("done", TodoStatus.PENDING_ACCEPTANCE),
        ("cancelled", TodoStatus.PENDING_ACCEPTANCE),
    ],
)
def test_all_b_context_statuses_have_an_explicit_todo_projection(
    context_status, todo_status
):
    service, _, _, _, _ = _service(contexts=(_context(status=context_status),))

    result = service.get_todo(todo_id="todo_1", actor_id="user_1")

    assert result.status is todo_status


def test_completed_a_status_wins_over_b_projection():
    service, _, _, _, collaboration = _service(
        todos=(_todo(status=TodoStatus.COMPLETED),),
        contexts=(_context(status="created"),),
    )

    result = service.get_todo(todo_id="todo_1", actor_id="user_1")

    assert result.status is TodoStatus.COMPLETED
    assert collaboration.get_calls == []


def test_unknown_b_status_is_rejected():
    service, _, _, _, _ = _service(contexts=(_context(status="mystery"),))

    with pytest.raises(TodoCollaborationError, match="unknown B Context status"):
        service.get_todo(todo_id="todo_1", actor_id="user_1")
