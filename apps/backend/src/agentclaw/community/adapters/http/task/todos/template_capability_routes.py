"""Authenticated internal capability discovery for the Todo template editor."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.concurrency import run_in_threadpool

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.openapi_v1.errors import UserIdMismatchError
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    USER_SCOPED_ERROR_RESPONSES,
    Envelope,
)
from agentclaw.community.adapters.http.openapi_v1.principal import USER_ID_DESCRIPTION
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.api.task.todo_template_capability_service import (
    TodoTemplateCapabilityServiceProtocol,
)
from agentclaw.community.di import Injected

from ...openapi_v1.task.todos.template_capability_schemas import (
    TodoTemplateCapabilitiesResponse,
)


router = APIRouter(
    prefix="/api/v1/collaboration/tasks",
    tags=["todo-templates"],
)


AuthenticatedUserDep = Annotated[AuthenticatedUser, Depends(get_current_user)]


@router.get(
    "/todo-template-capabilities",
    response_model=Envelope[TodoTemplateCapabilitiesResponse],
    responses=USER_SCOPED_ERROR_RESPONSES,
)
@envelope_errors
async def get_todo_template_capabilities(
    request: Request,
    user: AuthenticatedUserDep,
    user_id: str = Query(min_length=1, description=USER_ID_DESCRIPTION),
    service: TodoTemplateCapabilityServiceProtocol = Injected(
        TodoTemplateCapabilityServiceProtocol
    ),
) -> Envelope[TodoTemplateCapabilitiesResponse]:
    """Return the exact V1 template features accepted by the backend."""
    if user_id != user.staffId:
        raise UserIdMismatchError(
            f"named user_id does not match authenticated staff {user.staffId}"
        )
    # Capabilities are user-invariant after the explicit user scope check.
    capabilities = await run_in_threadpool(service.get_capabilities)
    return envelope(
        TodoTemplateCapabilitiesResponse.from_capabilities(capabilities),
        request,
    )
