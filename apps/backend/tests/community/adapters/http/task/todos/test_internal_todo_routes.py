"""Contract coverage for the internal Todo route mirrors."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module, singleton

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.task.collaboration_registration import (
    mount_collaboration_routers,
)
from agentclaw.community.adapters.http.task.todos.auto_import_agent_routes import (
    router as auto_import_agent_router,
)
from agentclaw.community.adapters.http.task.todos.human_routes import (
    router as human_router,
)
from agentclaw.community.adapters.http.task.todos.template_capability_routes import (
    router as template_capability_router,
)
from agentclaw.community.adapters.http.task.todos.template_routes import (
    router as template_router,
)
from agentclaw.community.api.task.todo_service import TodoServiceProtocol
from agentclaw.community.core.task.todos.models import TodoPage, TodoRecord, TodoStatus


class _Bindings(Module):
    def __init__(self, service: MagicMock) -> None:
        self._service = service

    def configure(self, binder) -> None:
        binder.bind(TodoServiceProtocol, to=self._service, scope=singleton)


def _todo() -> TodoRecord:
    now = datetime(2026, 11, 26, tzinfo=timezone.utc)
    return TodoRecord(
        todo_id="todo_1",
        space_id=7,
        title="Review release",
        description="Check the migration",
        bot_id="bot_1",
        bot_name="Release Bot",
        status=TodoStatus.PENDING,
        collab_context_id=None,
        created_by="149844",
        created_at=now,
        updated_at=now,
    )


def _client(service: MagicMock) -> TestClient:
    app = FastAPI()
    mount_collaboration_routers(app)
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id="provider-user-1",
        staffId="149844",
        operatorName="owner",
    )
    attach_injector(app, Injector([_Bindings(service)]))
    return TestClient(app)


def _todo_api_operations() -> set[tuple[str, str]]:
    routers = (
        human_router,
        template_router,
        template_capability_router,
        auto_import_agent_router,
    )
    return {
        (method, route.path)
        for router in routers
        for route in router.routes
        for method in route.methods - {"HEAD", "OPTIONS"}
    }


@pytest.mark.unit
def test_internal_todo_routes_are_registered() -> None:
    app = FastAPI()
    mount_collaboration_routers(app)
    operations = _todo_api_operations()

    assert ("GET", "/api/v1/collaboration/tasks/todos") in operations
    assert ("POST", "/api/v1/collaboration/tasks/todo-templates") in operations
    assert (
        "GET",
        "/api/v1/collaboration/tasks/todo-template-capabilities",
    ) in operations
    assert (
        "GET",
        "/api/v1/collaboration/tasks/todos/sources/dima/auto-import-rules/active",
    ) in operations
    assert len(operations) == 37


@pytest.mark.unit
def test_internal_todo_routes_precede_legacy_task_id_route() -> None:
    """Static Todo names must not be consumed by ``/{task_id}`` lookup."""
    from agentclaw.community.adapters.http.app import app
    from tests.community.framework.coverage_gate import _walk_apiroutes

    paths = [
        path
        for path, route in _walk_apiroutes(app.routes)
        if route.methods & {"GET"}
    ]
    todo_index = paths.index("/api/v1/collaboration/tasks/todos")
    task_index = paths.index("/api/v1/collaboration/tasks/{task_id}")

    assert todo_index < task_index


@pytest.mark.unit
def test_internal_list_todos_uses_authenticated_staff_id() -> None:
    service = MagicMock(spec=TodoServiceProtocol)
    service.list_todos.return_value = TodoPage(total=1, items=(_todo(),))

    response = _client(service).get(
        "/api/v1/collaboration/tasks/todos",
        params={
            "user_id": "149844",
            "space_id": 7,
            "page": 1,
            "page_size": 10,
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert payload["total"] == 1
    assert payload["items"][0]["todo_id"] == "todo_1"
    service.list_todos.assert_called_once_with(
        space_id=7,
        actor_id="149844",
        keyword=None,
        status=None,
        bot_id=None,
        page=1,
        page_size=10,
    )


@pytest.mark.unit
def test_internal_list_todos_rejects_named_user_mismatch() -> None:
    service = MagicMock(spec=TodoServiceProtocol)

    response = _client(service).get(
        "/api/v1/collaboration/tasks/todos",
        params={
            "user_id": "someone-else",
            "space_id": 7,
            "page": 1,
            "page_size": 10,
        },
    )

    assert response.status_code == 403, response.text
    service.list_todos.assert_not_called()
