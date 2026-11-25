"""Per-endpoint coverage for template Todo stage lifecycle routes."""

from __future__ import annotations

from typing import Any

from injector import singleton

from agentclaw.community.api.task.todo_service import TodoServiceProtocol
from agentclaw.community.api.task.todo_stage_service import TodoStageServiceProtocol
from agentclaw.community.api.task.todo_template_creation_service import (
    TodoTemplateCreationServiceProtocol,
)
from agentclaw.community.core.task.todos.models import TodoStatus
from tests.community.endpoints.todo_endpoint_support import (
    BOT_ID,
    OTHER_USER_ID,
    SPACE_ID,
    STAGE_ID,
    TEMPLATE_ID,
    TODO_ID,
    USER_ID,
    VERSION_ID,
    enable_public_auth,
    principal_headers,
    stage_record,
    todo_record,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

_BASE = "/openapi/v1/collaboration/tasks/todos"
_HEADERS = principal_headers()
_QUERY = {"user_id": USER_ID}
_WRONG_QUERY = {"user_id": OTHER_USER_ID}
_CREATE_BODY = {
    "title": "Template Todo",
    "description": "Execute the standard delivery flow.",
    "template_id": TEMPLATE_ID,
    "template_version_id": VERSION_ID,
    "participant_bindings": {
        "developer_bot": {
            "subject_type": "bot",
            "subject_id": BOT_ID,
            "subject_name": "Todo Template Bot",
        },
        "technical_owner": {
            "subject_type": "human",
            "subject_id": USER_ID,
            "subject_name": "Technical Owner",
        },
    },
}
_COMPLETE_BODY = {
    "summary": "Review completed.",
    "idempotency_key": "complete-human-stage-1",
    "result": {"approved": True},
}
_CANCEL_BODY = {"reason": "No longer needed."}


class _TemplateCreationService:
    def create_todo_from_template(self, **_kwargs: Any):
        return todo_record()


class _TodoTimelineService:
    def list_stages(self, **_kwargs: Any):
        return (stage_record(),)


class _TodoStageService:
    def complete_human_stage(self, **_kwargs: Any):
        return todo_record(status=TodoStatus.COMPLETED)

    def resume(self, **_kwargs: Any):
        return todo_record(status=TodoStatus.IN_PROGRESS)

    def cancel(self, **_kwargs: Any):
        return todo_record(status=TodoStatus.CANCELLED)


def _bind_lifecycle_world(world: Any) -> None:
    enable_public_auth(world)
    binder = world.injector.binder
    binder.bind(
        TodoTemplateCreationServiceProtocol,
        to=_TemplateCreationService(),
        scope=singleton,
    )
    binder.bind(
        TodoServiceProtocol,
        to=_TodoTimelineService(),
        scope=singleton,
    )
    binder.bind(
        TodoStageServiceProtocol,
        to=_TodoStageService(),
        scope=singleton,
    )


@endpoint_test(
    method="POST",
    path=f"{_BASE}/from-template",
    scenario="create_from_published_template",
    input=CaseInput(
        query_params={**_QUERY, "space_id": SPACE_ID},
        headers=_HEADERS,
        json_body=_CREATE_BODY,
    ),
    seed=_bind_lifecycle_world,
    expect=ExpectSuccess(
        status=201,
        json_contains={
            "code": 201000,
            "data": {
                "todo_id": TODO_ID,
                "status": "not_started",
                "template_id": TEMPLATE_ID,
                "template_version_id": VERSION_ID,
            },
        },
    ),
)
def create_todo_from_template_ok():
    """A published template expands into a not-started Todo."""


@endpoint_test(
    method="POST",
    path=f"{_BASE}/from-template",
    scenario="wrong_user",
    input=CaseInput(
        query_params={**_WRONG_QUERY, "space_id": SPACE_ID},
        headers=_HEADERS,
        json_body=_CREATE_BODY,
    ),
    seed=_bind_lifecycle_world,
    expect=ExpectError(status=403),
)
def create_todo_from_template_forbidden():
    """Template Todo creation rejects an impersonated user scope."""


@endpoint_test(
    method="GET",
    path=f"{_BASE}/{{todo_id}}/stages",
    scenario="list_expanded_stages",
    input=CaseInput(
        path_params={"todo_id": TODO_ID},
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_bind_lifecycle_world,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": [
                {
                    "stage_instance_id": STAGE_ID,
                    "stage_kind": "human",
                    "status": "in_progress",
                }
            ],
        },
    ),
)
def list_todo_stages_ok():
    """The workbench reads the immutable expanded stage timeline."""


@endpoint_test(
    method="GET",
    path=f"{_BASE}/{{todo_id}}/stages",
    scenario="wrong_user",
    input=CaseInput(
        path_params={"todo_id": TODO_ID},
        query_params=_WRONG_QUERY,
        headers=_HEADERS,
    ),
    seed=_bind_lifecycle_world,
    expect=ExpectError(status=403),
)
def list_todo_stages_forbidden():
    """Stage timeline reads reject an impersonated user scope."""


@endpoint_test(
    method="POST",
    path=f"{_BASE}/{{todo_id}}/stages/{{stage_instance_id}}/complete",
    scenario="complete_human_stage",
    input=CaseInput(
        path_params={"todo_id": TODO_ID, "stage_instance_id": STAGE_ID},
        query_params=_QUERY,
        headers=_HEADERS,
        json_body=_COMPLETE_BODY,
    ),
    seed=_bind_lifecycle_world,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"todo_id": TODO_ID, "status": "completed"},
        },
    ),
)
def complete_human_stage_ok():
    """The bound Human completes the current Human stage."""


@endpoint_test(
    method="POST",
    path=f"{_BASE}/{{todo_id}}/stages/{{stage_instance_id}}/complete",
    scenario="wrong_user",
    input=CaseInput(
        path_params={"todo_id": TODO_ID, "stage_instance_id": STAGE_ID},
        query_params=_WRONG_QUERY,
        headers=_HEADERS,
        json_body=_COMPLETE_BODY,
    ),
    seed=_bind_lifecycle_world,
    expect=ExpectError(status=403),
)
def complete_human_stage_forbidden():
    """Human-stage completion rejects an impersonated user scope."""


@endpoint_test(
    method="POST",
    path=f"{_BASE}/{{todo_id}}/resume",
    scenario="resume_interrupted_todo",
    input=CaseInput(
        path_params={"todo_id": TODO_ID},
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_bind_lifecycle_world,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"todo_id": TODO_ID, "status": "in_progress"},
        },
    ),
)
def resume_todo_ok():
    """An interrupted Todo resumes into in-progress execution."""


@endpoint_test(
    method="POST",
    path=f"{_BASE}/{{todo_id}}/resume",
    scenario="wrong_user",
    input=CaseInput(
        path_params={"todo_id": TODO_ID},
        query_params=_WRONG_QUERY,
        headers=_HEADERS,
    ),
    seed=_bind_lifecycle_world,
    expect=ExpectError(status=403),
)
def resume_todo_forbidden():
    """Todo resume rejects an impersonated user scope."""


@endpoint_test(
    method="POST",
    path=f"{_BASE}/{{todo_id}}/cancel",
    scenario="cancel_active_todo",
    input=CaseInput(
        path_params={"todo_id": TODO_ID},
        query_params=_QUERY,
        headers=_HEADERS,
        json_body=_CANCEL_BODY,
    ),
    seed=_bind_lifecycle_world,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"todo_id": TODO_ID, "status": "cancelled"},
        },
    ),
)
def cancel_todo_ok():
    """The creator explicitly cancels an active Todo."""


@endpoint_test(
    method="POST",
    path=f"{_BASE}/{{todo_id}}/cancel",
    scenario="wrong_user",
    input=CaseInput(
        path_params={"todo_id": TODO_ID},
        query_params=_WRONG_QUERY,
        headers=_HEADERS,
        json_body=_CANCEL_BODY,
    ),
    seed=_bind_lifecycle_world,
    expect=ExpectError(status=403),
)
def cancel_todo_forbidden():
    """Todo cancellation rejects an impersonated user scope."""
