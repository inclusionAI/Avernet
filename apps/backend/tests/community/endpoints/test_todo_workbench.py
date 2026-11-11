"""Per-endpoint coverage for the public Todo workbench surface."""

from __future__ import annotations

import time
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

import jwt
from injector import singleton

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
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
from agentclaw.community.core.task.todos.errors import TodoNotFoundError
from agentclaw.community.core.task.todos.models import (
    AcceptanceVerdict,
    TodoRecord,
    TodoStatus,
)
from agentclaw.community.plugin_api.todo_collaboration import (
    TodoCollaborationContextRequest,
    TodoCollaborationEventRequest,
    TodoCollaborationPlugin,
)
from agentclaw.community.plugins.local.todo_collaboration import (
    LocalTodoCollaborationPlugin,
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


def _todo(
    *,
    status: TodoStatus = TodoStatus.DRAFT,
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
            status=TodoStatus.DRAFT,
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

    def list_todo_candidates(self, **_kwargs: Any) -> tuple[TodoRecord, ...]:
        return (self.todo,)

    def bind_context_if_draft(self, **kwargs: Any) -> TodoRecord:
        self.todo = replace(
            self.todo,
            collab_context_id=kwargs["collab_context_id"],
        )
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
    status: TodoStatus = TodoStatus.DRAFT,
    context_id: str | None = None,
    seed_context: bool = False,
    seed_interaction: str | None = None,
) -> tuple[_InMemoryTodos, LocalTodoCollaborationPlugin]:
    _enable_public_auth(world)
    repository = _InMemoryTodos(_todo(status=status, context_id=context_id))
    collaboration = LocalTodoCollaborationPlugin()
    binder = world.injector.binder
    binder.bind(TodoRepositoryProtocol, to=repository, scope=singleton)
    binder.bind(SpaceAccessServiceProtocol, to=_FixedSpaceAccess(), scope=singleton)
    binder.bind(BotRepository, to=_FixedBots(), scope=singleton)
    binder.bind(TodoCollaborationPlugin, to=collaboration, scope=singleton)

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
    scenario="create_draft",
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
    """A Space member creates a draft Todo for an eligible Bot."""


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
    scenario="get_draft",
    input=CaseInput(
        path_params={"todo_id": "todo_1"},
        query_params={"user_id": _USER_ID},
        headers=_principal_headers(),
    ),
    seed=lambda world: _bind_todo_world(world),
    expect=ExpectSuccess(
        status=200,
        json_contains={"code": 200000, "data": {"status": "draft"}},
    ),
)
def get_todo_ok():
    """The owner reads a draft Todo."""


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
    scenario="start_draft",
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
    scenario="draft_conflict",
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
    """A draft Todo cannot accept post-execution feedback."""


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
    scenario="draft_conflict",
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
    """A draft Todo cannot be accepted before B reports completion."""
