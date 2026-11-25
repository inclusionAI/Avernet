from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import Request, Response
from pydantic import ValidationError

from agentclaw.community.adapters.http.openapi_v1.task.todos.template_router import (
    activate_todo_template,
    archive_todo_template,
    create_todo_template,
    create_todo_template_draft,
    get_todo_template,
    get_todo_template_version,
    list_todo_templates,
    publish_todo_template_version,
    router,
    update_todo_template_draft,
    validate_todo_template_version,
)
from agentclaw.community.adapters.http.openapi_v1.task.todos.template_schemas import (
    CreateTodoTemplateRequest,
    TodoTemplateDefinitionRequest,
    UpdateTodoTemplateDraftRequest,
)
from agentclaw.community.core.task.todos.template_management_models import (
    TodoTemplateDetail,
    TodoTemplatePage,
    TodoTemplateRecord,
    TodoTemplateStatus,
    TodoTemplateVersionRecord,
    TodoTemplateVersionStatus,
)
from agentclaw.community.core.task.todos.template_models import (
    TodoCompletionGate,
    TodoParticipantDefinition,
    TodoParticipantReference,
    TodoParticipantRefSource,
    TodoParticipantRequiredAt,
    TodoParticipantSubjectType,
    TodoStageDefinition,
    TodoStageKind,
    TodoTemplateDefinition,
)

_NOW = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)


def _request(method: str, path: str) -> Request:
    request = Request(scope={"type": "http", "method": method, "path": path})
    request.state.trace_id = "trace-template-management"
    return request


class _PageParams:
    def __init__(self, *, page: int, page_size: int) -> None:
        self.page = page
        self.page_size = page_size


def _definition(name: str = "Standard delivery") -> TodoTemplateDefinition:
    return TodoTemplateDefinition(
        schema_version="1.0",
        name=name,
        description="Implement and verify the Todo.",
        binding_schema=(
            TodoParticipantDefinition(
                key="developer_bot",
                label="Developer Bot",
                subject_type=TodoParticipantSubjectType.BOT,
                required_at=TodoParticipantRequiredAt.TODO_CREATE,
            ),
            TodoParticipantDefinition(
                key="technical_owner",
                label="Technical Owner",
                subject_type=TodoParticipantSubjectType.HUMAN,
                required_at=TodoParticipantRequiredAt.TODO_CREATE,
            ),
        ),
        stages=(
            TodoStageDefinition(
                key="development",
                name="Development",
                purpose="Implement the Todo and create a pull request.",
                stage_kind=TodoStageKind.BOT,
                executor_ref=TodoParticipantReference(
                    source=TodoParticipantRefSource.BINDING,
                    key="developer_bot",
                ),
                owner_ref=TodoParticipantReference(
                    source=TodoParticipantRefSource.BINDING,
                    key="technical_owner",
                ),
                completion_gate=TodoCompletionGate(),
            ),
        ),
    )


def _definition_payload(name: str = "Standard delivery") -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "name": name,
        "description": "Implement and verify the Todo.",
        "binding_schema": [
            {
                "key": "developer_bot",
                "label": "Developer Bot",
                "subject_type": "bot",
                "required_at": "todo_create",
            },
            {
                "key": "technical_owner",
                "label": "Technical Owner",
                "subject_type": "human",
                "required_at": "todo_create",
            },
        ],
        "stages": [
            {
                "key": "development",
                "name": "Development",
                "purpose": "Implement the Todo and create a pull request.",
                "stage_kind": "bot",
                "executor_ref": {"source": "binding", "key": "developer_bot"},
                "owner_ref": {"source": "binding", "key": "technical_owner"},
                "completion_gate": {"required_artifacts": []},
            }
        ],
    }


def _template(
    *, status: TodoTemplateStatus = TodoTemplateStatus.ACTIVE
) -> TodoTemplateRecord:
    return TodoTemplateRecord(
        template_id="todo_tpl_1",
        space_id=7,
        name="Standard delivery",
        description="Implement and verify the Todo.",
        status=status,
        published_version_id=None,
        created_by="admin_1",
        updated_by="admin_1",
        created_at=_NOW,
        updated_at=_NOW,
    )


def _version(
    *, status: TodoTemplateVersionStatus = TodoTemplateVersionStatus.DRAFT
) -> TodoTemplateVersionRecord:
    return TodoTemplateVersionRecord(
        template_version_id="todo_tplv_1",
        template_id="todo_tpl_1",
        version_no=1,
        status=status,
        schema_version="1.0",
        definition=_definition(),
        definition_hash="a" * 64,
        created_by="admin_1",
        published_by="admin_1"
        if status is TodoTemplateVersionStatus.PUBLISHED
        else None,
        published_at=_NOW if status is TodoTemplateVersionStatus.PUBLISHED else None,
        created_at=_NOW,
        updated_at=_NOW,
    )


class _RecordedService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.template = _template()
        self.version = _version()

    def _record(self, operation: str, **kwargs: Any) -> None:
        self.calls.append({"operation": operation, **kwargs})

    def create_template(self, **kwargs):
        self._record("create_template", **kwargs)
        return TodoTemplateDetail(self.template, (self.version,))

    def list_templates(self, **kwargs):
        self._record("list_templates", **kwargs)
        return TodoTemplatePage(total=1, items=(self.template,))

    def get_template(self, **kwargs):
        self._record("get_template", **kwargs)
        return TodoTemplateDetail(self.template, (self.version,))

    def get_version(self, **kwargs):
        self._record("get_version", **kwargs)
        return self.version

    def create_draft(self, **kwargs):
        self._record("create_draft", **kwargs)
        return replace(self.version, version_no=2, template_version_id="todo_tplv_2")

    def update_draft(self, **kwargs):
        self._record("update_draft", **kwargs)
        return replace(self.version, definition=kwargs["definition"])

    def validate_version(self, **kwargs):
        self._record("validate_version", **kwargs)
        return self.version

    def publish_version(self, **kwargs):
        self._record("publish_version", **kwargs)
        return replace(
            self.version,
            status=TodoTemplateVersionStatus.PUBLISHED,
            published_by=kwargs["actor_id"],
            published_at=_NOW,
        )

    def archive_template(self, **kwargs):
        self._record("archive_template", **kwargs)
        return replace(self.template, status=TodoTemplateStatus.ARCHIVED)

    def activate_template(self, **kwargs):
        self._record("activate_template", **kwargs)
        return self.template


@pytest.mark.asyncio
async def test_create_template_returns_201_and_converts_definition() -> None:
    service = _RecordedService()
    response = Response()

    payload = await create_todo_template(
        body=CreateTodoTemplateRequest(space_id=7, definition=_definition_payload()),
        request=_request("POST", "/openapi/v1/collaboration/tasks/todo-templates"),
        response=response,
        user_id="admin_1",
        service=service,
    )

    assert response.status_code == 201
    assert payload.code == 201000
    assert payload.request_id == "trace-template-management"
    assert payload.data.template.template_id == "todo_tpl_1"
    assert payload.data.versions[0].definition.name == "Standard delivery"
    assert service.calls == [
        {
            "operation": "create_template",
            "space_id": 7,
            "actor_id": "admin_1",
            "definition": _definition(),
        }
    ]


@pytest.mark.asyncio
async def test_list_and_get_template_management_reads() -> None:
    service = _RecordedService()

    page = await list_todo_templates(
        request=_request("GET", "/openapi/v1/collaboration/tasks/todo-templates"),
        page_params=_PageParams(page=2, page_size=25),
        space_id=7,
        status=TodoTemplateStatus.ACTIVE,
        user_id="member_1",
        service=service,
    )
    detail = await get_todo_template(
        template_id="todo_tpl_1",
        request=_request(
            "GET", "/openapi/v1/collaboration/tasks/todo-templates/todo_tpl_1"
        ),
        user_id="member_1",
        service=service,
    )
    version = await get_todo_template_version(
        template_id="todo_tpl_1",
        version_id="todo_tplv_1",
        request=_request(
            "GET",
            "/openapi/v1/collaboration/tasks/todo-templates/todo_tpl_1/versions/todo_tplv_1",
        ),
        user_id="member_1",
        service=service,
    )

    assert page.data.total == 1
    assert page.data.items[0].status == TodoTemplateStatus.ACTIVE
    assert detail.data.versions[0].template_version_id == "todo_tplv_1"
    assert version.data.definition.binding_schema[0].subject_type == "bot"
    assert service.calls == [
        {
            "operation": "list_templates",
            "space_id": 7,
            "actor_id": "member_1",
            "status": TodoTemplateStatus.ACTIVE,
            "page": 2,
            "page_size": 25,
        },
        {
            "operation": "get_template",
            "template_id": "todo_tpl_1",
            "actor_id": "member_1",
        },
        {
            "operation": "get_version",
            "template_id": "todo_tpl_1",
            "template_version_id": "todo_tplv_1",
            "actor_id": "member_1",
        },
    ]


@pytest.mark.asyncio
async def test_draft_validate_publish_and_status_commands_delegate_exactly() -> None:
    service = _RecordedService()
    update = UpdateTodoTemplateDraftRequest(
        definition=TodoTemplateDefinitionRequest.model_validate(
            _definition_payload("Updated delivery")
        )
    )

    created = await create_todo_template_draft(
        template_id="todo_tpl_1",
        request=_request(
            "POST", "/openapi/v1/collaboration/tasks/todo-templates/todo_tpl_1/versions"
        ),
        response=Response(),
        user_id="admin_1",
        service=service,
    )
    updated = await update_todo_template_draft(
        template_id="todo_tpl_1",
        version_id="todo_tplv_1",
        body=update,
        request=_request(
            "PUT",
            "/openapi/v1/collaboration/tasks/todo-templates/todo_tpl_1/versions/todo_tplv_1",
        ),
        user_id="admin_1",
        service=service,
    )
    validated = await validate_todo_template_version(
        template_id="todo_tpl_1",
        version_id="todo_tplv_1",
        request=_request(
            "POST",
            "/openapi/v1/collaboration/tasks/todo-templates/todo_tpl_1/versions/todo_tplv_1/validate",
        ),
        user_id="member_1",
        service=service,
    )
    published = await publish_todo_template_version(
        template_id="todo_tpl_1",
        version_id="todo_tplv_1",
        request=_request(
            "POST",
            "/openapi/v1/collaboration/tasks/todo-templates/todo_tpl_1/versions/todo_tplv_1/publish",
        ),
        user_id="admin_1",
        service=service,
    )
    archived = await archive_todo_template(
        template_id="todo_tpl_1",
        request=_request(
            "POST", "/openapi/v1/collaboration/tasks/todo-templates/todo_tpl_1/archive"
        ),
        user_id="admin_1",
        service=service,
    )
    activated = await activate_todo_template(
        template_id="todo_tpl_1",
        request=_request(
            "POST", "/openapi/v1/collaboration/tasks/todo-templates/todo_tpl_1/activate"
        ),
        user_id="admin_1",
        service=service,
    )

    assert created.code == 201000
    assert created.data.version_no == 2
    assert updated.data.definition.name == "Updated delivery"
    assert validated.data.status == TodoTemplateVersionStatus.DRAFT
    assert published.data.status == TodoTemplateVersionStatus.PUBLISHED
    assert archived.data.status == TodoTemplateStatus.ARCHIVED
    assert activated.data.status == TodoTemplateStatus.ACTIVE
    assert service.calls == [
        {
            "operation": "create_draft",
            "template_id": "todo_tpl_1",
            "actor_id": "admin_1",
        },
        {
            "operation": "update_draft",
            "template_id": "todo_tpl_1",
            "template_version_id": "todo_tplv_1",
            "actor_id": "admin_1",
            "definition": _definition("Updated delivery"),
        },
        {
            "operation": "validate_version",
            "template_id": "todo_tpl_1",
            "template_version_id": "todo_tplv_1",
            "actor_id": "member_1",
        },
        {
            "operation": "publish_version",
            "template_id": "todo_tpl_1",
            "template_version_id": "todo_tplv_1",
            "actor_id": "admin_1",
        },
        {
            "operation": "archive_template",
            "template_id": "todo_tpl_1",
            "actor_id": "admin_1",
        },
        {
            "operation": "activate_template",
            "template_id": "todo_tpl_1",
            "actor_id": "admin_1",
        },
    ]


def test_template_request_models_reject_unknown_fields_recursively() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        CreateTodoTemplateRequest.model_validate(
            {"space_id": 7, "definition": _definition_payload(), "unknown": True}
        )

    nested = _definition_payload()
    nested["stages"][0]["executor_ref"]["unknown"] = True
    with pytest.raises(ValidationError, match="extra_forbidden"):
        TodoTemplateDefinitionRequest.model_validate(nested)


def test_template_routes_are_static_todos_siblings_with_complete_operation_set() -> (
    None
):
    operations = {
        (method, route.path)
        for route in router.routes
        for method in (route.methods or set()) - {"HEAD", "OPTIONS"}
    }

    assert operations == {
        ("POST", "/openapi/v1/collaboration/tasks/todo-templates"),
        ("GET", "/openapi/v1/collaboration/tasks/todo-templates"),
        ("GET", "/openapi/v1/collaboration/tasks/todo-templates/{template_id}"),
        (
            "GET",
            "/openapi/v1/collaboration/tasks/todo-templates/{template_id}/versions/{version_id}",
        ),
        (
            "POST",
            "/openapi/v1/collaboration/tasks/todo-templates/{template_id}/versions",
        ),
        (
            "PUT",
            "/openapi/v1/collaboration/tasks/todo-templates/{template_id}/versions/{version_id}",
        ),
        (
            "POST",
            "/openapi/v1/collaboration/tasks/todo-templates/{template_id}/versions/{version_id}/validate",
        ),
        (
            "POST",
            "/openapi/v1/collaboration/tasks/todo-templates/{template_id}/versions/{version_id}/publish",
        ),
        (
            "POST",
            "/openapi/v1/collaboration/tasks/todo-templates/{template_id}/archive",
        ),
        (
            "POST",
            "/openapi/v1/collaboration/tasks/todo-templates/{template_id}/activate",
        ),
    }
    assert all("/todos/" not in path for _method, path in operations)
