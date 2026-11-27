"""Internal Todo routes mirror public contracts while using browser login auth."""

from __future__ import annotations

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
