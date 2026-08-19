"""OpenAPI replacements for the legacy public Bot catalog endpoints."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import ValidationError

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    Envelope,
    ErrorEnvelope,
    Page,
    error_example,
)
from agentclaw.community.adapters.http.openapi_v1.log_safe import for_log
from agentclaw.community.adapters.http.openapi_v1.principal import (
    UserIdDep,
    refuse_app_only_caller,
)
from agentclaw.community.adapters.http.openapi_v1.responses import error_response, page
from agentclaw.community.api.bot_discover_service import BotDiscoverServiceProtocol
from agentclaw.community.api.bot_public_service import BotPublicServiceProtocol
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger

from .schemas import (
    DiscoveredPublicBot,
    Friendship,
    PublicBot,
    RuntimeState,
)

logger = get_logger()

_REFUSES_APP_ONLY = [Depends(refuse_app_only_caller)]
_FRIENDSHIP_STATUSES = frozenset({"PENDING", "ACCEPTED", "REJECTED", "CANCELLED"})


def _catalog_401_example(code: int) -> dict[str, object]:
    return error_example(401, "Unauthorized", code=code)["content"][
        "application/json"
    ]["example"]


_CATALOG_401_RESPONSES: dict[int, dict[str, object]] = {
    401: {
        "model": ErrorEnvelope,
        "description": "Missing or invalid credentials, or a verified app-only caller.",
        "content": {
            "application/json": {
                "examples": {
                    "missing_or_invalid_credentials": {
                        "summary": "Missing or invalid credentials (401000)",
                        "value": _catalog_401_example(401000),
                    },
                    "verified_app_only_caller": {
                        "summary": "Verified app-only caller (401001)",
                        "value": _catalog_401_example(401001),
                    },
                }
            }
        },
    }
}

router = APIRouter(prefix="/openapi/v1/bots/public", tags=["bot-public"])


def _request_id(request: Request) -> str:
    return getattr(request.state, "trace_id", "") or ""


def _log_result(operation: str, request: Request, user_id: str, *, count: int) -> None:
    logger.info(
        "[openapi_v1.bot_public.%s] caller=%s request_id=%s count=%s",
        operation,
        for_log(user_id),
        _request_id(request),
        count,
    )


def _log_failure(operation: str, request: Request, user_id: str, category: str) -> None:
    logger.warning(
        "[openapi_v1.bot_public.%s] caller=%s request_id=%s failure=%s",
        operation,
        for_log(user_id),
        _request_id(request),
        category,
    )


def _friendship(record: object) -> Friendship | None:
    if not isinstance(record, Mapping):
        return None
    status = record.get("status")
    if status not in _FRIENDSHIP_STATUSES:
        return None
    requires_approval = record.get("require_approval")
    if not isinstance(requires_approval, bool):
        approvals = (record.get("ext") or {}).get("approvals", [])
        requires_approval = any(
            isinstance(approval, Mapping) and approval.get("require_approval") is True
            for approval in approvals
        )
    return Friendship(status=status, requires_approval=requires_approval)


def _public_bot(record: Mapping[str, Any]) -> PublicBot:
    # COSEC: Explicitly project only catalog fields so service records cannot
    # expose bindings, device data, extensions, credentials, or environment data.
    return PublicBot(
        bot_id=str(record.get("bot_id") or ""),
        entity_id=str(record.get("entity_id") or record.get("owner_id") or ""),
        bot_type=record["bot_type"],
        name=str(record.get("bot_name") or ""),
        description=str(record.get("bot_desc") or ""),
        owner_name=record.get("owner_name"),
        engine=str(record.get("active_engine") or ""),
        status=str(record.get("status") or ""),
        friendship=_friendship(record.get("friend_record_approval")),
    )


@router.get(
    "/search",
    response_model=Envelope[Page[PublicBot]],
    response_model_exclude_none=True,
    dependencies=_REFUSES_APP_ONLY,
    responses=_CATALOG_401_RESPONSES,
)
async def search_public_bots(
    request: Request,
    user_id: UserIdDep,
    search: str | None = Query(default=None, description="Bot or owner name keyword."),
    page_number: int = Query(default=1, alias="page", ge=1, description="1-based page number."),
    page_size: int = Query(default=20, ge=1, le=100, description="Items per page."),
    service: BotPublicServiceProtocol = Injected(BotPublicServiceProtocol),
) -> Envelope[Page[PublicBot]]:
    try:
        result = service.search_public_bots_by_keyword(
            user_id=user_id, search=search, page=page_number, page_size=page_size
        )
    except Exception:  # noqa: BLE001 - the public error must remain fixed
        _log_failure("search", request, user_id, "service_failure")
        return error_response(500, "Internal Server Error", request)
    try:
        items = [_public_bot(item) for item in result.get("items", []) if isinstance(item, Mapping)]
    except (KeyError, TypeError, ValidationError):
        _log_failure("search", request, user_id, "invalid_service_result")
        return error_response(500, "Internal Server Error", request)
    _log_result("search", request, user_id, count=len(items))
    return page(int(result.get("total", 0)), items, request)


@router.get(
    "/discover",
    response_model=Envelope[Page[DiscoveredPublicBot]],
    response_model_exclude_none=True,
    dependencies=_REFUSES_APP_ONLY,
    responses=_CATALOG_401_RESPONSES,
)
async def discover_public_bots(
    request: Request,
    user_id: UserIdDep,
    keyword: str = Query(min_length=1, description="Keyword used for discovery."),
    top_k: int = Query(default=10, ge=1, le=20, description="Maximum recommendations."),
    min_score: float = Query(default=0.1, ge=0, le=1, description="Minimum recommendation score."),
    runtime_state: RuntimeState = Query(default="online", description="Runtime state filter."),
    service: BotDiscoverServiceProtocol = Injected(BotDiscoverServiceProtocol),
) -> Envelope[Page[DiscoveredPublicBot]]:
    try:
        result = service.search_by_keyword(
            keyword=keyword,
            user_id=user_id,
            top_k=top_k,
            min_score=min_score,
            filters={"runtime_state": [runtime_state]},
        )
    except Exception:  # noqa: BLE001 - the public error must remain fixed
        _log_failure("discover", request, user_id, "recommender_failure")
        return error_response(502, "Recommendation service unavailable", request)
    context = result.get("context") if isinstance(result, Mapping) else None
    if not isinstance(context, Mapping) or context.get("recommend_response") is None:
        _log_failure("discover", request, user_id, "recommender_unavailable")
        return error_response(502, "Recommendation service unavailable", request)
    try:
        items = []
        for record in result.get("items", []):
            if not isinstance(record, Mapping):
                continue
            recommendation = record.get("recommend")
            if not isinstance(recommendation, Mapping):
                continue
            items.append(
                DiscoveredPublicBot(
                    **_public_bot(record).model_dump(),
                    recommendation={
                        "score": recommendation.get("score", 0),
                        "reasons": recommendation.get("reasons") or [],
                        "short_profile": recommendation.get("short_profile"),
                    },
                )
            )
    except (KeyError, TypeError, ValidationError):
        _log_failure("discover", request, user_id, "invalid_recommender_result")
        return error_response(502, "Recommendation service unavailable", request)
    _log_result("discover", request, user_id, count=len(items))
    return page(int(result.get("total", 0)), items, request)
