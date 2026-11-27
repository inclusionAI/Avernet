"""Internal authenticated mirrors of the public Todo product routes.

The endpoint functions stay owned by the public Todo adapter so request/response
contracts and service delegation cannot drift. This module only changes the
transport boundary: ``/openapi/v1`` becomes ``/api/v1`` and the public principal
cache is populated from the logged-in internal user before reused handlers run.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.routing import APIRoute

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.openapi_v1.bots.router_listings import (
    router as public_bot_inventory_router,
)
from agentclaw.community.adapters.http.openapi_v1.spaces.router import (
    router as public_space_router,
)
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
from agentclaw.community.core.gateway_principal import (
    GatewayUser,
    UserPrincipal,
    VerifiedCaller,
)

_PUBLIC_PREFIX = "/openapi/v1/collaboration/tasks"
_PUBLIC_CALLER_STATE_ATTR = "avernet_public_caller"


async def _bridge_internal_login_to_principal(
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
) -> None:
    """Make reused public handlers see the authenticated browser user.

    Public Todo handlers scope ``user_id`` through the verified-principal seam.
    Internal requests already have an authenticated user, so cache an equivalent
    user-only caller before the handler dependencies execute. The internal login
    always wins over any caller-supplied public-principal header.
    """
    gateway_user = GatewayUser(
        id=user.staffId,
        username=user.operatorName,
        display_name=user.nickName,
        full_name=user.nickName,
        tenant_id=user.tenantId,
    )
    setattr(
        request.state,
        _PUBLIC_CALLER_STATE_ATTR,
        VerifiedCaller(principals=(UserPrincipal(subject=gateway_user),)),
    )


def _copy_route(target: APIRouter, source: APIRoute, *, path: str) -> None:
    target.add_api_route(
        path,
        source.endpoint,
        response_model=source.response_model,
        status_code=source.status_code,
        tags=source.tags,
        dependencies=source.dependencies,
        summary=source.summary,
        description=source.description,
        response_description=source.response_description,
        responses=source.responses,
        deprecated=source.deprecated,
        methods=source.methods,
        operation_id=source.operation_id,
        response_model_include=source.response_model_include,
        response_model_exclude=source.response_model_exclude,
        response_model_by_alias=source.response_model_by_alias,
        response_model_exclude_unset=source.response_model_exclude_unset,
        response_model_exclude_defaults=source.response_model_exclude_defaults,
        response_model_exclude_none=source.response_model_exclude_none,
        include_in_schema=source.include_in_schema,
        response_class=source.response_class,
        name=source.name,
        callbacks=source.callbacks,
        openapi_extra=source.openapi_extra,
        generate_unique_id_function=source.generate_unique_id_function,
        strict_content_type=source.strict_content_type,
    )


def _add_router(target: APIRouter, source: APIRouter) -> None:
    for route in source.routes:
        if not isinstance(route, APIRoute):
            raise TypeError(f"Unsupported Todo route type: {type(route).__name__}")
        if not route.path.startswith(_PUBLIC_PREFIX):
            raise ValueError(f"Todo route is outside the public task prefix: {route.path}")
        _copy_route(
            target,
            route,
            path=route.path.removeprefix(_PUBLIC_PREFIX),
        )


def _find_route(source: APIRouter, *, path: str, method: str) -> APIRoute:
    for route in source.routes:
        if (
            isinstance(route, APIRoute)
            and route.path == path
            and method in (route.methods or set())
        ):
            return route
    raise LookupError(f"Missing source route: {method} {path}")


router = APIRouter(
    tags=["todos-internal"],
    dependencies=[Depends(_bridge_internal_login_to_principal)],
)

for _public_router in (
    public_template_capability_router,
    public_template_router,
    public_bot_team_router,
    public_auto_import_agent_router,
    public_todo_router,
):
    _add_router(router, _public_router)


# Todo consumes two established product reads outside the public Task namespace.
# Re-export only the narrow surfaces used by the Todo UI so browser traffic stays
# on the authenticated Task Engine boundary instead of depending on OpenAPI.
_copy_route(
    router,
    _find_route(
        public_space_router,
        path="/openapi/v1/bots/spaces/{space_id}/members",
        method="GET",
    ),
    path="/spaces/{space_id}/members",
)
_copy_route(
    router,
    _find_route(
        public_bot_inventory_router,
        path="/openapi/v1/bots/all",
        method="GET",
    ),
    path="/bots",
)
