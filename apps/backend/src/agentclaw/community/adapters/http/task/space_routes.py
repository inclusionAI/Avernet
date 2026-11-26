"""Authenticated Space reads used by the internal Task/Todo frontend."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from starlette.concurrency import run_in_threadpool

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope, Page
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope_errors,
    page,
)
from agentclaw.community.adapters.http.task.schemas_space import TaskSpaceItem
from agentclaw.community.api.space_service import SpaceServiceProtocol
from agentclaw.community.core.spaces.models import SpaceListScope, SpaceType
from agentclaw.community.di import Injected


router = APIRouter(tags=["task"])


@router.get("/spaces", response_model=Envelope[Page[TaskSpaceItem]])
@envelope_errors
async def list_task_spaces(
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    keyword: Annotated[
        str | None,
        Query(max_length=128, description="Optional Space-name search text."),
    ] = None,
    space_type: Annotated[
        SpaceType | None,
        Query(description="Optional Space type filter."),
    ] = None,
    page_no: Annotated[int, Query(ge=1, description="One-based page number.")] = 1,
    page_size: Annotated[
        int,
        Query(ge=1, le=100, description="Maximum items returned per page."),
    ] = 20,
    scope: Annotated[
        Literal["accessible"],
        Query(description="Todo selectors only expose Spaces accessible to the actor."),
    ] = "accessible",
    service: SpaceServiceProtocol = Injected(SpaceServiceProtocol),  # noqa: B008
) -> Envelope[Page[TaskSpaceItem]]:
    """List Spaces visible to the authenticated human.

    The actor comes exclusively from the verified login context. Any legacy
    ``user_id`` query parameter is intentionally ignored and cannot select a
    different user's Space memberships.
    """
    total, records = await run_in_threadpool(
        service.list_spaces,
        user_id=user.staffId,
        keyword=keyword,
        space_type=space_type,
        page_no=page_no,
        page_size=page_size,
        scope=SpaceListScope(scope),
    )
    return page(
        total,
        [
            TaskSpaceItem(
                space_id=record.space.id,
                space_name=record.space.name,
                space_type=record.space.space_type.value,
            )
            for record in records
        ],
        request,
    )
