"""Internal user-level Agent Coding work-items HTTP adapter."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.core.aicoding.work_items_models import (
    RUN_STATUS_TOKENS,
    SOURCE_TOKENS,
    parse_optional_token_filter,
    WorkItemsFilterError,
)
from agentclaw.community.api.work_items_service import (
    AICodingWorkItemsServiceProtocol,
)
from agentclaw.community.di import Injected


class WorkItemsRoute(APIRoute):
    """Keep numeric query validation on the internal 400 contract."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def reject_invalid_query_as_400(request):
            try:
                return await handler(request)
            except RequestValidationError as exc:
                raise HTTPException(
                    400, detail={"code": "INVALID_QUERY", "errors": exc.errors()}
                ) from None

        return reject_invalid_query_as_400


router = APIRouter(
    prefix="/api/v1/aicoding",
    tags=["aicoding-work-items"],
    route_class=WorkItemsRoute,
)


@router.get("/work-items", response_model=None)
async def get_work_items(
    source: str | None = Query(None, description="mine,others,collab"),
    since_ms: int | None = Query(None, gt=0, description="epoch milliseconds"),
    run_status: str | None = Query(
        None, description="non_idle or DevFlow status tokens"
    ),
    limit: int | None = Query(None, ge=1, le=200, description="per-bot scan window"),
    user: AuthenticatedUser = Depends(get_current_user),
    service: AICodingWorkItemsServiceProtocol = Injected(
        AICodingWorkItemsServiceProtocol
    ),
) -> dict:
    """Return a recent-work snapshot across all Coding bots reachable by ``user``.

    ``limit`` is a per-bot scan window, not a final-page limit. OCB accepts
    partial results: one failing Bot never fails the whole response.
    """
    try:
        source_filter = parse_optional_token_filter(source, SOURCE_TOKENS, "source")
        status_filter = parse_optional_token_filter(
            run_status, RUN_STATUS_TOKENS, "run_status"
        )
    except WorkItemsFilterError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    data = await service.get_work_items(
        current_user_id=user.staffId,
        source_filter=source_filter,
        since_ms=since_ms,
        run_status_filter=status_filter,
        scan_limit=limit,
    )
    return {"success": True, "data": data.model_dump()}


__all__ = ["router"]
