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
    TodoNotFoundError,
    TodoStateConflictError,
)
from agentclaw.community.core.task.todos.models import (
    TodoPage,
    TodoRecord,
    TodoStatus,
)
from agentclaw.community.core.task.todos.services.todo_service import TodoService
from agentclaw.community.plugin_api.todo_collaboration import (
    TodoCollaborationContext,
    TodoCollaborationContextRequest,
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
        "collab_context_id": None,
        "created_by": "user_1",
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return TodoRecord(**values)


def _plugin_event(seq: int, event_type: str, **overrides: Any):
    values: dict[str, Any] = {
        "event_id": f"event_{seq}",
        "event_seq": seq,
        "event_type": event_type,
        "actor_id": "user_1",
        "actor_type": "human",
        "content": "content",
        "execution_id": "execution_1",
        "context_version": "v1",
        "status": None,
        "artifacts": (),
        "created_at": "2026-11-10T08:00:00Z",
    }
    values.update(overrides)
    return TodoCollaborationEvent(**values)


class FakeSpaceAccess:
    def __init__(self, space=None) -> None:
        self.space = space or _space()
        self.calls: list[dict[str, Any]] = []

    def require_space_member(self, **kwargs):
        self.calls.append(kwargs)
        return self.space, SimpleNamespace(user_id=kwargs["user_id"])


class FakeSpaceMembers:
    def __init__(self, total: int = 1) -> None:
        self.total = total
        self.calls: list[dict[str, Any]] = []

    def list_members(self, **kwargs):
        self.calls.append(kwargs)
        return self.total, [SimpleNamespace(member=SimpleNamespace(user_id="user_2"))]


class FakeBots:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[str] = []

    def list_live_by_bot_id(self, bot_id: str):
        self.calls.append(bot_id)
        return self.rows


class FakeCollaboration:
    def __init__(self, events: tuple[TodoCollaborationEvent, ...] = ()) -> None:
        self.events = list(events)
        self.context_calls: list[TodoCollaborationContextRequest] = []
        self.append_calls: list[tuple[str, TodoCollaborationEventRequest]] = []
        self.list_calls: list[str] = []

    def create_context(
        self, request: TodoCollaborationContextRequest
    ) -> TodoCollaborationContext:
        self.context_calls.append(request)
        return TodoCollaborationContext(collab_context_id="context_todo_1")

    def append_event(
        self, *, collab_context_id: str, request: TodoCollaborationEventRequest
    ) -> TodoCollaborationEvent:
        self.append_calls.append((collab_context_id, request))
        event = _plugin_event(
            len(self.events) + 1,
            request.event_type,
            actor_id=request.actor_id,
            content=request.content,
            status="in_progress",
        )
        self.events.append(event)
        return event

    def list_events(self, *, collab_context_id: str):
        self.list_calls.append(collab_context_id)
        return tuple(self.events)


class FakeTodos:
    def __init__(self, todo: TodoRecord | None = None) -> None:
        self.todo = todo or _todo()
        self.calls: list[dict[str, Any]] = []

    def _record(self, operation: str, **kwargs: Any):
        self.calls.append({"operation": operation, **kwargs})

    @staticmethod
    def _replace(todo: TodoRecord, **kwargs: Any) -> TodoRecord:
        return replace(todo, **kwargs)

    def create_todo(self, **kwargs):
        self._record("create_todo", **kwargs)
        self.todo = _todo(
            todo_id="todo_created",
            space_id=kwargs["space_id"],
            title=kwargs["title"],
            description=kwargs["description"],
            bot_id=kwargs["bot_id"],
            bot_name=kwargs["bot_name"],
            created_by=kwargs["created_by"],
        )
        return self.todo

    def get_todo(self, **kwargs):
        self._record("get_todo", **kwargs)
        return self.todo

    def list_todos(self, **kwargs):
        self._record("list_todos", **kwargs)
        return TodoPage(total=1, items=(self.todo,))

    def update_draft(self, **kwargs):
        self._record("update_draft", **kwargs)
        self.todo = self._replace(
            self.todo,
            title=kwargs["title"],
            description=kwargs["description"],
            bot_id=kwargs["bot_id"],
            bot_name=kwargs["bot_name"],
        )
        return self.todo

    def start_if_draft(self, **kwargs):
        self._record("start_if_draft", **kwargs)
        if self.todo.status is not TodoStatus.DRAFT:
            raise TodoStateConflictError("not draft")
        self.todo = self._replace(
            self.todo,
            status=TodoStatus.IN_PROGRESS,
            collab_context_id=kwargs["collab_context_id"],
        )
        return self.todo

    def mark_running_if_pending_acceptance(self, **kwargs):
        self._record("mark_running_if_pending_acceptance", **kwargs)
        if self.todo.status is not TodoStatus.PENDING_ACCEPTANCE:
            raise TodoStateConflictError("not pending")
        self.todo = self._replace(self.todo, status=TodoStatus.IN_PROGRESS)
        return self.todo

    def mark_pending_acceptance_if_in_progress(self, **kwargs):
        self._record("mark_pending_acceptance_if_in_progress", **kwargs)
        if self.todo.status is TodoStatus.IN_PROGRESS:
            self.todo = self._replace(self.todo, status=TodoStatus.PENDING_ACCEPTANCE)
        return self.todo

    def accept_if_pending(self, **kwargs):
        self._record("accept_if_pending", **kwargs)
        if self.todo.status is not TodoStatus.PENDING_ACCEPTANCE:
            raise TodoStateConflictError("not pending")
        self.todo = self._replace(
            self.todo,
            status=(
                TodoStatus.COMPLETED
                if kwargs["verdict"].value == "approved"
                else TodoStatus.IN_PROGRESS
            ),
        )
        return self.todo


def _service(
    *,
    rows=None,
    space=None,
    todo=None,
    events=(),
    member_total=1,
):
    repository = FakeTodos(todo)
    access = FakeSpaceAccess(space)
    members = FakeSpaceMembers(member_total)
    bots = FakeBots(rows if rows is not None else [])
    collaboration = FakeCollaboration(events)
    service = TodoService(
        repository,
        access,
        members,
        bots,
        collaboration,
    )
    return service, repository, access, members, bots, collaboration


def test_create_todo_normalizes_input_and_snapshots_team_bot_name():
    service, repository, access, _, bots, _ = _service(
        rows=[
            {
                "bot_id": "bot_1",
                "bot_name": "Team Bot",
                "space_id": 7,
                "owner_id": "another_user",
            }
        ]
    )

    created = service.create_todo(
        space_id=7,
        actor_id="user_1",
        title="  Ship feature  ",
        description="  Detailed request  ",
        bot_id="  bot_1  ",
        env="pre",
    )

    assert created.status is TodoStatus.DRAFT
    assert created.collab_context_id is None
    assert access.calls == [{"space_id": 7, "user_id": "user_1", "env": "pre"}]
    assert bots.calls == ["bot_1"]
    assert repository.calls == [
        {
            "operation": "create_todo",
            "space_id": 7,
            "title": "Ship feature",
            "description": "Detailed request",
            "bot_id": "bot_1",
            "bot_name": "Team Bot",
            "created_by": "user_1",
        }
    ]


@pytest.mark.parametrize(
    "space,rows",
    [
        (
            _space(space_type=SpaceType.TEAM),
            [{"bot_id": "bot_1", "bot_name": "Other", "space_id": 8, "owner_id": "u"}],
        ),
        (
            _space(space_type=SpaceType.PERSONAL, owner_id="user_1"),
            [
                {
                    "bot_id": "bot_1",
                    "bot_name": "Other",
                    "space_id": None,
                    "owner_id": "user_2",
                }
            ],
        ),
    ],
)
def test_create_todo_rejects_bot_outside_space_scope(space, rows):
    service, repository, _, _, _, _ = _service(space=space, rows=rows)

    with pytest.raises(TodoBotScopeError):
        service.create_todo(
            space_id=7,
            actor_id="user_1",
            title="title",
            description="details",
            bot_id="bot_1",
        )

    assert repository.calls == []


@pytest.mark.parametrize(
    "bot_space_id",
    [None, 7],
)
def test_create_todo_requires_personal_bot_owner_even_in_same_space(bot_space_id):
    service, repository, _, _, _, _ = _service(
        space=_space(space_type=SpaceType.PERSONAL, owner_id="user_1"),
        rows=[
            {
                "bot_id": "bot_1",
                "bot_name": "Foreign Owner",
                "space_id": bot_space_id,
                "owner_id": "user_2",
            }
        ],
    )

    with pytest.raises(TodoBotScopeError):
        service.create_todo(
            space_id=7,
            actor_id="user_1",
            title="title",
            description="details",
            bot_id="bot_1",
        )

    assert repository.calls == []


@pytest.mark.parametrize(
    "bot_space_id",
    [None, 7],
)
def test_create_todo_accepts_only_personal_bot_owned_by_actor(bot_space_id):
    service, repository, _, _, _, _ = _service(
        space=_space(space_type=SpaceType.PERSONAL, owner_id="user_1"),
        rows=[
            {
                "bot_id": "bot_1",
                "bot_name": "My Bot",
                "space_id": bot_space_id,
                "owner_id": "user_1",
            }
        ],
    )

    created = service.create_todo(
        space_id=7,
        actor_id="user_1",
        title="title",
        description="details",
        bot_id="bot_1",
    )

    assert created.bot_name == "My Bot"


def test_create_todo_rejects_ambiguous_bot_after_scope_filter():
    service, _, _, _, _, _ = _service(
        rows=[
            {"bot_id": "bot_1", "bot_name": "One", "space_id": 7, "owner_id": "u1"},
            {"bot_id": "bot_1", "bot_name": "Two", "space_id": 7, "owner_id": "u2"},
        ]
    )

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
    service, _, _, _, _, _ = _service()

    with pytest.raises(ValidationError):
        service.create_todo(space_id=7, actor_id="user_1", **kwargs)


def test_list_todos_checks_membership_normalizes_filters_and_paginates():
    service, repository, access, _, _, _ = _service()

    page = service.list_todos(
        space_id=7,
        actor_id="user_1",
        keyword="  feature  ",
        status="draft",
        page=3,
        page_size=20,
        env=None,
    )

    assert page.total == 1
    assert access.calls == [{"space_id": 7, "user_id": "user_1", "env": None}]
    assert repository.calls[-1] == {
        "operation": "list_todos",
        "space_id": 7,
        "keyword": "feature",
        "status": TodoStatus.DRAFT,
        "offset": 40,
        "limit": 20,
    }


def test_get_todo_rechecks_membership_using_todo_space():
    service, repository, access, _, _, _ = _service(todo=_todo(space_id=99))

    record = service.get_todo(todo_id="todo_1", actor_id="user_1", env="gray")

    assert record.todo_id == "todo_1"
    assert repository.calls == [{"operation": "get_todo", "todo_id": "todo_1"}]
    assert access.calls == [{"space_id": 99, "user_id": "user_1", "env": "gray"}]


def test_start_todo_creates_collab_context_and_persists_association():
    service, repository, access, members, _, collaboration = _service(
        rows=[
            {
                "bot_id": "bot_1",
                "bot_name": "Team Bot",
                "space_id": 7,
                "owner_id": "another_user",
            }
        ]
    )

    started = service.start_todo(todo_id="todo_1", actor_id="user_1", env="pre")

    assert started.status is TodoStatus.IN_PROGRESS
    assert started.collab_context_id == "context_todo_1"
    assert access.calls[-1] == {"space_id": 7, "user_id": "user_1", "env": "pre"}
    assert members.calls == [
        {
            "space_id": 7,
            "actor_id": "user_1",
            "keyword": None,
            "page_no": 1,
            "page_size": 100,
        }
    ]
    assert len(collaboration.context_calls) == 1
    request = collaboration.context_calls[0]
    assert request.task_id == "todo_1"
    assert request.task_title == "original"
    assert request.collaboration_goal == "details"
    assert request.collaborators == ("user_2", "user_1")
    assert request.owner_id == "user_1"
    assert request.bot_id == "bot_1"
    assert repository.calls[-1] == {
        "operation": "start_if_draft",
        "todo_id": "todo_1",
        "collab_context_id": "context_todo_1",
    }


def test_start_todo_rejects_non_draft_without_calling_b():
    service, repository, _, _, _, collaboration = _service(
        todo=_todo(status=TodoStatus.IN_PROGRESS)
    )

    with pytest.raises(TodoStateConflictError):
        service.start_todo(todo_id="todo_1", actor_id="user_1")

    assert collaboration.context_calls == []
    assert repository.calls == [{"operation": "get_todo", "todo_id": "todo_1"}]


def test_start_todo_refuses_overlarge_space_member_boundary():
    service, _, _, _, _, _ = _service(member_total=101)

    with pytest.raises(ValidationError, match="more than 100"):
        service.start_todo(todo_id="todo_1", actor_id="user_1")


def test_list_events_projects_b_events_and_marks_new_completion_pending():
    events = (
        _plugin_event(1, "execution.status", status="in_progress"),
        _plugin_event(2, "human_interaction", content="please revise"),
        _plugin_event(
            3,
            "execution.status",
            status="pending_acceptance",
            actor_type="bot",
            artifacts=(
                SimpleNamespace(
                    artifact_id="artifact_1",
                    name="report.docx",
                    url="/files/report.docx",
                ),
            ),
        ),
    )
    service, repository, _, _, _, collaboration = _service(
        todo=_todo(
            status=TodoStatus.IN_PROGRESS,
            collab_context_id="context_todo_1",
        ),
        events=events,
    )

    result = service.list_events(todo_id="todo_1", actor_id="user_1")

    assert collaboration.list_calls == ["context_todo_1"]
    assert repository.todo.status is TodoStatus.PENDING_ACCEPTANCE
    assert repository.calls[-1] == {
        "operation": "mark_pending_acceptance_if_in_progress",
        "todo_id": "todo_1",
    }
    assert [event.event_seq for event in result] == [1, 2, 3]
    assert result[-1].artifacts[0].name == "report.docx"
    assert result[-1].artifacts[0].url == "/files/report.docx"


def test_list_events_ignores_completion_before_latest_human_boundary():
    events = (
        _plugin_event(1, "execution.status", status="pending_acceptance"),
        _plugin_event(2, "human_interaction", content="please revise"),
    )
    service, repository, _, _, _, _ = _service(
        todo=_todo(
            status=TodoStatus.IN_PROGRESS,
            collab_context_id="context_todo_1",
        ),
        events=events,
    )

    service.list_events(todo_id="todo_1", actor_id="user_1")

    assert repository.todo.status is TodoStatus.IN_PROGRESS


def test_list_events_without_context_returns_empty_projection():
    service, repository, _, _, _, collaboration = _service()

    assert service.list_events(todo_id="todo_1", actor_id="user_1") == ()
    assert collaboration.list_calls == []
    assert [call["operation"] for call in repository.calls] == ["get_todo"]


def test_create_interaction_appends_boundary_event_and_resumes_todo():
    service, repository, _, _, _, collaboration = _service(
        todo=_todo(
            status=TodoStatus.PENDING_ACCEPTANCE,
            collab_context_id="context_todo_1",
        ),
        events=(_plugin_event(1, "execution.status", status="pending_acceptance"),),
    )

    event = service.create_interaction(
        todo_id="todo_1",
        actor_id="user_1",
        author_name=None,
        message="please revise section 2",
    )

    assert event.event_type == "human_interaction"
    assert repository.todo.status is TodoStatus.IN_PROGRESS
    assert len(collaboration.append_calls) == 1
    context_id, request = collaboration.append_calls[0]
    assert context_id == "context_todo_1"
    assert request.event_type == "human_interaction"
    assert request.actor_id == "user_1"
    assert request.content == "please revise section 2"
    assert request.boundary_event_seq == 1


def test_create_interaction_rejects_state_other_than_pending_acceptance():
    service, _, _, _, _, collaboration = _service(
        todo=_todo(
            status=TodoStatus.IN_PROGRESS,
            collab_context_id="context_todo_1",
        )
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
        ("rejected", TodoStatus.IN_PROGRESS),
    ],
)
def test_submit_acceptance_appends_human_event_and_transitions_product_state(
    verdict, target
):
    service, repository, _, _, _, collaboration = _service(
        todo=_todo(
            status=TodoStatus.PENDING_ACCEPTANCE,
            collab_context_id="context_todo_1",
        ),
        events=(_plugin_event(1, "execution.status", status="pending_acceptance"),),
    )

    updated = service.submit_acceptance(
        todo_id="todo_1",
        actor_id="user_1",
        verdict=verdict,
        message="looks good",
    )

    assert updated.status is target
    assert len(collaboration.append_calls) == 1
    _, request = collaboration.append_calls[0]
    assert request.event_type == "human_acceptance"
    assert request.payload == {"verdict": verdict}
    assert request.boundary_event_seq == 1
    assert repository.calls[-1] == {
        "operation": "accept_if_pending",
        "todo_id": "todo_1",
        "verdict": collaboration.append_calls[0][1].payload["verdict"],
        "message": "looks good",
        "author_id": "user_1",
    }


def test_submit_acceptance_rejects_missing_collab_context():
    service, _, _, _, _, collaboration = _service(
        todo=_todo(status=TodoStatus.PENDING_ACCEPTANCE)
    )

    with pytest.raises(TodoNotFoundError):
        service.submit_acceptance(
            todo_id="todo_1",
            actor_id="user_1",
            verdict="approved",
            message=None,
        )

    assert collaboration.append_calls == []


def test_update_todo_merges_partial_fields_and_validates_selected_bot_scope():
    service, repository, access, _, bots, _ = _service(
        rows=[
            {
                "bot_id": "bot_2",
                "bot_name": "New Bot",
                "space_id": 7,
                "owner_id": "another_user",
            }
        ]
    )

    updated = service.update_todo(
        todo_id="todo_1",
        actor_id="user_1",
        title="changed",
        description=None,
        bot_id="bot_2",
        env="pre",
    )

    assert updated.title == "changed"
    assert updated.description == "details"
    assert updated.bot_name == "New Bot"
    assert access.calls == [{"space_id": 7, "user_id": "user_1", "env": "pre"}]
    assert bots.calls == ["bot_2"]
    assert repository.calls[-1] == {
        "operation": "update_draft",
        "todo_id": "todo_1",
        "title": "changed",
        "description": "details",
        "bot_id": "bot_2",
        "bot_name": "New Bot",
    }
