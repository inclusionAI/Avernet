"""Per-endpoint coverage for the public Todo workbench surface."""

from __future__ import annotations

import time
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

import jwt
from injector import singleton

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.task.todo_artifact_service import (
    TodoArtifactServiceProtocol,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.task.todos import (
    TodoRepositoryProtocol,
)
from agentclaw.community.core.spaces.models import (
    SpaceMemberRecord,
    SpaceRecord,
    SpaceRole,
    SpaceType,
)
from agentclaw.community.core.spaces.protocols import SpaceAccessServiceProtocol
from agentclaw.community.core.task.todos.errors import (
    TodoNotFoundError,
    TodoStateConflictError,
)
from agentclaw.community.core.task.todos.artifact_models import (
    TodoArtifactDownload,
    TodoArtifactKind,
    TodoArtifactRecord,
)
from agentclaw.community.core.task.todos.models import (
    AcceptanceVerdict,
    TodoPage,
    TodoRecord,
    TodoStatus,
)
from agentclaw.community.core.task.todos.collaboration.protocol import (
    TodoCollaborationContextRequest,
    TodoCollaborationEventRequest,
    TodoCollaborationPort,
)
from tests.community.core.task.todos.collaboration.fakes import (
    FakeTodoCollaborationPort,
)
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

_USER_ID = "todo-endpoint-user"
_SPACE_ID = 1
_BOT_ID = "todo-bot"
_NOW = datetime(2026, 11, 10, 8, 0, tzinfo=timezone.utc)
_SIGNING_KEY = "todo-endpoint-signing-key-at-least-32-bytes"


class _Secret:
    secret_user = "test"
    secret_value = _SIGNING_KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _principal_headers() -> dict[str, str]:
    now = int(time.time())
    principal_value = jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 60 * 60,
            "principals": [
                {
                    "type": "user",
                    "tenant": "todo-endpoint-test",
                    "subject": {
                        "id": _USER_ID,
                        "username": "todo-endpoint-user@example.com",
                    },
                }
            ],
        },
        _SIGNING_KEY,
        algorithm="HS256",
    )
    return {PRINCIPAL_HEADER: principal_value}


def _enable_public_auth(_world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


_SPACE = SpaceRecord(
    id=_SPACE_ID,
    space_code="todo-space",
    space_type=SpaceType.TEAM,
    name="Todo Endpoint Space",
    personal_owner_id=None,
    env="",
    created_by=_USER_ID,
    updated_by=_USER_ID,
    gmt_created=_NOW,
    gmt_modified=_NOW,
)

_MEMBER = SpaceMemberRecord(
    id=1,
    space_id=_SPACE_ID,
    user_id=_USER_ID,
    user_name="Todo User",
    role=SpaceRole.MEMBER,
    env="",
    created_by=_USER_ID,
    gmt_created=_NOW,
    gmt_modified=_NOW,
)


class _FixedSpaceAccess:
    def require_space_member(self, **_kwargs):
        return _SPACE, _MEMBER


class _FixedBots:
    def list_live_by_bot_id(self, bot_id: str):
        return [
            {
                "bot_id": bot_id,
                "bot_name": "Todo Endpoint Bot",
                "space_id": _SPACE_ID,
                "owner_id": _USER_ID,
            }
        ]


def _artifact_record(
    *, kind: TodoArtifactKind = TodoArtifactKind.TEXT
) -> TodoArtifactRecord:
    content: dict[str, object]
    if kind is TodoArtifactKind.FILE:
        content = {
            "kind": "file",
            "storage_id": "task-artifacts/todos/todo_1/report.txt",
            "file_name": "report.txt",
            "file_type": "text/plain",
            "size": 6,
            "is_final_artifact": True,
        }
    else:
        content = {
            "kind": "text",
            "text": "done",
            "is_final_artifact": True,
        }
    return TodoArtifactRecord(
        artifact_id="todoartifact_1",
        todo_id="todo_1",
        collab_context_id="context_todo_1",
        execution_id="todoexec_1",
        artifact_name="Result",
        artifact_kind=kind,
        content=content,
        content_hash="hash",
        creator=_BOT_ID,
        created_at=_NOW,
        updated_at=_NOW,
    )


class _FixedTodoArtifacts:
    def report_artifact(self, **_kwargs: Any) -> TodoArtifactRecord:
        return _artifact_record()

    def upload_file(self, **_kwargs: Any) -> TodoArtifactRecord:
        return _artifact_record(kind=TodoArtifactKind.FILE)

    def list_artifacts(self, **_kwargs: Any) -> tuple[TodoArtifactRecord, ...]:
        return (_artifact_record(kind=TodoArtifactKind.FILE),)

    def read_file(self, **_kwargs: Any) -> TodoArtifactDownload:
        return TodoArtifactDownload(
            record=_artifact_record(kind=TodoArtifactKind.FILE),
            payload=b"report",
        )


def _bind_artifact_world(world) -> None:
    _enable_public_auth(world)
    world.injector.binder.bind(
        TodoArtifactServiceProtocol,
        to=_FixedTodoArtifacts(),
        scope=singleton,
    )


def _todo(
    *,
    status: TodoStatus = TodoStatus.PENDING,
    context_id: str | None = None,
    todo_id: str = "todo_1",
) -> TodoRecord:
    return TodoRecord(
        todo_id=todo_id,
        space_id=_SPACE_ID,
        title="Endpoint Todo",
        description="Build the Todo workbench",
        bot_id=_BOT_ID,
        bot_name="Todo Endpoint Bot",
        status=status,
        collab_context_id=context_id,
        created_by=_USER_ID,
        created_at=_NOW,
        updated_at=_NOW,
    )


class _InMemoryTodos:
    def __init__(self, todo: TodoRecord | None = None) -> None:
        self.todo = todo or _todo()

    def create_todo(self, **kwargs: Any) -> TodoRecord:
        self.todo = _todo(
            todo_id="todo_created",
            context_id=None,
            status=TodoStatus.PENDING,
        )
        self.todo = replace(
            self.todo,
            space_id=kwargs["space_id"],
            title=kwargs["title"],
            description=kwargs["description"],
            bot_id=kwargs["bot_id"],
            bot_name=kwargs["bot_name"],
            created_by=kwargs["created_by"],
        )
        return self.todo

    def get_todo(self, **kwargs: Any) -> TodoRecord:
        if self.todo.todo_id != kwargs["todo_id"]:
            raise TodoNotFoundError(f"Todo {kwargs['todo_id']} not found")
        return self.todo

    def list_todos(self, **kwargs: Any) -> TodoPage:
        if self.todo.space_id != kwargs["space_id"]:
            return TodoPage(total=0, items=())
        status = kwargs.get("status")
        if status is not None and self.todo.status is not status:
            return TodoPage(total=0, items=())
        bot_id = kwargs.get("bot_id")
        if bot_id is not None and self.todo.bot_id != bot_id:
            return TodoPage(total=0, items=())
        keyword = kwargs.get("keyword")
        if keyword and keyword.lower() not in (
            f"{self.todo.title} {self.todo.description} {self.todo.bot_name}".lower()
        ):
            return TodoPage(total=0, items=())
        return TodoPage(total=1, items=(self.todo,))

    def list_todos_by_status(self, **kwargs: Any) -> tuple[TodoRecord, ...]:
        if self.todo.status is not kwargs["status"]:
            return ()
        return (self.todo,)[: kwargs["limit"]]

    def bind_context_if_pending(self, **kwargs: Any) -> TodoRecord:
        if self.todo.status is not TodoStatus.PENDING or self.todo.collab_context_id:
            raise TodoStateConflictError("Todo is not available for context binding")
        self.todo = replace(
            self.todo,
            collab_context_id=kwargs["collab_context_id"],
        )
        return self.todo

    def transition_status(self, **kwargs: Any) -> TodoRecord:
        if self.todo.status is not kwargs["expected_status"]:
            raise TodoStateConflictError("Todo status conflict")
        self.todo = replace(self.todo, status=kwargs["new_status"])
        return self.todo

    def record_acceptance(self, **kwargs: Any) -> TodoRecord:
        self.todo = replace(
            self.todo,
            status=(
                TodoStatus.COMPLETED
                if kwargs["verdict"] is AcceptanceVerdict.APPROVED
                else TodoStatus.PENDING_ACCEPTANCE
            ),
        )
        return self.todo


def _bind_todo_world(
    world,
    *,
    status: TodoStatus = TodoStatus.PENDING,
    context_id: str | None = None,
    seed_context: bool = False,
    seed_interaction: str | None = None,
) -> tuple[_InMemoryTodos, FakeTodoCollaborationPort]:
    _enable_public_auth(world)
    repository = _InMemoryTodos(_todo(status=status, context_id=context_id))
    collaboration = FakeTodoCollaborationPort()
    binder = world.injector.binder
    binder.bind(TodoRepositoryProtocol, to=repository, scope=singleton)
    binder.bind(SpaceAccessServiceProtocol, to=_FixedSpaceAccess(), scope=singleton)
    binder.bind(BotRepository, to=_FixedBots(), scope=singleton)
    binder.bind(TodoCollaborationPort, to=collaboration, scope=singleton)

    if seed_context:
        context = collaboration.create_context(
            TodoCollaborationContextRequest(
                space_id=str(_SPACE_ID),
                environment=_SPACE.env,
                principal_id=_USER_ID,
                product_kind="team_todo",
                workflow_kind="team_todo_execution",
                method_kind="Single",
                executor_id=_BOT_ID,
                executor_owner_id=_USER_ID,
                objective="Build the Todo workbench",
                todo_id="todo_1",
            )
        )
        assert context.collab_context_id == "context_todo_1"
        if status in {TodoStatus.IN_PROGRESS, TodoStatus.PENDING_ACCEPTANCE}:
            context = collaboration.start_context_for_test(
                collab_context_id=context.collab_context_id
            )
        if status is TodoStatus.PENDING_ACCEPTANCE:
            context = collaboration.complete_context(context.collab_context_id)
        if seed_interaction:
            collaboration.append_event(
                collab_context_id=context.collab_context_id,
                request=TodoCollaborationEventRequest(
                    expected_version=context.context_version,
                    event_kind="user_interaction.posted",
                    primitive_type="Message",
                    actor_id=_USER_ID,
                    actor_type="human",
                    parent_event=context.current_event_id,
                    visibility="workspace",
                    payload={"content": seed_interaction},
                ),
            )
    return repository, collaboration


QUERY = {
    "user_id": _USER_ID,
}


@endpoint_test(
    method="GET",
    path="/openapi/v1/todos",
    scenario="list_space_todos",
    input=CaseInput(
        query_params={"space_id": _SPACE_ID, "user_id": _USER_ID},
        headers=_principal_headers(),
    ),
    seed=lambda world: _bind_todo_world(world),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"items": [{"todo_id": "todo_1"}]},
        },
    ),
)
def list_todos_ok():
    """The caller receives one Todo in the current Space."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/todos",
    scenario="wrong_user",
    input=CaseInput(
        query_params={"space_id": _SPACE_ID, "user_id": "someone-else"},
        headers=_principal_headers(),
    ),
    seed=_enable_public_auth,
    expect=ExpectError(status=403),
)
def list_todos_forbidden():
    """The explicit user scope must be the verified principal."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/todos",
    scenario="create_pending",
    input=CaseInput(
        query_params={"user_id": _USER_ID},
        headers=_principal_headers(),
        json_body={
            "space_id": _SPACE_ID,
            "title": "Endpoint Todo",
            "description": "Build the Todo workbench",
            "bot_id": _BOT_ID,
        },
    ),
    seed=lambda world: _bind_todo_world(world),
    expect=ExpectSuccess(
        status=201, json_contains={"code": 201000, "data": {"todo_id": "todo_created"}}
    ),
)
def create_todo_ok():
    """A Space member creates a pending Todo for an eligible Bot."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/todos",
    scenario="invalid_title",
    input=CaseInput(
        query_params={"user_id": _USER_ID},
        headers=_principal_headers(),
        json_body={
            "space_id": _SPACE_ID,
            "title": "",
            "description": "Build the Todo workbench",
            "bot_id": _BOT_ID,
        },
    ),
    seed=_enable_public_auth,
    expect=ExpectError(status=422),
)
def create_todo_validation_failed():
    """An empty title is rejected at the public schema boundary."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/todos/{todo_id}",
    scenario="get_pending",
    input=CaseInput(
        path_params={"todo_id": "todo_1"},
        query_params={"user_id": _USER_ID},
        headers=_principal_headers(),
    ),
    seed=lambda world: _bind_todo_world(world),
    expect=ExpectSuccess(
        status=200,
        json_contains={"code": 200000, "data": {"status": "pending"}},
    ),
)
def get_todo_ok():
    """The owner reads a pending Todo."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/todos/{todo_id}",
    scenario="not_found",
    input=CaseInput(
        path_params={"todo_id": "todo_missing"},
        query_params={"user_id": _USER_ID},
        headers=_principal_headers(),
    ),
    seed=lambda world: _bind_todo_world(world),
    expect=ExpectError(status=404),
)
def get_todo_not_found():
    """A missing Todo is not exposed as an empty result."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/todos/{todo_id}/run",
    scenario="dispatch_pending",
    input=CaseInput(
        path_params={"todo_id": "todo_1"},
        query_params={"user_id": _USER_ID},
        headers=_principal_headers(),
    ),
    seed=lambda world: _bind_todo_world(
        world, context_id="context_todo_1", seed_context=True
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"status": "in_progress", "collab_context_id": "context_todo_1"},
        },
    ),
)
def run_todo_ok():
    """Starting attaches the B collaboration Context."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/todos/{todo_id}/run",
    scenario="running_todo",
    input=CaseInput(
        path_params={"todo_id": "todo_1"},
        query_params={"user_id": _USER_ID},
        headers=_principal_headers(),
    ),
    seed=lambda world: _bind_todo_world(
        world,
        status=TodoStatus.IN_PROGRESS,
        context_id="context_todo_1",
        seed_context=True,
    ),
    expect=ExpectError(status=409),
)
def run_todo_conflict():
    """A second Run cannot restart an already running Todo."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/todos/{todo_id}/events",
    scenario="project_b_events",
    input=CaseInput(
        path_params={"todo_id": "todo_1"},
        query_params={"user_id": _USER_ID},
        headers=_principal_headers(),
    ),
    seed=lambda world: _bind_todo_world(
        world,
        status=TodoStatus.IN_PROGRESS,
        context_id="context_todo_1",
        seed_context=True,
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": [
                {"event_type": "task.created"},
                {"event_type": "task.started"},
            ],
        },
    ),
)
def list_todo_events_ok():
    """The complete ordered B event stream is returned through A."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/todos/{todo_id}/events",
    scenario="not_found",
    input=CaseInput(
        path_params={"todo_id": "todo_missing"},
        query_params={"user_id": _USER_ID},
        headers=_principal_headers(),
    ),
    seed=lambda world: _bind_todo_world(world),
    expect=ExpectError(status=404),
)
def list_todo_events_not_found():
    """Event reads require an existing Todo."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/todos/{todo_id}/interactions",
    scenario="projection_ok",
    input=CaseInput(
        path_params={"todo_id": "todo_1"},
        query_params={"user_id": _USER_ID},
        headers=_principal_headers(),
    ),
    seed=lambda world: _bind_todo_world(
        world,
        status=TodoStatus.PENDING_ACCEPTANCE,
        context_id="context_todo_1",
        seed_context=True,
        seed_interaction="Please edit section 2",
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": [{"event_type": "user_interaction.posted"}],
        },
    ),
)
def list_todo_interactions_ok():
    """Interaction reads contain the prior Human event."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/todos/{todo_id}/interactions",
    scenario="not_found",
    input=CaseInput(
        path_params={"todo_id": "todo_missing"},
        query_params=QUERY,
        headers=_principal_headers(),
    ),
    seed=lambda world: _bind_todo_world(world),
    expect=ExpectError(status=404),
)
def list_todo_interactions_not_found():
    """Interaction reads require an existing Todo."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/todos/{todo_id}/interactions",
    scenario="pending_acceptance",
    input=CaseInput(
        path_params={"todo_id": "todo_1"},
        query_params=QUERY,
        headers=_principal_headers(),
        json_body={"message": "Please edit section 2"},
    ),
    seed=lambda world: _bind_todo_world(
        world,
        status=TodoStatus.PENDING_ACCEPTANCE,
        context_id="context_todo_1",
        seed_context=True,
    ),
    expect=ExpectSuccess(
        status=201,
        json_contains={
            "code": 201000,
            "data": {"event_type": "user_interaction.posted"},
        },
    ),
)
def create_todo_interaction_ok():
    """A pending Todo accepts one Human interaction in B."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/todos/{todo_id}/interactions",
    scenario="pending_conflict",
    input=CaseInput(
        path_params={"todo_id": "todo_1"},
        query_params=QUERY,
        headers=_principal_headers(),
        json_body={"message": "Too early"},
    ),
    seed=lambda world: _bind_todo_world(
        world, context_id="context_todo_1", seed_context=True
    ),
    expect=ExpectError(status=409),
)
def create_todo_interaction_conflict():
    """A pending Todo cannot accept post-execution feedback."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/todos/{todo_id}/acceptance",
    scenario="approve_pending",
    input=CaseInput(
        path_params={"todo_id": "todo_1"},
        query_params=QUERY,
        headers=_principal_headers(),
        json_body={"verdict": "approved", "message": "Looks good"},
    ),
    seed=lambda world: _bind_todo_world(
        world,
        status=TodoStatus.PENDING_ACCEPTANCE,
        context_id="context_todo_1",
        seed_context=True,
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"status": "completed"},
        },
    ),
)
def accept_todo_ok():
    """Approving a pending Todo completes the A-owned product state."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/todos/{todo_id}/acceptance",
    scenario="pending_conflict",
    input=CaseInput(
        path_params={"todo_id": "todo_1"},
        query_params=QUERY,
        headers=_principal_headers(),
        json_body={"verdict": "approved"},
    ),
    seed=lambda world: _bind_todo_world(
        world, context_id="context_todo_1", seed_context=True
    ),
    expect=ExpectError(status=409),
)
def accept_todo_conflict():
    """A pending Todo cannot be accepted before B reports completion."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/todos/{todo_id}/artifacts",
    scenario="list_persisted_artifacts",
    input=CaseInput(
        path_params={"todo_id": "todo_1"},
        query_params=QUERY,
        headers=_principal_headers(),
    ),
    seed=_bind_artifact_world,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": [
                {
                    "artifact_id": "todoartifact_1",
                    "todo_execution_id": "todoexec_1",
                    "kind": "file",
                }
            ],
        },
    ),
)
def list_todo_artifacts_ok():
    """The authenticated Human can list persisted Bot artifacts."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/todos/{todo_id}/artifacts",
    scenario="wrong_user",
    input=CaseInput(
        path_params={"todo_id": "todo_1"},
        query_params={"user_id": "someone-else"},
        headers=_principal_headers(),
    ),
    seed=_bind_artifact_world,
    expect=ExpectError(status=403),
)
def list_todo_artifacts_forbidden():
    """Artifact listing remains scoped to the authenticated Human."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/todos/{todo_id}/artifacts/{artifact_id}/content",
    scenario="download_file",
    input=CaseInput(
        path_params={"todo_id": "todo_1", "artifact_id": "todoartifact_1"},
        query_params={**QUERY, "disposition": "inline"},
        headers=_principal_headers(),
    ),
    seed=_bind_artifact_world,
    expect=ExpectSuccess(status=200),
)
def read_todo_artifact_content_ok():
    """The authenticated Human can read file bytes through the Backend."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/todos/{todo_id}/artifacts/{artifact_id}/content",
    scenario="wrong_user",
    input=CaseInput(
        path_params={"todo_id": "todo_1", "artifact_id": "todoartifact_1"},
        query_params={"user_id": "someone-else"},
        headers=_principal_headers(),
    ),
    seed=_bind_artifact_world,
    expect=ExpectError(status=403),
)
def read_todo_artifact_content_forbidden():
    """Artifact bytes are not exposed outside the authenticated Human scope."""


@endpoint_test(
    method="POST",
    path="/api/v1/collaboration/todos/{todo_id}/report",
    scenario="report_text_artifact",
    input=CaseInput(
        path_params={"todo_id": "todo_1"},
        headers={"X-Todo-Execution-Token": "todo-token"},
        json_body={
            "todo_execution_id": "todoexec_1",
            "artifact_name": "Result",
            "kind": "text",
            "content": {"text": "done"},
            "is_final_artifact": True,
        },
    ),
    seed=_bind_artifact_world,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "artifact_id": "todoartifact_1",
                "todo_execution_id": "todoexec_1",
            },
        },
    ),
)
def report_todo_artifact_ok():
    """The active Bot generation reports a non-file artifact."""


@endpoint_test(
    method="POST",
    path="/api/v1/collaboration/todos/{todo_id}/report",
    scenario="conflicting_execution_ids",
    input=CaseInput(
        path_params={"todo_id": "todo_1"},
        headers={"X-Todo-Execution-Token": "todo-token"},
        json_body={
            "todo_execution_id": "todoexec_1",
            "execution_id": "todoexec_old",
            "artifact_name": "Result",
            "kind": "text",
            "content": {"text": "done"},
        },
    ),
    seed=_bind_artifact_world,
    expect=ExpectError(status=422),
)
def report_todo_artifact_rejects_conflicting_ids():
    """The compatibility alias cannot select another execution generation."""


@endpoint_test(
    method="POST",
    path="/api/v1/collaboration/todos/{todo_id}/artifacts/object-file",
    scenario="upload_file_artifact",
    input=CaseInput(
        path_params={"todo_id": "todo_1"},
        headers={"X-Todo-Execution-Token": "todo-token"},
        form_data={
            "todo_execution_id": "todoexec_1",
            "artifact_name": "Result",
            "is_final_artifact": "true",
        },
        files=[("file", ("report.txt", b"report"))],
    ),
    seed=_bind_artifact_world,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "artifact_id": "todoartifact_1",
                "todo_execution_id": "todoexec_1",
                "kind": "file",
            },
        },
    ),
)
def upload_todo_artifact_file_ok():
    """The active Bot generation uploads one object-backed file artifact."""


@endpoint_test(
    method="POST",
    path="/api/v1/collaboration/todos/{todo_id}/artifacts/object-file",
    scenario="missing_execution_id",
    input=CaseInput(
        path_params={"todo_id": "todo_1"},
        headers={"X-Todo-Execution-Token": "todo-token"},
        form_data={"artifact_name": "Result"},
        files=[("file", ("report.txt", b"report"))],
    ),
    seed=_bind_artifact_world,
    expect=ExpectError(status=422),
)
def upload_todo_artifact_file_requires_execution_id():
    """A file upload cannot be accepted without an execution generation."""
