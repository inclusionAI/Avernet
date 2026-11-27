"""Internal Todo routes mirror public contracts while using browser login auth."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute, _IncludedRouter
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.openapi_v1.task.todos.auto_import_agent_router import (
    router as public_auto_import_agent_router,
)
from agentclaw.community.adapters.http.openapi_v1.task.todos.bot_team_router import (
    router as public_bot_team_router,
)
from agentclaw.community.adapters.http.openapi_v1.task.todos.router import (
    router as public_todo_router,
)
from agentclaw.community.adapters.http.openapi_v1.task.todos.template_capability_router import (
    router as public_template_capability_router,
)
from agentclaw.community.adapters.http.openapi_v1.task.todos.template_router import (
    router as public_template_router,
)
from agentclaw.community.adapters.http.task.router import router as task_router
from agentclaw.community.adapters.http.task.todos.internal_router import router
from agentclaw.community.api.task.todo_template_capability_service import (
    TodoTemplateCapabilityServiceProtocol,
)
from agentclaw.community.api.task.todo_product_creation_service import (
    TodoProductCreationServiceProtocol,
)
from agentclaw.community.api.task.todo_service import TodoServiceProtocol
from agentclaw.community.core.task.todos.models import TodoRecord, TodoStatus
from agentclaw.community.core.task.todos.product_creation_models import (
    EnsureProductColletResult,
    TodoDimaImportResult,
    TodoDimaImportStatus,
    TodoExecutorKind,
    TodoExecutorSelection,
    TodoOwnerInput,
    TodoProductColletStatus,
)
from agentclaw.community.core.task.todos.services.todo_template_capability_service import (
    TodoTemplateCapabilityService,
)

_PUBLIC_PREFIX = "/openapi/v1/collaboration/tasks"
_INTERNAL_PREFIX = "/api/v1/collaboration/tasks"
_PUBLIC_ROUTERS = (
    public_template_capability_router,
    public_template_router,
    public_bot_team_router,
    public_auto_import_agent_router,
    public_todo_router,
)
_NOW = datetime(2026, 11, 27, 8, 0, tzinfo=timezone.utc)


def _todo() -> TodoRecord:
    return TodoRecord(
        todo_id="todo_1",
        space_id=107,
        title="测试",
        description="111111",
        bot_id="20261103_wcapfdkz",
        bot_name="Coding Bot",
        status=TodoStatus.PENDING,
        collab_context_id=None,
        created_by="149844",
        created_at=_NOW,
        updated_at=_NOW,
        owner_id="149844",
        owner_name="持续",
    )


class _LegacyTodoServiceMustNotCreate:
    def create_todo(self, **_kwargs: Any) -> TodoRecord:
        raise AssertionError("product payload must not use legacy Todo creation")


class _RecordingProductCreationService:
    def __init__(self) -> None:
        self.create_calls: list[dict[str, Any]] = []
        self.import_calls: list[dict[str, Any]] = []

    def create_todo(self, **kwargs: Any) -> TodoRecord:
        self.create_calls.append(kwargs)
        return _todo()

    async def ensure_product_collet(
        self, **_kwargs: Any
    ) -> EnsureProductColletResult:
        return EnsureProductColletResult(
            status=TodoProductColletStatus.NOT_APPLICABLE,
            todo=_todo(),
        )

    async def import_dima_todos(
        self, **kwargs: Any
    ) -> tuple[TodoDimaImportResult, ...]:
        self.import_calls.append(kwargs)
        return (
            TodoDimaImportResult(
                source_work_item_id="2026112400120935582",
                status=TodoDimaImportStatus.IMPORTED,
                todo_id="todo_1",
                product_runtime_status=TodoProductColletStatus.NOT_APPLICABLE,
            ),
        )


def _routes(source) -> list[APIRoute]:
    return [route for route in source.routes if isinstance(route, APIRoute)]


def _ordered_routes(routes, prefix: str = ""):
    for route in routes:
        if isinstance(route, _IncludedRouter):
            nested_prefix = prefix + route.include_context.prefix
            yield from _ordered_routes(route.original_router.routes, nested_prefix)
            yield from _ordered_routes(route.original_router._low_priority_routes, nested_prefix)
        elif isinstance(route, APIRoute):
            yield prefix + route.path, route



def test_internal_routes_have_method_and_path_parity_with_public_todo_routes():
    expected = {
        (
            _INTERNAL_PREFIX + route.path.removeprefix(_PUBLIC_PREFIX),
            frozenset(route.methods or ()),
        )
        for source in _PUBLIC_ROUTERS
        for route in _routes(source)
    }
    actual = {
        (_INTERNAL_PREFIX + route.path, frozenset(route.methods or ()))
        for route in _routes(router)
        if route.path not in {"/spaces/{space_id}/members", "/bots"}
    }

    assert actual == expected
    assert (
        "/api/v1/collaboration/tasks/bot-teams",
        frozenset({"POST"}),
    ) in actual


def test_internal_support_routes_cover_todo_dependencies_outside_task_openapi():
    actual = {
        (_INTERNAL_PREFIX + route.path, frozenset(route.methods or ()))
        for route in _routes(router)
    }

    assert (
        "/api/v1/collaboration/tasks/spaces/{space_id}/members",
        frozenset({"GET"}),
    ) in actual
    assert (
        "/api/v1/collaboration/tasks/bots",
        frozenset({"GET"}),
    ) in actual


def test_internal_todo_routes_are_registered_before_generic_task_id_read():
    ordered = list(_ordered_routes(task_router.routes))
    route_positions = {
        (path, frozenset(route.methods or ())): index
        for index, (path, route) in enumerate(ordered)
    }
    exact_key = (
        "/api/v1/collaboration/tasks/bot-teams",
        frozenset({"GET"}),
    )
    generic_key = (
        "/api/v1/collaboration/tasks/{task_id}",
        frozenset({"GET"}),
    )

    assert exact_key in route_positions
    assert generic_key in route_positions
    assert route_positions[exact_key] < route_positions[generic_key]


def test_internal_capability_route_uses_login_identity_without_public_header():
    app = FastAPI()
    app.include_router(router, prefix=_INTERNAL_PREFIX)
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id="provider-id",
        staffId="149844",
        operatorName="owner",
    )
    injector = Injector()
    injector.binder.bind(
        TodoTemplateCapabilityServiceProtocol,
        to=TodoTemplateCapabilityService(),
    )
    attach_injector(app, injector)

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/collaboration/tasks/todo-template-capabilities",
            params={"user_id": "149844"},
        )

    assert response.status_code == 200
    assert response.json()["data"]["supported_schema_versions"] == ["1.0"]


def _product_app(
    product_service: _RecordingProductCreationService,
) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix=_INTERNAL_PREFIX)
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id="provider-id",
        staffId="149844",
        operatorName="持续",
    )
    injector = Injector()
    injector.binder.bind(
        TodoServiceProtocol,
        to=_LegacyTodoServiceMustNotCreate(),
    )
    injector.binder.bind(
        TodoProductCreationServiceProtocol,
        to=product_service,
    )
    attach_injector(app, injector)
    return app


def test_internal_create_todo_preserves_product_executor_payload():
    product_service = _RecordingProductCreationService()
    app = _product_app(product_service)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/collaboration/tasks/todos",
            params={"user_id": "149844"},
            json={
                "space_id": 107,
                "title": "测试",
                "description": "111111",
                "executor": {"type": "bot", "id": "20261103_wcapfdkz"},
                "owner": {"user_id": "149844", "user_name": "持续"},
            },
        )

    assert response.status_code == 201, response.text
    assert product_service.create_calls == [
        {
            "space_id": 107,
            "actor_id": "149844",
            "title": "测试",
            "description": "111111",
            "executor": TodoExecutorSelection(
                type=TodoExecutorKind.BOT,
                id="20261103_wcapfdkz",
            ),
            "owner": TodoOwnerInput(user_id="149844", user_name="持续"),
        }
    ]


def test_internal_dima_import_preserves_product_executor_payload():
    product_service = _RecordingProductCreationService()
    app = _product_app(product_service)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/collaboration/tasks/todos/products/dima-imports",
            params={"user_id": "149844"},
            json={
                "space_id": 107,
                "executor": {"type": "bot", "id": "20261103_wcapfdkz"},
                "owner": {"user_id": "149844", "user_name": "持续"},
                "items": [
                    {
                        "title": "测试",
                        "description": "111111",
                        "source": {
                            "type": "dima",
                            "container_id": "P1",
                            "item_id": "2026112400120935582",
                        },
                    }
                ],
            },
        )

    assert response.status_code == 201, response.text
    assert len(product_service.import_calls) == 1
    assert product_service.import_calls[0]["executor"] == TodoExecutorSelection(
        type=TodoExecutorKind.BOT,
        id="20261103_wcapfdkz",
    )
