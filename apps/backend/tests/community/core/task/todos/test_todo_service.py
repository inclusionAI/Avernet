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
    TodoAcceptanceTarget,
    TodoAcceptanceTargetKind,
    TodoInteractionMention,
    TodoInteractionMentionActorType,
    TodoPage,
    TodoRecord,
    TodoStatus,
)
from agentclaw.community.core.task.todos.services.todo_service import TodoService
from agentclaw.community.core.task.todos.source_models import TodoSourceInput
from agentclaw.community.core.task.todos.collaboration.protocol import (
    TodoCollaborationArtifact,
    TodoCollaborationContext,
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
        "status": TodoStatus.PENDING,
        "collab_context_id": None,
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
    status: str = "done",
    version: int = 3,
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
        return [row for row in self.rows if row.get("bot_id") == bot_id]


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
            created_by=kwargs["created_by"],
        )
        self.todos[todo.todo_id] = todo
        return todo

    def get_todo(self, *, todo_id: str) -> TodoRecord:
        self._record("get_todo", todo_id=todo_id)
        return self.todos[todo_id]

    def transition_status(
        self,
        *,
        todo_id: str,
        expected_status: TodoStatus,
        new_status: TodoStatus,
    ) -> TodoRecord:
        self._record(
            "transition_status",
            todo_id=todo_id,
            expected_status=expected_status,
            new_status=new_status,
        )
        todo = self.todos[todo_id]
        if todo.status is not expected_status:
            raise TodoStateConflictError("concurrent status transition")
        updated = replace(todo, status=new_status)
        self.todos[todo_id] = updated
        return updated

    def list_todos(self, **kwargs) -> TodoPage:
        self._record("list_todos", **kwargs)
        return TodoPage(total=len(self.todos), items=tuple(self.todos.values()))

    def record_acceptance(self, **kwargs) -> TodoRecord:
        self._record("record_acceptance", **kwargs)
        todo = self.todos[kwargs["todo_id"]]
        target = (
            TodoStatus.COMPLETED
            if kwargs["verdict"] is AcceptanceVerdict.APPROVED
            else TodoStatus.IN_PROGRESS
        )
        todo = replace(todo, status=target)
        self.todos[todo.todo_id] = todo
        return todo


class FakeExecution:
    def __init__(self, result: TodoRecord | None = None) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def dispatch_pending(
        self, *, todo_id: str, env: str | None = None
    ) -> TodoRecord:
        self.calls.append({"todo_id": todo_id, "env": env})
        if self.result is None:
            raise AssertionError("Unexpected Todo execution dispatch")
        return self.result

    async def refresh_in_progress(self, *, todo_id: str) -> TodoRecord:
        raise AssertionError("TodoService must not refresh scheduler state")

    async def resume_claimed_interaction(self, *, todo_id: str) -> TodoRecord:
        self.calls.append(
            {"operation": "resume_claimed_interaction", "todo_id": todo_id}
        )
        if self.result is None:
            return _todo(status=TodoStatus.IN_PROGRESS)
        return self.result


class FakeCollaboration:
    def __init__(self, contexts: tuple[TodoCollaborationContext, ...] = ()) -> None:
        self.contexts = {context.collab_context_id: context for context in contexts}
        self.create_calls: list[object] = []
        self.get_calls: list[tuple[str, str]] = []
        self.run_calls: list[tuple[str, str]] = []
        self.append_calls: list[tuple[str, TodoCollaborationEventRequest]] = []

    def create_context(self, request):
        self.create_calls.append(request)
        raise AssertionError("Todo creation must not create a Context")

    def get_context(
        self, *, collab_context_id: str, actor_id: str
    ) -> TodoCollaborationContext:
        self.get_calls.append((collab_context_id, actor_id))
        return self.contexts[collab_context_id]

    async def run_context(self, *, collab_context_id: str, actor_id: str):
        self.run_calls.append((collab_context_id, actor_id))
        raise AssertionError("Todo creation and reads must not run a Context")

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
            payload=dict(request.payload),
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
    execution_result: TodoRecord | None = None,
):
    repository = FakeTodos(todos or (_todo(),))
    access = FakeSpaceAccess(space)
    bots = FakeBots(rows or [])
    collaboration = FakeCollaboration(contexts)
    execution = FakeExecution(execution_result)
    service = TodoService(
        repository,
        access,
        bots,
        collaboration,
        execution,
    )
    return service, repository, access, bots, collaboration, None


def _team_bot(
    name: str = "Team Bot",
    *,
    bot_id: str = "bot_1",
    space_id: int = 7,
) -> dict[str, Any]:
    return {
        "bot_id": bot_id,
        "bot_name": name,
        "space_id": space_id,
        "owner_id": "another_user",
    }


def test_create_todo_persists_pending_without_creating_or_running_context():
    service, repository, access, bots, collaboration, _ = _service(rows=[_team_bot()])

    created = service.create_todo(
        space_id=7,
        actor_id="user_1",
        title="  Ship feature  ",
        description="  Detailed request  ",
        bot_id="  bot_1  ",
        env="pre",
    )

    assert created.status is TodoStatus.PENDING
    assert created.collab_context_id is None
    assert access.calls == [{"space_id": 7, "user_id": "user_1", "env": "pre"}]
    assert bots.calls == ["bot_1"]
    assert collaboration.create_calls == []
    assert collaboration.run_calls == []
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


def test_create_todo_normalizes_and_forwards_dima_source():
    service, repository, _, _, _, _ = _service(rows=[_team_bot()])

    service.create_todo(
        space_id=7,
        actor_id="user_1",
        title="title",
        description="details",
        bot_id="bot_1",
        source=TodoSourceInput(
            source_type="dima",
            container_id="  P1  ",
            item_id="  work_1  ",
            url="  https://dima.example/items/work_1  ",
            updated_at="  2026-11-20T12:00:00Z  ",
            metadata={" processor_id ": " 149844 ", "empty": "  "},
        ),
    )

    assert repository.calls[-1]["source"] == TodoSourceInput(
        source_type="dima",
        container_id="P1",
        item_id="work_1",
        url="https://dima.example/items/work_1",
        updated_at="2026-11-20T12:00:00Z",
        metadata={"processor_id": "149844"},
    )


@pytest.mark.parametrize(
    "source,match",
    [
        (
            TodoSourceInput(
                source_type="github",  # type: ignore[arg-type]
                container_id="P1",
                item_id="work_1",
            ),
            "source type",
        ),
        (
            TodoSourceInput(
                source_type="dima",
                container_id=" ",
                item_id="work_1",
            ),
            "container_id",
        ),
        (
            TodoSourceInput(
                source_type="dima",
                container_id="P1",
                item_id=" ",
            ),
            "item_id",
        ),
        (
            TodoSourceInput(
                source_type="dima",
                container_id="P1",
                item_id="work_1",
                url="not-a-url",
            ),
            "source url",
        ),
        (
            TodoSourceInput(
                source_type="dima",
                container_id="P1",
                item_id="work_1",
                updated_at="yesterday",
            ),
            "updated_at",
        ),
        (
            TodoSourceInput(
                source_type="dima",
                container_id="P1",
                item_id="work_1",
                metadata={"processor_id": 149844},  # type: ignore[dict-item]
            ),
            "keys and values",
        ),
    ],
)
def test_create_todo_rejects_invalid_source(source, match):
    service, repository, _, _, _, _ = _service(rows=[_team_bot()])

    with pytest.raises(ValidationError, match=match):
        service.create_todo(
            space_id=7,
            actor_id="user_1",
            title="title",
            description="details",
            bot_id="bot_1",
            source=source,
        )

    assert repository.calls == []


def test_create_todo_does_not_require_public_catalog_visibility():
    bot = {**_team_bot(), "visibility": "private", "user_visibility": "private"}
    service, repository, _, _, collaboration, _ = _service(rows=[bot])

    created = service.create_todo(
        space_id=7,
        actor_id="user_1",
        title="title",
        description="details",
        bot_id="bot_1",
    )

    assert created.status is TodoStatus.PENDING
    assert repository.calls[-1]["operation"] == "create_todo"
    assert collaboration.create_calls == []


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
    service, repository, _, _, collaboration, _ = _service(space=space, rows=rows)

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


@pytest.mark.parametrize("owner_id", [pytest.param(None, id="none"), "", "  "])
def test_create_todo_rejects_team_bot_without_owner_identity(owner_id):
    bot = _team_bot()
    if owner_id is None:
        bot.pop("owner_id")
    else:
        bot["owner_id"] = owner_id
    service, repository, _, _, collaboration, _ = _service(rows=[bot])

    with pytest.raises(TodoBotScopeError, match="owner identity"):
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
    service, _, _, _, _, _ = _service(rows=[_team_bot("One"), _team_bot("Two")])

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


def test_list_todos_uses_persisted_status_filter_and_database_pagination():
    persisted = TodoPage(
        total=2,
        items=(_todo(todo_id="todo_2", status=TodoStatus.PENDING_ACCEPTANCE),),
    )
    service, repository, access, _, collaboration, _ = _service()
    repository.list_todos = lambda **kwargs: (
        repository._record("list_todos", **kwargs) or persisted
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

    assert page == persisted
    assert access.calls == [{"space_id": 7, "user_id": "user_1", "env": None}]
    assert repository.calls == [
        {
            "operation": "list_todos",
            "space_id": 7,
            "keyword": "feature",
            "status": TodoStatus.PENDING_ACCEPTANCE,
            "bot_id": "bot_1",
            "page": 2,
            "page_size": 1,
        }
    ]
    assert collaboration.get_calls == []


def test_get_todo_returns_persisted_status_without_reading_context():
    todo = _todo(
        status=TodoStatus.IN_PROGRESS,
        collab_context_id="context_todo_1",
    )
    service, _, _, _, collaboration, _ = _service(todos=(todo,))

    result = service.get_todo(todo_id="todo_1", actor_id="user_1")

    assert result is todo
    assert collaboration.get_calls == []


def test_list_events_without_context_is_empty():
    service, repository, _, _, collaboration, _ = _service(todos=(_todo(),))

    assert service.list_events(todo_id="todo_1", actor_id="user_1") == ()
    assert [call["operation"] for call in repository.calls] == ["get_todo"]
    assert collaboration.get_calls == []


def test_list_events_maps_context_artifacts():
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
    todo = _todo(
        status=TodoStatus.PENDING_ACCEPTANCE,
        collab_context_id="context_todo_1",
    )
    service, _, _, _, collaboration, _ = _service(
        todos=(todo,),
        contexts=(_context(events=(event,)),),
    )

    result = service.list_events(todo_id="todo_1", actor_id="user_1")

    assert result[0].event_type == "task.completed"
    assert result[0].context_version == 3
    assert result[0].artifacts[0].name == "report.docx"
    assert collaboration.get_calls == [("context_todo_1", "user_1")]


def test_list_interactions_returns_durable_human_bot_and_artifact_events():
    events = (
        _event(1, "task.created", content="original request"),
        _event(2, "task.started", actor_type="bot", content=""),
        _event(
            3,
            "artifact.reported",
            actor_type="bot",
            content="产出文本：first result",
            created_at="2026-11-10T08:00:03Z",
            payload={
                "artifact_id": "text_1",
                "todo_execution_id": "todoexec_1",
                "run_id": "run_1",
            },
        ),
        _event(4, "user_interaction.posted", content="please revise"),
        _event(
            5,
            "artifact.reported",
            actor_type="bot",
            content="产出文本：second result",
            created_at="2026-11-10T08:00:06Z",
            payload={
                "artifact_id": "text_2",
                "todo_execution_id": "todoexec_2",
                "run_id": "run_2",
            },
        ),
        _event(6, "task.completed", actor_type="bot", content=""),
    )
    todo = _todo(
        status=TodoStatus.IN_PROGRESS,
        collab_context_id="context_todo_1",
    )
    service, _, _, _, collaboration, _ = _service(
        todos=(todo,),
        contexts=(_context(events=events),),
    )

    result = service.list_interactions(todo_id="todo_1", actor_id="user_1", env="pre")

    assert [item.event_type for item in result] == [
        "task.created",
        "artifact.reported",
        "user_interaction.posted",
        "artifact.reported",
        "task.completed",
    ]
    assert [item.content for item in result] == [
        "original request",
        "产出文本：first result",
        "please revise",
        "产出文本：second result",
        "",
    ]
    assert result[1].payload["artifact_id"] == "text_1"
    assert result[3].payload["todo_execution_id"] == "todoexec_2"
    assert result[3].payload["run_id"] == "run_2"
    assert collaboration.get_calls == [("context_todo_1", "user_1")]


def test_list_interactions_returns_durable_human_events_before_completion_text():
    events = (
        _event(1, "task.created", content="original request"),
        _event(2, "user_interaction.posted", content="please revise"),
    )
    todo = _todo(
        status=TodoStatus.PENDING_ACCEPTANCE,
        collab_context_id="context_todo_1",
    )
    service, _, _, _, collaboration, _ = _service(
        todos=(todo,),
        contexts=(_context(events=events),),
    )

    result = service.list_interactions(todo_id="todo_1", actor_id="user_1")

    assert [item.event_type for item in result] == [
        "task.created",
        "user_interaction.posted",
    ]
    assert [item.content for item in result] == ["original request", "please revise"]
    assert collaboration.get_calls == [("context_todo_1", "user_1")]


@pytest.mark.asyncio
async def test_create_interaction_persists_event_and_claim_without_execution():
    todo = _todo(
        status=TodoStatus.PENDING_ACCEPTANCE,
        collab_context_id="context_todo_1",
    )
    service, repository, _, execution, collaboration, _ = _service(
        todos=(todo,),
        contexts=(_context(),),
    )

    event = await service.create_interaction(
        todo_id="todo_1",
        actor_id="user_1",
        author_name="User One",
        message="please revise section 2",
    )

    assert event.event_type == "user_interaction.posted"
    context_id, request = collaboration.append_calls[0]
    assert context_id == "context_todo_1"
    assert request.expected_version == 3
    assert request.payload == {
        "content": "please revise section 2",
        "author_name": "User One",
    }
    assert repository.todos["todo_1"].status is TodoStatus.IN_PROGRESS
    assert execution.calls == []


@pytest.mark.asyncio
async def test_create_interaction_persists_reply_and_same_space_bot_mentions():
    reply = _event(2, "artifact.reported", actor_type="bot")
    todo = _todo(
        status=TodoStatus.PENDING_ACCEPTANCE,
        collab_context_id="context_todo_1",
    )
    service, repository, _, bots, collaboration, _ = _service(
        rows=[_team_bot("Review Bot", bot_id="bot_2")],
        todos=(todo,),
        contexts=(_context(events=(reply,)),),
    )

    event = await service.create_interaction(
        todo_id="todo_1",
        actor_id="user_1",
        author_name=None,
        message="please verify this result",
        reply_to_event_id="event_2",
        mentions=(
            TodoInteractionMention(
                actor_type=TodoInteractionMentionActorType.BOT,
                actor_id="bot_2",
            ),
        ),
    )

    assert event.payload == {
        "content": "please verify this result",
        "reply_to_event_id": "event_2",
        "mentions": [{"actor_type": "bot", "actor_id": "bot_2"}],
    }
    assert bots.calls == ["bot_2"]
    assert repository.todos["todo_1"].status is TodoStatus.IN_PROGRESS
    assert collaboration.append_calls[0][1].parent_event == "event_2"


@pytest.mark.asyncio
async def test_create_interaction_rejects_unknown_reply_before_claiming_status():
    todo = _todo(
        status=TodoStatus.PENDING_ACCEPTANCE,
        collab_context_id="context_todo_1",
    )
    service, repository, _, _, collaboration, _ = _service(
        todos=(todo,),
        contexts=(_context(events=(_event(1, "task.started"),)),),
    )

    with pytest.raises(ValidationError, match="visible interaction event"):
        await service.create_interaction(
            todo_id="todo_1",
            actor_id="user_1",
            author_name=None,
            message="reply",
            reply_to_event_id="event_1",
        )

    assert repository.todos["todo_1"].status is TodoStatus.PENDING_ACCEPTANCE
    assert collaboration.append_calls == []


@pytest.mark.asyncio
async def test_create_interaction_rejects_completion_notice_as_reply_target():
    todo = _todo(
        status=TodoStatus.PENDING_ACCEPTANCE,
        collab_context_id="context_todo_1",
    )
    service, repository, _, _, collaboration, _ = _service(
        todos=(todo,),
        contexts=(_context(events=(_event(1, "task.completed"),)),),
    )

    with pytest.raises(ValidationError, match="visible interaction event"):
        await service.create_interaction(
            todo_id="todo_1",
            actor_id="user_1",
            author_name=None,
            message="reply",
            reply_to_event_id="event_1",
        )

    assert repository.todos["todo_1"].status is TodoStatus.PENDING_ACCEPTANCE
    assert collaboration.append_calls == []


@pytest.mark.asyncio
async def test_create_interaction_rejects_duplicate_mentions_before_claiming_status():
    todo = _todo(
        status=TodoStatus.PENDING_ACCEPTANCE,
        collab_context_id="context_todo_1",
    )
    mention = TodoInteractionMention(
        actor_type=TodoInteractionMentionActorType.BOT,
        actor_id="bot_2",
    )
    service, repository, _, bots, collaboration, _ = _service(
        rows=[_team_bot(bot_id="bot_2")],
        todos=(todo,),
        contexts=(_context(),),
    )

    with pytest.raises(ValidationError, match="unique"):
        await service.create_interaction(
            todo_id="todo_1",
            actor_id="user_1",
            author_name=None,
            message="mention twice",
            mentions=(mention, mention),
        )

    assert bots.calls == []
    assert repository.todos["todo_1"].status is TodoStatus.PENDING_ACCEPTANCE
    assert collaboration.append_calls == []


@pytest.mark.asyncio
async def test_create_interaction_rejects_mentioned_bot_outside_todo_space():
    todo = _todo(
        status=TodoStatus.PENDING_ACCEPTANCE,
        collab_context_id="context_todo_1",
    )
    service, repository, _, bots, collaboration, _ = _service(
        rows=[_team_bot(bot_id="bot_2", space_id=8)],
        todos=(todo,),
        contexts=(_context(),),
    )

    with pytest.raises(TodoBotScopeError):
        await service.create_interaction(
            todo_id="todo_1",
            actor_id="user_1",
            author_name=None,
            message="mention outside space",
            mentions=(
                TodoInteractionMention(
                    actor_type=TodoInteractionMentionActorType.BOT,
                    actor_id="bot_2",
                ),
            ),
        )

    assert bots.calls == ["bot_2"]
    assert repository.todos["todo_1"].status is TodoStatus.PENDING_ACCEPTANCE
    assert collaboration.append_calls == []


@pytest.mark.asyncio
async def test_create_interaction_rejects_non_pending_acceptance_without_reading_context():
    todo = _todo(
        status=TodoStatus.IN_PROGRESS,
        collab_context_id="context_todo_1",
    )
    service, _, _, _, collaboration, _ = _service(todos=(todo,))

    with pytest.raises(TodoStateConflictError):
        await service.create_interaction(
            todo_id="todo_1",
            actor_id="user_1",
            author_name=None,
            message="message",
        )

    assert collaboration.get_calls == []
    assert collaboration.append_calls == []


@pytest.mark.parametrize(
    "verdict,target,message,targets,event_type",
    [
        ("approved", TodoStatus.COMPLETED, "looks good", (), "task.accepted"),
        (
            "rejected",
            TodoStatus.IN_PROGRESS,
            "please revise",
            (
                TodoAcceptanceTarget(
                    kind=TodoAcceptanceTargetKind.EVENT,
                    target_id="event_2",
                    floor=2,
                ),
            ),
            "task.rejected",
        ),
    ],
)
def test_submit_acceptance_appends_decision_event_before_projecting_todo(
    verdict, target, message, targets, event_type
):
    todo = _todo(
        status=TodoStatus.PENDING_ACCEPTANCE,
        collab_context_id="context_todo_1",
    )
    service, repository, _, _, collaboration, _ = _service(
        todos=(todo,),
        contexts=(_context(),),
    )

    updated = service.submit_acceptance(
        todo_id="todo_1",
        actor_id="user_1",
        verdict=verdict,
        decision_id="decision_1",
        message=message,
        targets=targets,
    )

    assert updated.status is target
    assert collaboration.get_calls == [("context_todo_1", "user_1")]
    context_id, request = collaboration.append_calls[0]
    assert context_id == "context_todo_1"
    assert request.event_kind == event_type
    assert request.actor_id == "user_1"
    assert request.actor_type == "human"
    assert request.payload == {
        "decision_id": "decision_1",
        "todo_id": "todo_1",
        "verdict": verdict,
        "content": message,
        "author_id": "user_1",
        "targets": [
            {"kind": item.kind.value, "id": item.target_id, "floor": item.floor}
            for item in targets
        ],
    }
    assert repository.calls[-1] == {
        "operation": "record_acceptance",
        "todo_id": "todo_1",
        "acceptance_id": "decision_1",
        "verdict": AcceptanceVerdict(verdict),
        "message": message,
        "author_id": "user_1",
    }


def test_submit_acceptance_same_decision_id_reuses_event_without_duplicate_append():
    todo = _todo(
        status=TodoStatus.PENDING_ACCEPTANCE,
        collab_context_id="context_todo_1",
    )
    service, repository, _, _, collaboration, _ = _service(
        todos=(todo,),
        contexts=(_context(),),
    )

    first = service.submit_acceptance(
        todo_id="todo_1",
        actor_id="user_1",
        verdict="approved",
        decision_id="decision_1",
        message="looks good",
    )
    second = service.submit_acceptance(
        todo_id="todo_1",
        actor_id="user_1",
        verdict="approved",
        decision_id="decision_1",
        message="looks good",
    )

    assert first.status is TodoStatus.COMPLETED
    assert second.status is TodoStatus.COMPLETED
    assert len(collaboration.append_calls) == 1
    acceptance_calls = [
        call for call in repository.calls if call["operation"] == "record_acceptance"
    ]
    assert len(acceptance_calls) == 2
    assert acceptance_calls[0] == acceptance_calls[1]


def test_submit_acceptance_rejects_changed_payload_for_existing_decision_id():
    event = _event(
        4,
        "task.accepted",
        payload={
            "decision_id": "decision_1",
            "todo_id": "todo_1",
            "verdict": "approved",
            "content": "original",
            "author_id": "user_1",
            "targets": [],
        },
    )
    service, repository, _, _, collaboration, _ = _service(
        todos=(
            _todo(
                status=TodoStatus.COMPLETED,
                collab_context_id="context_todo_1",
            ),
        ),
        contexts=(_context(events=(event,)),),
    )

    with pytest.raises(TodoStateConflictError, match="decision_id"):
        service.submit_acceptance(
            todo_id="todo_1",
            actor_id="user_1",
            verdict="approved",
            decision_id="decision_1",
            message="changed",
        )

    assert collaboration.append_calls == []
    assert all(call["operation"] != "record_acceptance" for call in repository.calls)


def test_submit_acceptance_rejects_second_decision_in_same_acceptance_round():
    event = _event(
        4,
        "task.accepted",
        payload={
            "decision_id": "decision_1",
            "todo_id": "todo_1",
            "verdict": "approved",
            "content": "looks good",
            "author_id": "user_1",
            "targets": [],
        },
    )
    service, repository, _, _, collaboration, _ = _service(
        todos=(
            _todo(
                status=TodoStatus.PENDING_ACCEPTANCE,
                collab_context_id="context_todo_1",
            ),
        ),
        contexts=(_context(events=(event,)),),
    )

    with pytest.raises(TodoStateConflictError, match="already has"):
        service.submit_acceptance(
            todo_id="todo_1",
            actor_id="user_1",
            verdict="approved",
            decision_id="decision_2",
            message="also looks good",
        )

    assert collaboration.append_calls == []
    assert all(call["operation"] != "record_acceptance" for call in repository.calls)


def test_submit_acceptance_rejects_decision_id_from_prior_acceptance_round():
    rejection = _event(
        2,
        "task.rejected",
        content="revise enterprise pricing",
        payload={
            "decision_id": "decision_old",
            "todo_id": "todo_1",
            "verdict": "rejected",
            "content": "revise enterprise pricing",
            "author_id": "user_1",
            "targets": [{"kind": "event", "id": "event_1", "floor": 1}],
        },
    )
    context = _context(
        events=(
            _event(1, "task.completed"),
            rejection,
            _event(3, "task.started", actor_id="bot_1", actor_type="bot"),
            _event(4, "task.completed", actor_id="bot_1", actor_type="bot"),
        ),
    )
    service, repository, _, _, collaboration, _ = _service(
        todos=(
            _todo(
                status=TodoStatus.PENDING_ACCEPTANCE,
                collab_context_id=context.collab_context_id,
            ),
        ),
        contexts=(context,),
    )

    with pytest.raises(TodoStateConflictError, match="earlier acceptance round"):
        service.submit_acceptance(
            todo_id="todo_1",
            actor_id="user_1",
            verdict="rejected",
            decision_id="decision_old",
            message="revise enterprise pricing",
            targets=(
                TodoAcceptanceTarget(
                    kind=TodoAcceptanceTargetKind.EVENT,
                    target_id="event_1",
                    floor=1,
                ),
            ),
        )

    assert collaboration.append_calls == []
    assert all(call["operation"] != "record_acceptance" for call in repository.calls)


def test_submit_acceptance_can_retry_a_projection_after_b_append_succeeds():
    todo = _todo(
        status=TodoStatus.PENDING_ACCEPTANCE,
        collab_context_id="context_todo_1",
    )
    service, repository, _, _, collaboration, _ = _service(
        todos=(todo,),
        contexts=(_context(),),
    )
    original_record = repository.record_acceptance
    failed_once = False

    def fail_first_projection(**kwargs):
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            raise RuntimeError("database unavailable")
        return original_record(**kwargs)

    repository.record_acceptance = fail_first_projection

    with pytest.raises(RuntimeError, match="database unavailable"):
        service.submit_acceptance(
            todo_id="todo_1",
            actor_id="user_1",
            verdict="approved",
            decision_id="decision_1",
            message=None,
        )

    updated = service.submit_acceptance(
        todo_id="todo_1",
        actor_id="user_1",
        verdict="approved",
        decision_id="decision_1",
        message=None,
    )

    assert updated.status is TodoStatus.COMPLETED
    assert len(collaboration.append_calls) == 1


def test_submit_acceptance_rejects_non_pending_acceptance_without_decision_event():
    context = _context(status="in_progress")
    service, repository, _, _, collaboration, _ = _service(
        todos=(
            _todo(
                status=TodoStatus.IN_PROGRESS,
                collab_context_id=context.collab_context_id,
            ),
        ),
        contexts=(context,),
    )

    with pytest.raises(TodoStateConflictError, match="pending acceptance"):
        service.submit_acceptance(
            todo_id="todo_1",
            actor_id="user_1",
            verdict="approved",
            decision_id="decision_1",
            message=None,
        )

    assert collaboration.append_calls == []
    assert all(call["operation"] != "record_acceptance" for call in repository.calls)


@pytest.mark.asyncio
async def test_start_todo_delegates_to_execution_after_actor_space_check():
    started = _todo(status=TodoStatus.IN_PROGRESS, collab_context_id="context_1")
    service, _, access, _, collaboration, _ = _service(execution_result=started)

    result = await service.start_todo(
        todo_id="todo_1",
        actor_id="user_1",
        env="pre",
    )

    assert result is started
    assert access.calls == [{"space_id": 7, "user_id": "user_1", "env": "pre"}]
    assert service._execution.calls == [{"todo_id": "todo_1", "env": "pre"}]
    assert collaboration.run_calls == []


@pytest.mark.asyncio
async def test_start_todo_rejects_non_pending_before_execution_dispatch():
    service, _, _, _, _, _ = _service(
        todos=(_todo(status=TodoStatus.IN_PROGRESS),),
    )

    with pytest.raises(TodoStateConflictError):
        await service.start_todo(todo_id="todo_1", actor_id="user_1")

    assert service._execution.calls == []


def test_create_todo_with_owner_persists_todo_owner_binding():
    from agentclaw.community.core.task.todos.product_creation_models import TodoOwnerInput
    from agentclaw.community.core.task.todos.stage_models import TodoParticipantBinding

    service, repository, _, _, _, _ = _service(rows=[_team_bot()])

    service.create_todo(
        space_id=7,
        actor_id="user_1",
        title="title",
        description="details",
        bot_id="bot_1",
        owner=TodoOwnerInput(user_id=" owner_1 ", user_name=" Owner One "),
    )

    assert repository.calls[-1]["participant_bindings"] == (
        TodoParticipantBinding(
            key="todo_owner",
            label="待办负责人",
            subject_type="human",
            subject_id="owner_1",
            subject_name="Owner One",
        ),
    )
