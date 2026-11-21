"""Human-only capability discovery for the Todo template editor."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    USER_SCOPED_ERROR_RESPONSES,
    Envelope,
)
from agentclaw.community.adapters.http.openapi_v1.principal import (
    UserIdDep,
    refuse_app_only_caller,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.api.task.todo_template_capability_service import (
    TodoTemplateCapabilityServiceProtocol,
)
from agentclaw.community.di import Injected

from .template_capability_schemas import TodoTemplateCapabilitiesResponse


router = APIRouter(
    prefix="/openapi/v1/collaboration/tasks",
    tags=["todo-templates"],
    route_class=PublicAPIRoute,
    dependencies=[Depends(refuse_app_only_caller)],
)


@router.get(
    "/todo-template-capabilities",
    response_model=Envelope[TodoTemplateCapabilitiesResponse],
    responses=USER_SCOPED_ERROR_RESPONSES,
)
@envelope_errors
async def get_todo_template_capabilities(
    request: Request,
    user_id: UserIdDep,
    service: TodoTemplateCapabilityServiceProtocol = Injected(
        TodoTemplateCapabilityServiceProtocol
    ),
) -> Envelope[TodoTemplateCapabilitiesResponse]:
    """Return the exact V1 template features accepted by the backend."""
    del user_id  # The identity seam validates scope; capabilities are user-invariant.
    capabilities = await run_in_threadpool(service.get_capabilities)
    return envelope(
        TodoTemplateCapabilitiesResponse.from_capabilities(capabilities),
        request,
    )
