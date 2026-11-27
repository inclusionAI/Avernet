from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import Request, Response
from pydantic import ValidationError as PydanticValidationError

from agentclaw.community.adapters.http.openapi_v1.responses import mapped_error_response
from agentclaw.community.adapters.http.openapi_v1.task.todos.auto_import_agent_router import (
    create_todo_from_dima_auto_import_rule,
    get_dima_auto_import_rule,
    list_active_dima_auto_import_rules,
    list_dima_auto_import_progress,
    record_dima_auto_import_checkpoint,
)
from agentclaw.community.adapters.http.openapi_v1.task.todos.auto_import_agent_router import (
    router as auto_import_agent_router,
)
from agentclaw.community.adapters.http.openapi_v1.task.todos.dima_import_schemas import (
    ImportDimaTodosRequest,
)
from agentclaw.community.adapters.http.openapi_v1.task.todos.router import (
    create_dima_auto_import_rule,
    create_todo,
    create_todo_interaction,
    get_dima_work_item_draft,
    get_todo,
    get_todo_progress,
    import_dima_todos,
    list_dima_auto_import_rules,
    list_dima_projects,
    list_dima_work_items,
    list_todo_artifacts,
    list_todo_events,
    list_todo_interactions,
    list_todos,
    read_todo_artifact_content,
    run_todo,
    submit_todo_acceptance,
    update_dima_auto_import_rule,
)
from agentclaw.community.adapters.http.openapi_v1.task.todos.schemas import (
    CreateTodoAutoImportRuleRequest,
    CreateTodoFromAutoImportRuleRequest,
    CreateTodoInteractionRequest,
    CreateTodoRequest,
    SubmitTodoAcceptanceRequest,
    TodoAutoImportCheckpointRequest,
    UpdateTodoAutoImportRuleRequest,
)
from agentclaw.community.core.task.todos.artifact_models import (
    TodoArtifactDownload,
    TodoArtifactKind,
    TodoArtifactRecord,
)
from agentclaw.community.core.task.todos.auto_import_models import (
    TodoAutoImportCheckpoint,
    TodoAutoImportProgressRecord,
    TodoAutoImportProgressStatus,
    TodoAutoImportRuleRecord,
    TodoAutoImportRuleStatus,
)
from agentclaw.community.core.task.todos.errors import (
    TodoAutoImportCursorConflictError,
    TodoSourceAlreadyImportedError,
)
from agentclaw.community.core.task.todos.models import (
    AcceptanceVerdict,
    TodoEventRecord,
    TodoPage,
    TodoRecord,
    TodoStatus,
)
from agentclaw.community.core.task.todos.models import (
    TodoArtifactRecord as TodoEventArtifactRecord,
)
from agentclaw.community.core.task.todos.product_creation_models import (
    EnsureProductColletResult,
    TodoDimaImportResult,
    TodoDimaImportStatus,
    TodoExecutorKind,
    TodoExecutorSelection,
    TodoOwnerInput,
    TodoProductColletStatus,
)
from agentclaw.community.core.task.todos.source_models import (
    TodoSourceDraft,
    TodoSourceInput,
    TodoSourceProject,
    TodoSourceWorkItem,
)

NOW = datetime(2026, 11, 10, 8, 30, tzinfo=timezone.utc)
AUTO_IMPORT_TIME = datetime(2026, 11, 25, 8, 0, tzinfo=timezone.utc)


def _request(method: str, path: str) -> Request:
    request = Request(scope={"type": "http", "method": method, "path": path})
    request.state.trace_id = "trace-todo"
    return request


def _artifact() -> TodoEventArtifactRecord:
    return TodoEventArtifactRecord(
        artifact_id="artifact_1",
        name="report.docx",
        url="/files/report.docx",
    )


def _event() -> TodoEventRecord:
    return TodoEventRecord(
        event_id="event_3",
        event_seq=3,
        event_type="task.completed",
        actor_id="bot_1",
        actor_type="bot",
        content="done",
        execution_id="execution_1",
        context_version=2,
        status="pending_acceptance",
        artifacts=(_artifact(),),
        created_at="2026-11-10T08:00:00Z",
        payload={"content": "done", "result_kind": "text"},
    )


def _todo(
    *,
    status: TodoStatus = TodoStatus.IN_PROGRESS,
    collab_context_id: str | None = "context_todo_1",
    owner_id: str = "human_1",
    owner_name: str = "Human One",
) -> TodoRecord:
    return TodoRecord(
        todo_id="todo_1",
        space_id=42,
        title="Prepare release notes",
        description="Summarize the changes",
        bot_id="bot_1",
        bot_name="Release Bot",
        status=status,
        collab_context_id=collab_context_id,
        created_by="human_1",
        created_at=NOW,
        updated_at=NOW,
        owner_id=owner_id,
        owner_name=owner_name,
    )


def _interaction_event() -> TodoEventRecord:
    return TodoEventRecord(
        event_id="event_2",
        event_seq=2,
        event_type="user_interaction.posted",
        actor_id="human_1",
        actor_type="human",
        content="Please revise section 2",
        execution_id="execution_1",
        context_version=2,
        status=None,
        artifacts=(),
        created_at="2026-11-10T07:55:00Z",
        payload={"content": "Please revise section 2"},
    )


class _PageParams:
    def __init__(self, page: int, page_size: int) -> None:
        self.page = page
        self.page_size = page_size


class _RecordedService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def _record(self, op: str, **kwargs: Any):
        self.calls.append({"op": op, **kwargs})

    def list_todos(self, **kwargs):
        self._record("list_todos", **kwargs)
        return TodoPage(total=1, items=(_todo(),))

    def get_todo(self, **kwargs):
        self._record("get_todo", **kwargs)
        return _todo()

    def create_todo(self, **kwargs):
        self._record("create_todo", **kwargs)
        return _todo(status=TodoStatus.PENDING, collab_context_id=None)

    async def start_todo(self, **kwargs):
        self._record("start_todo", **kwargs)
        return _todo()

    def list_events(self, **kwargs):
        self._record("list_events", **kwargs)
        return (_event(),)

    def list_interactions(self, **kwargs):
        self._record("list_interactions", **kwargs)
        return (_interaction_event(),)

    async def create_interaction(self, **kwargs):
        self._record("create_interaction", **kwargs)
        return TodoEventRecord(
            event_id="event_4",
            event_seq=4,
            event_type="user_interaction.posted",
            actor_id="human_1",
            actor_type="human",
            content="Please include the rollback plan",
            execution_id="execution_1",
            context_version=3,
            status="in_progress",
            artifacts=(),
            created_at="2026-11-10T08:05:00Z",
            payload={"content": "Please include the rollback plan"},
        )

    def submit_acceptance(self, **kwargs):
        self._record("submit_acceptance", **kwargs)
        status = (
            TodoStatus.COMPLETED
            if kwargs["verdict"] == AcceptanceVerdict.APPROVED
            else TodoStatus.IN_PROGRESS
        )
        return _todo(status=status)


class _RecordedProductCreationService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.runtime_calls: list[dict[str, Any]] = []
        self.last_todo: TodoRecord | None = None

    def create_todo(self, **kwargs: Any) -> TodoRecord:
        self.calls.append(kwargs)
        owner = kwargs["owner"]
        self.last_todo = _todo(
            status=TodoStatus.PENDING,
            collab_context_id=None,
            owner_id=owner.user_id,
            owner_name=owner.user_name,
        )
        assert self.last_todo is not None
        return self.last_todo

    async def ensure_product_collet(self, **kwargs: Any) -> EnsureProductColletResult:
        self.runtime_calls.append(kwargs)
        assert self.last_todo is not None
        return EnsureProductColletResult(
            status=TodoProductColletStatus.NOT_APPLICABLE,
            todo=self.last_todo,
        )


class _RecordedDimaImportService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def import_dima_todos(self, **kwargs: Any):
        self.calls.append(kwargs)
        return (
            TodoDimaImportResult(
                source_work_item_id="DIM-1",
                status=TodoDimaImportStatus.IMPORTED,
                todo_id="todo_1",
                product_runtime_status=TodoProductColletStatus.READY,
            ),
            TodoDimaImportResult(
                source_work_item_id="DIM-2",
                status=TodoDimaImportStatus.DUPLICATE,
                todo_id="todo_existing",
                product_runtime_status=TodoProductColletStatus.READY,
            ),
        )


def _auto_import_rule(
    *,
    status: TodoAutoImportRuleStatus = TodoAutoImportRuleStatus.ENABLED,
) -> TodoAutoImportRuleRecord:
    return TodoAutoImportRuleRecord(
        rule_id="rule_1",
        space_id=42,
        source_container_ids=("W1", "P1"),
        filter_text="只导入需要研发处理且尚未完成的事项",
        bot_id="bot_1",
        status=status,
        cursor_updated_at=AUTO_IMPORT_TIME.replace(tzinfo=None),
        created_by="human_1",
        created_at=AUTO_IMPORT_TIME.replace(tzinfo=None),
        updated_at=AUTO_IMPORT_TIME.replace(tzinfo=None),
    )


def _auto_import_progress() -> TodoAutoImportProgressRecord:
    return TodoAutoImportProgressRecord(
        progress_id="progress_1",
        rule_id="rule_1",
        source_container_id="W1",
        source_item_id="item_1",
        source_updated_at=AUTO_IMPORT_TIME.replace(tzinfo=None),
        status=TodoAutoImportProgressStatus.IMPORTED,
        todo_id="todo_1",
        detail="created",
        created_at=AUTO_IMPORT_TIME.replace(tzinfo=None),
        updated_at=AUTO_IMPORT_TIME.replace(tzinfo=None),
    )


class _RecordedAutoImportService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def _record(self, operation: str, **kwargs: Any) -> None:
        self.calls.append({"operation": operation, **kwargs})

    def create_rule(self, **kwargs: Any) -> TodoAutoImportRuleRecord:
        self._record("create_rule", **kwargs)
        return _auto_import_rule()

    def list_rules(self, **kwargs: Any) -> tuple[TodoAutoImportRuleRecord, ...]:
        self._record("list_rules", **kwargs)
        return (_auto_import_rule(),)

    def list_active_rules(self, **kwargs: Any) -> tuple[TodoAutoImportRuleRecord, ...]:
        self._record("list_active_rules", **kwargs)
        return (_auto_import_rule(),)

    def get_rule(self, **kwargs: Any) -> TodoAutoImportRuleRecord:
        self._record("get_rule", **kwargs)
        return _auto_import_rule()

    def update_rule(self, **kwargs: Any) -> TodoAutoImportRuleRecord:
        self._record("update_rule", **kwargs)
        status = kwargs["status"] or TodoAutoImportRuleStatus.ENABLED
        return _auto_import_rule(status=TodoAutoImportRuleStatus(status))

    def list_progress(self, **kwargs: Any) -> tuple[TodoAutoImportProgressRecord, ...]:
        self._record("list_progress", **kwargs)
        return (_auto_import_progress(),)

    def record_checkpoint(self, **kwargs: Any) -> TodoAutoImportProgressRecord:
        self._record("record_checkpoint", **kwargs)
        return _auto_import_progress()

    def create_todo_from_rule(self, **kwargs: Any) -> TodoRecord:
        self._record("create_todo_from_rule", **kwargs)
        return _todo(status=TodoStatus.PENDING, collab_context_id=None)


@pytest.mark.asyncio
async def test_list_todos_maps_product_page_without_internal_fields():
    service = _RecordedService()

    payload = await list_todos(
        request=_request("GET", "/openapi/v1/collaboration/tasks/todos"),
        page_params=_PageParams(page=2, page_size=25),
        space_id=42,
        principal={"user_id": "human_1"},
        user_id="human_1",
        keyword="release",
        status=TodoStatus.IN_PROGRESS,
        bot_id="bot_2",
        service=service,
    )

    assert payload.code == 200000
    assert payload.request_id == "trace-todo"
    assert payload.data.total == 1
    item = payload.data.items[0]
    assert item.todo_id == "todo_1"
    assert item.collab_context_id == "context_todo_1"
    assert item.status == TodoStatus.IN_PROGRESS
    assert "created_by" not in item.model_dump()
    assert service.calls == [
        {
            "op": "list_todos",
            "space_id": 42,
            "actor_id": "human_1",
            "keyword": "release",
            "status": TodoStatus.IN_PROGRESS,
            "bot_id": "bot_2",
            "page": 2,
            "page_size": 25,
        }
    ]


@pytest.mark.asyncio
async def test_create_todo_returns_201_and_passes_body_fields():
    service = _RecordedService()
    response = Response()

    payload = await create_todo(
        body=CreateTodoRequest(
            space_id=42,
            title="Prepare release notes",
            description="Summarize the changes",
            bot_id="bot_1",
        ),
        request=_request("POST", "/openapi/v1/collaboration/tasks/todos"),
        response=response,
        principal={"user_id": "human_1"},
        user_id="human_1",
        service=service,
    )

    assert response.status_code == 201
    assert payload.code == 201000
    assert payload.message == "Created"
    assert payload.data.status == TodoStatus.PENDING
    assert payload.data.collab_context_id is None
    assert payload.data.owner_id == "human_1"
    assert payload.data.owner_name == "Human One"
    assert service.calls == [
        {
            "op": "create_todo",
            "space_id": 42,
            "actor_id": "human_1",
            "title": "Prepare release notes",
            "description": "Summarize the changes",
            "bot_id": "bot_1",
        }
    ]


@pytest.mark.asyncio
async def test_create_todo_maps_bot_team_executor_and_owner_to_product_service():
    legacy_service = _RecordedService()
    product_service = _RecordedProductCreationService()
    response = Response()

    payload = await create_todo(
        body=CreateTodoRequest(
            space_id=42,
            title="Prepare release notes",
            description="Summarize the changes",
            executor={
                "type": "bot_team",
                "id": "todo_tpl_1",
                "revision_id": "todo_tplv_1",
            },
            owner={"user_id": "owner_1", "user_name": "Owner One"},
        ),
        request=_request("POST", "/openapi/v1/collaboration/tasks/todos"),
        response=response,
        principal={"user_id": "human_1"},
        user_id="human_1",
        service=legacy_service,
        product_service=product_service,
    )

    assert response.status_code == 201
    assert payload.data.owner_id == "owner_1"
    assert payload.data.owner_name == "Owner One"
    assert payload.data.product_runtime_status == "not_applicable"
    assert legacy_service.calls == []
    assert product_service.calls == [
        {
            "space_id": 42,
            "actor_id": "human_1",
            "title": "Prepare release notes",
            "description": "Summarize the changes",
            "executor": TodoExecutorSelection(
                type=TodoExecutorKind.BOT_TEAM,
                id="todo_tpl_1",
                revision_id="todo_tplv_1",
            ),
            "owner": TodoOwnerInput(user_id="owner_1", user_name="Owner One"),
        }
    ]


def _dima_import_item(item_id: str = "DIM-1") -> dict[str, Any]:
    return {
        "title": f"Dima {item_id}",
        "description": "Import independently",
        "source": {
            "type": "dima",
            "container_id": "P1",
            "item_id": item_id,
        },
    }


@pytest.mark.asyncio
async def test_import_dima_todos_maps_actor_and_per_item_results():
    service = _RecordedDimaImportService()
    response = Response()

    payload = await import_dima_todos(
        body=ImportDimaTodosRequest(
            space_id=42,
            executor={
                "type": "bot_team",
                "id": "todo_tpl_1",
                "revision_id": "todo_tplv_1",
            },
            owner={"user_id": "owner_1", "user_name": "Owner One"},
            items=(_dima_import_item("DIM-1"), _dima_import_item("DIM-2")),
        ),
        request=_request(
            "POST", "/openapi/v1/collaboration/tasks/todos/products/dima-imports"
        ),
        response=response,
        principal={"user_id": "human_1"},
        user_id="human_1",
        service=service,
    )

    assert response.status_code == 201
    assert payload.code == 201000
    assert payload.message == "Created"
    assert [
        (item.status, item.todo_id, item.product_runtime_status)
        for item in payload.data.items
    ] == [
        ("imported", "todo_1", "ready"),
        ("duplicate", "todo_existing", "ready"),
    ]
    assert len(service.calls) == 1
    call = service.calls[0]
    assert call["space_id"] == 42
    assert call["actor_id"] == "human_1"
    assert call["executor"] == TodoExecutorSelection(
        type=TodoExecutorKind.BOT_TEAM,
        id="todo_tpl_1",
        revision_id="todo_tplv_1",
    )
    assert call["owner"] == TodoOwnerInput(
        user_id="owner_1",
        user_name="Owner One",
    )
    assert tuple(item.source.item_id for item in call["items"]) == ("DIM-1", "DIM-2")


def test_import_dima_todos_rejects_batches_over_twenty_at_the_api_boundary():
    with pytest.raises(PydanticValidationError, match="at most 20 items"):
        ImportDimaTodosRequest(
            space_id=42,
            executor={
                "type": "bot_team",
                "id": "todo_tpl_1",
                "revision_id": "todo_tplv_1",
            },
            owner={"user_id": "owner_1", "user_name": "Owner One"},
            items=tuple(_dima_import_item(f"DIM-{index}") for index in range(21)),
        )


@pytest.mark.asyncio
async def test_create_todo_maps_legacy_bot_id_with_owner_to_product_service():
    legacy_service = _RecordedService()
    product_service = _RecordedProductCreationService()

    await create_todo(
        body=CreateTodoRequest(
            space_id=42,
            title="Prepare release notes",
            description="Summarize the changes",
            bot_id="bot_1",
            owner={"user_id": "owner_1", "user_name": "Owner One"},
        ),
        request=_request("POST", "/openapi/v1/collaboration/tasks/todos"),
        response=Response(),
        principal={"user_id": "human_1"},
        user_id="human_1",
        service=legacy_service,
        product_service=product_service,
    )

    assert legacy_service.calls == []
    assert product_service.calls[0]["executor"] == TodoExecutorSelection(
        type=TodoExecutorKind.BOT,
        id="bot_1",
    )
    assert product_service.calls[0]["owner"] == TodoOwnerInput(
        user_id="owner_1",
        user_name="Owner One",
    )


@pytest.mark.parametrize(
    ("request_fields", "message"),
    [
        ({}, "exactly one"),
        (
            {
                "bot_id": "bot_1",
                "executor": {"type": "bot", "id": "bot_1"},
                "owner": {"user_id": "owner_1", "user_name": "Owner One"},
            },
            "exactly one",
        ),
        (
            {"executor": {"type": "bot_team", "id": "team_1", "revision_id": "rev_1"}},
            "owner",
        ),
    ],
)
def test_create_todo_validates_executor_compatibility(
    request_fields: dict[str, object],
    message: str,
):
    with pytest.raises(PydanticValidationError, match=message):
        CreateTodoRequest(
            space_id=42,
            title="Prepare release notes",
            description="Summarize the changes",
            **request_fields,
        )


@pytest.mark.asyncio
async def test_create_todo_maps_dima_source_identity():
    service = _RecordedService()

    await create_todo(
        body=CreateTodoRequest(
            space_id=42,
            title="Prepare release notes",
            description="Summarize the changes",
            bot_id="bot_1",
            source={
                "type": "dima",
                "container_id": "P1",
                "item_id": "work_1",
                "url": "https://dima.example/items/work_1",
                "updated_at": "2026-11-20T12:00:00Z",
                "metadata": {
                    "processor_id": "149844",
                    "processor_name": "张三",
                },
            },
        ),
        request=_request("POST", "/openapi/v1/collaboration/tasks/todos"),
        response=Response(),
        principal={"user_id": "human_1"},
        user_id="human_1",
        service=service,
    )

    assert service.calls[-1]["source"] == TodoSourceInput(
        source_type="dima",
        container_id="P1",
        item_id="work_1",
        url="https://dima.example/items/work_1",
        updated_at="2026-11-20T12:00:00Z",
        metadata={"processor_id": "149844", "processor_name": "张三"},
    )


def test_create_todo_rejects_non_string_dima_source_metadata():
    with pytest.raises(PydanticValidationError, match="keys and values"):
        CreateTodoRequest(
            space_id=42,
            title="Prepare release notes",
            description="Summarize the changes",
            bot_id="bot_1",
            source={
                "type": "dima",
                "container_id": "P1",
                "item_id": "work_1",
                "metadata": {"processor_id": 149844},
            },
        )


def test_duplicate_source_error_exposes_existing_todo_id():
    response = mapped_error_response(
        TodoSourceAlreadyImportedError("todo_existing"),
        _request("POST", "/openapi/v1/collaboration/tasks/todos"),
    )

    assert response is not None
    assert response.status_code == 409
    payload = json.loads(response.body)
    assert payload["code"] == 409000
    assert payload["message"] == "Dima work item already imported"
    assert payload["data"] == {"existing_todo_id": "todo_existing"}


@pytest.mark.asyncio
async def test_auto_import_rule_routes_forward_authenticated_human_fields():
    service = _RecordedAutoImportService()
    principal = {"user_id": "human_1"}
    response = Response()

    created_payload = await create_dima_auto_import_rule(
        body=CreateTodoAutoImportRuleRequest(
            space_id=42,
            source_container_ids=["W1", "P1"],
            filter_text="只导入需要研发处理且尚未完成的事项",
            bot_id="bot_1",
        ),
        request=_request(
            "POST",
            "/openapi/v1/collaboration/tasks/todos/sources/dima/auto-import-rules",
        ),
        response=response,
        principal=principal,
        user_id="human_1",
        service=service,
    )
    listed_payload = await list_dima_auto_import_rules(
        request=_request(
            "GET",
            "/openapi/v1/collaboration/tasks/todos/sources/dima/auto-import-rules",
        ),
        principal=principal,
        user_id="human_1",
        space_id=42,
        status="disabled",
        service=service,
    )
    active_payload = await list_active_dima_auto_import_rules(
        request=_request(
            "GET",
            "/openapi/v1/collaboration/tasks/todos/sources/dima/auto-import-rules/active",
        ),
        space_id=None,
        service=service,
    )

    assert response.status_code == 201
    assert created_payload.code == 201000
    assert created_payload.data.rule_id == "rule_1"
    assert created_payload.data.cursor_updated_at == "2026-11-25T08:00:00+00:00"
    assert created_payload.data.created_at == "2026-11-25T08:00:00+00:00"
    assert listed_payload.data[0].space_id == 42
    assert active_payload.data[0].status == "enabled"
    assert service.calls == [
        {
            "operation": "create_rule",
            "space_id": 42,
            "actor_id": "human_1",
            "source_container_ids": ("W1", "P1"),
            "filter_text": "只导入需要研发处理且尚未完成的事项",
            "bot_id": "bot_1",
        },
        {
            "operation": "list_rules",
            "actor_id": "human_1",
            "space_id": 42,
            "status": "disabled",
        },
        {
            "operation": "list_active_rules",
            "space_id": None,
        },
    ]


@pytest.mark.asyncio
async def test_auto_import_detail_update_progress_and_checkpoint_routes():
    service = _RecordedAutoImportService()
    principal = {"user_id": "human_1"}

    detail = await get_dima_auto_import_rule(
        rule_id="rule_1",
        request=_request(
            "GET",
            "/openapi/v1/collaboration/tasks/todos/sources/dima/auto-import-rules/rule_1",
        ),
        service=service,
    )
    updated = await update_dima_auto_import_rule(
        rule_id="rule_1",
        body=UpdateTodoAutoImportRuleRequest(
            source_container_ids=["P2"],
            filter_text="只导入缺陷",
            bot_id="bot_2",
            status="disabled",
        ),
        request=_request(
            "PATCH",
            "/openapi/v1/collaboration/tasks/todos/sources/dima/auto-import-rules/rule_1",
        ),
        principal=principal,
        user_id="human_1",
        service=service,
    )
    progress = await list_dima_auto_import_progress(
        rule_id="rule_1",
        request=_request(
            "GET",
            "/openapi/v1/collaboration/tasks/todos/sources/dima/auto-import-rules/rule_1/progress",
        ),
        source_container_id="W1",
        source_item_id="item_1",
        source_updated_at=AUTO_IMPORT_TIME,
        limit=25,
        service=service,
    )
    checkpoint = await record_dima_auto_import_checkpoint(
        rule_id="rule_1",
        body=TodoAutoImportCheckpointRequest(
            source_container_id="W1",
            source_item_id="item_1",
            source_updated_at=AUTO_IMPORT_TIME,
            status="imported",
            todo_id="todo_1",
            detail="created",
            cursor_updated_at=AUTO_IMPORT_TIME,
        ),
        request=_request(
            "POST",
            "/openapi/v1/collaboration/tasks/todos/sources/dima/auto-import-rules/rule_1/checkpoints",
        ),
        service=service,
    )
    created_response = Response()
    created = await create_todo_from_dima_auto_import_rule(
        rule_id="rule_1",
        body=CreateTodoFromAutoImportRuleRequest(
            title="Imported Dima task",
            description="Created by the external Agent",
            source={
                "type": "dima",
                "container_id": "W1",
                "item_id": "item_1",
                "updated_at": AUTO_IMPORT_TIME.isoformat(),
                "metadata": {"processor_id": "human_2"},
            },
        ),
        request=_request(
            "POST",
            "/openapi/v1/collaboration/tasks/todos/sources/dima/auto-import-rules/rule_1/todos",
        ),
        response=created_response,
        service=service,
    )

    assert detail.data.rule_id == "rule_1"
    assert updated.data.status == "disabled"
    assert progress.data[0].source_updated_at == "2026-11-25T08:00:00+00:00"
    assert checkpoint.data.todo_id == "todo_1"
    assert created_response.status_code == 201
    assert created.data.todo_id == "todo_1"
    assert service.calls[:3] == [
        {
            "operation": "get_rule",
            "rule_id": "rule_1",
        },
        {
            "operation": "update_rule",
            "rule_id": "rule_1",
            "actor_id": "human_1",
            "source_container_ids": ("P2",),
            "filter_text": "只导入缺陷",
            "bot_id": "bot_2",
            "status": "disabled",
        },
        {
            "operation": "list_progress",
            "rule_id": "rule_1",
            "source_container_id": "W1",
            "source_item_id": "item_1",
            "source_updated_at": AUTO_IMPORT_TIME,
            "limit": 25,
        },
    ]
    recorded_checkpoint = service.calls[3]
    assert recorded_checkpoint["operation"] == "record_checkpoint"
    assert recorded_checkpoint["rule_id"] == "rule_1"
    assert recorded_checkpoint["checkpoint"] == TodoAutoImportCheckpoint(
        source_container_id="W1",
        source_item_id="item_1",
        source_updated_at=AUTO_IMPORT_TIME,
        status=TodoAutoImportProgressStatus.IMPORTED,
        todo_id="todo_1",
        detail="created",
        cursor_updated_at=AUTO_IMPORT_TIME,
    )
    created_call = service.calls[4]
    assert created_call["operation"] == "create_todo_from_rule"
    assert created_call["rule_id"] == "rule_1"
    assert created_call["source"] == TodoSourceInput(
        source_type="dima",
        container_id="W1",
        item_id="item_1",
        url=None,
        updated_at=AUTO_IMPORT_TIME.isoformat(),
        metadata={"processor_id": "human_2"},
    )


def test_auto_import_active_route_precedes_dynamic_rule_route():
    paths = [route.path for route in auto_import_agent_router.routes]

    assert paths.index(
        "/openapi/v1/collaboration/tasks/todos/sources/dima/auto-import-rules/active"
    ) < paths.index(
        "/openapi/v1/collaboration/tasks/todos/sources/dima/auto-import-rules/{rule_id}"
    )


def test_auto_import_cursor_conflict_maps_to_409():
    response = mapped_error_response(
        TodoAutoImportCursorConflictError("cursor cannot move backwards"),
        _request(
            "POST",
            "/openapi/v1/collaboration/tasks/todos/sources/dima/auto-import-rules/rule_1/checkpoints",
        ),
    )

    assert response is not None
    assert response.status_code == 409
    assert json.loads(response.body)["message"] == "Todo auto-import cursor conflict"


@pytest.mark.asyncio
async def test_get_and_run_use_authenticated_actor():
    service = _RecordedService()

    detail = await get_todo(
        todo_id="todo_1",
        request=_request("GET", "/openapi/v1/collaboration/tasks/todos/todo_1"),
        principal={"user_id": "human_1"},
        user_id="human_1",
        service=service,
    )
    started = await run_todo(
        todo_id="todo_1",
        request=_request("POST", "/openapi/v1/collaboration/tasks/todos/todo_1/run"),
        principal={"user_id": "human_1"},
        user_id="human_1",
        service=service,
    )

    assert detail.data.todo_id == "todo_1"
    assert started.data.status == TodoStatus.IN_PROGRESS
    assert service.calls == [
        {"op": "get_todo", "todo_id": "todo_1", "actor_id": "human_1"},
        {"op": "start_todo", "todo_id": "todo_1", "actor_id": "human_1"},
    ]


@pytest.mark.asyncio
async def test_events_and_interactions_project_b_event_records():
    service = _RecordedService()

    events = await list_todo_events(
        todo_id="todo_1",
        request=_request("GET", "/openapi/v1/collaboration/tasks/todos/todo_1/events"),
        principal={"user_id": "human_1"},
        user_id="human_1",
        service=service,
    )
    history = await list_todo_interactions(
        todo_id="todo_1",
        request=_request(
            "GET", "/openapi/v1/collaboration/tasks/todos/todo_1/interactions"
        ),
        principal={"user_id": "human_1"},
        user_id="human_1",
        service=service,
    )

    assert events.data[0].event_id == "event_3"
    assert events.data[0].execution_id == "execution_1"
    assert events.data[0].context_version == 2
    assert events.data[0].status == "pending_acceptance"
    assert events.data[0].artifacts[0].name == "report.docx"
    assert events.data[0].artifacts[0].url == "/files/report.docx"
    assert "payload" not in events.data[0].model_dump()
    assert service.calls == [
        {"op": "list_events", "todo_id": "todo_1", "actor_id": "human_1"},
        {"op": "list_interactions", "todo_id": "todo_1", "actor_id": "human_1"},
    ]
    assert history.code == 200000
    assert history.data[0].event_type == "user_interaction.posted"
    assert history.data[0].payload == {"content": "Please revise section 2"}


@pytest.mark.asyncio
async def test_create_interaction_uses_message_only_and_returns_created_event():
    service = _RecordedService()
    response = Response()

    payload = await create_todo_interaction(
        todo_id="todo_1",
        body=CreateTodoInteractionRequest(message="Please include the rollback plan"),
        request=_request(
            "POST", "/openapi/v1/collaboration/tasks/todos/todo_1/interactions"
        ),
        response=response,
        principal={"user_id": "human_1"},
        user_id="human_1",
        service=service,
    )

    assert response.status_code == 201
    assert payload.code == 201000
    assert payload.data.event_id == "event_4"
    assert payload.data.event_type == "user_interaction.posted"
    assert payload.data.content == "Please include the rollback plan"
    assert payload.data.payload == {"content": "Please include the rollback plan"}
    assert service.calls == [
        {
            "op": "create_interaction",
            "todo_id": "todo_1",
            "actor_id": "human_1",
            "author_name": None,
            "message": "Please include the rollback plan",
            "reply_to_event_id": None,
            "mentions": (),
        }
    ]


@pytest.mark.asyncio
async def test_create_interaction_maps_reply_and_structured_mentions():
    service = _RecordedService()
    response = Response()

    await create_todo_interaction(
        todo_id="todo_1",
        body=CreateTodoInteractionRequest(
            message="Please review this floor",
            reply_to_event_id="event_2",
            mentions=[{"actor_type": "bot", "actor_id": "bot_2"}],
        ),
        request=_request(
            "POST", "/openapi/v1/collaboration/tasks/todos/todo_1/interactions"
        ),
        response=response,
        principal={"user_id": "human_1"},
        user_id="human_1",
        service=service,
    )

    call = service.calls[0]
    assert call["reply_to_event_id"] == "event_2"
    assert len(call["mentions"]) == 1
    assert call["mentions"][0].actor_type.value == "bot"
    assert call["mentions"][0].actor_id == "bot_2"


def test_create_interaction_rejects_non_bot_mention_actor():
    with pytest.raises(PydanticValidationError):
        CreateTodoInteractionRequest(
            message="invalid",
            mentions=[{"actor_type": "human", "actor_id": "human_2"}],
        )


@pytest.mark.asyncio
async def test_acceptance_returns_updated_todo_and_passes_verdict():
    service = _RecordedService()

    payload = await submit_todo_acceptance(
        todo_id="todo_1",
        body=SubmitTodoAcceptanceRequest(
            decision_id="decision_1",
            verdict=AcceptanceVerdict.APPROVED,
            message="Looks good",
        ),
        request=_request(
            "POST", "/openapi/v1/collaboration/tasks/todos/todo_1/acceptance"
        ),
        principal={"user_id": "human_1"},
        user_id="human_1",
        service=service,
    )

    assert payload.code == 200000
    assert payload.data.status == TodoStatus.COMPLETED
    assert service.calls == [
        {
            "op": "submit_acceptance",
            "todo_id": "todo_1",
            "actor_id": "human_1",
            "verdict": AcceptanceVerdict.APPROVED,
            "decision_id": "decision_1",
            "message": "Looks good",
            "targets": (),
        }
    ]


@pytest.mark.asyncio
async def test_rejection_forwards_full_message_and_stable_targets():
    service = _RecordedService()

    payload = await submit_todo_acceptance(
        todo_id="todo_1",
        body=SubmitTodoAcceptanceRequest(
            decision_id="decision_reject_1",
            verdict=AcceptanceVerdict.REJECTED,
            message="Please correct the enterprise price",
            targets=[
                {"kind": "event", "id": "event_2", "floor": 2},
                {"kind": "artifact", "id": "artifact_1", "floor": 3},
            ],
        ),
        request=_request(
            "POST", "/openapi/v1/collaboration/tasks/todos/todo_1/acceptance"
        ),
        principal={"user_id": "human_1"},
        user_id="human_1",
        service=service,
    )

    assert payload.data.status == TodoStatus.IN_PROGRESS
    call = service.calls[0]
    assert call["op"] == "submit_acceptance"
    assert call["decision_id"] == "decision_reject_1"
    assert call["message"] == "Please correct the enterprise price"
    assert [
        (target.kind.value, target.target_id, target.floor)
        for target in call["targets"]
    ] == [
        ("event", "event_2", 2),
        ("artifact", "artifact_1", 3),
    ]


def _persisted_file_artifact() -> TodoArtifactRecord:
    return TodoArtifactRecord(
        artifact_id="todoartifact_1",
        todo_id="todo_1",
        collab_context_id="context_todo_1",
        execution_id="todoexec_1",
        artifact_name="Release report",
        artifact_kind=TodoArtifactKind.FILE,
        content={
            "kind": "file",
            "storage_id": "task-artifacts/todos/todo_1/report.pdf",
            "file_name": "release report.pdf",
            "file_type": "application/pdf",
            "size": 4,
            "is_final_artifact": True,
        },
        content_hash="hash",
        creator="bot_1",
        created_at=NOW,
        updated_at=NOW,
    )


class _ArtifactService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def list_artifacts(self, **kwargs):
        self.calls.append({"op": "list_artifacts", **kwargs})
        return (_persisted_file_artifact(),)

    def read_file(self, **kwargs):
        self.calls.append({"op": "read_file", **kwargs})
        return TodoArtifactDownload(
            record=_persisted_file_artifact(),
            payload=b"%PDF",
        )


@pytest.mark.asyncio
async def test_list_artifacts_returns_public_content_without_storage_id():
    service = _ArtifactService()

    payload = await list_todo_artifacts(
        todo_id="todo_1",
        request=_request(
            "GET", "/openapi/v1/collaboration/tasks/todos/todo_1/artifacts"
        ),
        principal={"user_id": "human_1"},
        user_id="human_1",
        service=service,
    )

    assert payload.code == 200000
    assert payload.request_id == "trace-todo"
    assert payload.data[0].kind.value == TodoArtifactKind.FILE.value
    assert payload.data[0].todo_execution_id == "todoexec_1"
    assert not hasattr(payload.data[0], "execution_id")
    assert payload.data[0].content == {
        "kind": "file",
        "file_name": "release report.pdf",
        "file_type": "application/pdf",
        "size": 4,
        "is_final_artifact": True,
    }
    assert service.calls == [
        {
            "op": "list_artifacts",
            "todo_id": "todo_1",
            "actor_id": "human_1",
        }
    ]


@pytest.mark.asyncio
async def test_read_artifact_content_sets_preview_headers_and_passes_actor():
    service = _ArtifactService()

    response = await read_todo_artifact_content(
        todo_id="todo_1",
        artifact_id="todoartifact_1",
        principal={"user_id": "human_1"},
        user_id="human_1",
        disposition="inline",
        service=service,
    )

    assert response.body == b"%PDF"
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-length"] == "4"
    assert response.headers["content-disposition"] == (
        "inline; filename*=UTF-8''release%20report.pdf"
    )
    assert service.calls == [
        {
            "op": "read_file",
            "todo_id": "todo_1",
            "artifact_id": "todoartifact_1",
            "actor_id": "human_1",
        }
    ]


def _source_request(path: str) -> Request:
    request = Request(
        scope={
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"user_id=human_1",
            "headers": [
                (b"cookie", b"IAM_TOKEN=iam-token"),
                (b"x-source-test", b"present"),
            ],
            "server": ("example.test", 443),
            "client": ("127.0.0.1", 1234),
        }
    )
    request.state.trace_id = "trace-todo-source"
    return request


class _RecordedProgressService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def get_progress(self, **kwargs: Any):
        from agentclaw.community.core.task.todos.progress_models import (
            TodoProgress,
            TodoProgressStageStatus,
            TodoStageProgress,
        )

        self.calls.append({"op": "get_progress", **kwargs})
        return TodoProgress(
            todo_id="todo_1",
            status=TodoStatus.IN_PROGRESS,
            runtime_kind="product_collet",
            runtime_unavailable=False,
            runtime_error_code=None,
            current_stage_code="stage_1",
            rollout_revision=4,
            stopped=False,
            stages=(
                TodoStageProgress(
                    stage_instance_id="stage_1",
                    code="stage_1",
                    name="需求分析",
                    order=1,
                    kind="bot",
                    status=TodoProgressStageStatus.IN_PROGRESS,
                    owner_user_id="human_1",
                    owner_user_name="Human One",
                    executor_subject_id="bot_1",
                    executor_subject_name="Coding Bot",
                    summary="正在分析需求",
                    current_step="需求分析完成",
                ),
            ),
        )


class _RecordedSourceService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def list_projects(self, **kwargs):
        self.calls.append({"op": "list_projects", **kwargs})
        return (TodoSourceProject(id="P1", name="Dima project"),)

    def list_work_items(self, **kwargs):
        self.calls.append({"op": "list_work_items", **kwargs})
        return (
            TodoSourceWorkItem(
                id="2026112000000000001",
                project_id="P1",
                category="req",
                title="Dima requirement",
                status="处理中",
                updated_at="2026-11-20T12:00:00Z",
            ),
        )

    def load_work_item_draft(self, **kwargs):
        self.calls.append({"op": "load_work_item_draft", **kwargs})
        return TodoSourceDraft(
            source_type="dima",
            project_id="P1",
            work_item_id="2026112000000000001",
            category="req",
            title="Dima requirement",
            description="Imported description",
            source_url="https://dima.example/items/1",
            source_updated_at="2026-11-20T12:00:00Z",
            metadata={"processor_id": "149844", "processor_name": "张三"},
        )


@pytest.mark.asyncio
async def test_dima_source_routes_forward_verified_human_request_context():
    service = _RecordedSourceService()
    principal = {"user_id": "human_1"}

    projects = await list_dima_projects(
        request=_source_request(
            "/openapi/v1/collaboration/tasks/todos/sources/dima/projects"
        ),
        principal=principal,
        user_id="human_1",
        service=service,
    )
    items = await list_dima_work_items(
        project_id="P1",
        request=_source_request(
            "/openapi/v1/collaboration/tasks/todos/sources/dima/projects/P1/work-items"
        ),
        principal=principal,
        user_id="human_1",
        query="requirement",
        service=service,
    )
    draft = await get_dima_work_item_draft(
        project_id="P1",
        work_item_id="2026112000000000001",
        request=_source_request(
            "/openapi/v1/collaboration/tasks/todos/sources/dima/projects/P1/"
            "work-items/2026112000000000001"
        ),
        principal=principal,
        user_id="human_1",
        service=service,
    )

    assert projects.data[0].model_dump() == {
        "id": "P1",
        "name": "Dima project",
        "kind": "project",
    }
    assert items.data[0].category == "req"
    assert draft.data.title == "Dima requirement"
    assert draft.data.description == "Imported description"
    assert draft.data.metadata == {
        "processor_id": "149844",
        "processor_name": "张三",
    }
    assert [call["op"] for call in service.calls] == [
        "list_projects",
        "list_work_items",
        "load_work_item_draft",
    ]
    for call in service.calls:
        assert call["actor_id"] == "human_1"
        auth_request = call["auth_request"]
        assert auth_request.cookies["IAM_TOKEN"] == "iam-token"
        assert auth_request.headers["x-source-test"] == "present"
        assert auth_request.verified_actor_user_id == "human_1"
        assert auth_request.base_url == "https://example.test/"
    assert service.calls[1]["project_id"] == "P1"
    assert service.calls[1]["query"] == "requirement"
    assert service.calls[2]["work_item_id"] == "2026112000000000001"


@pytest.mark.asyncio
async def test_todo_progress_route_projects_stable_service_value():
    service = _RecordedProgressService()

    result = await get_todo_progress(
        todo_id="todo_1",
        request=_request("GET", "/openapi/v1/collaboration/tasks/todos/todo_1/progress"),
        principal={"user_id": "human_1"},
        user_id="human_1",
        expanded_stage_code="stage_1",
        message_limit=25,
        service=service,
    )

    assert service.calls == [
        {
            "op": "get_progress",
            "todo_id": "todo_1",
            "actor_id": "human_1",
            "expanded_stage_code": "stage_1",
            "limit": 25,
        }
    ]
    assert result.data.todo_id == "todo_1"
    assert result.data.runtime_kind == "product_collet"
    assert result.data.stages[0].status == "in_progress"
    assert result.data.stages[0].current_step == "需求分析完成"
