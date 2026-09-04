"""OpenAPI Bot catalog endpoints."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import ValidationError

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    Envelope,
    ErrorEnvelope,
    Page,
    error_example,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    Principal,
    require_principal,
)
from agentclaw.community.adapters.http.openapi_v1.responses import error_response, page
from agentclaw.community.api.bot_discover_service import BotDiscoverServiceProtocol
from agentclaw.community.api.bot_public_service import BotPublicServiceProtocol
from agentclaw.community.core.bot_public.catalog_metadata import (
    BotCatalogCaller,
    BotCatalogSearchFilters,
    BotCatalogSearchUnavailableError,
)
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger

from .schemas import DiscoveredPublicBot, PublicBot, RuntimeState
from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute

logger = get_logger()
PrincipalDep = Annotated[Principal, Depends(require_principal)]
router = APIRouter(prefix="/openapi/v1/bots/catalog", tags=["bot-catalog"], route_class=PublicAPIRoute)

_CATALOG_VISIBILITIES = frozenset({"public", "protected", "private"})
_CATALOG_STATUSES = frozenset({"online", "hidden"})
_CATALOG_VIEWER_TYPES = frozenset({"human", "bot"})
_CATALOG_FRIENDSHIPS = frozenset({"all", "friends", "non_friends"})


def _request_id(request: Request) -> str:
    return getattr(request.state, "trace_id", "") or ""


def _log_result(operation: str, request: Request, *, count: int) -> None:
    logger.info(
        "[openapi_v1.bot_catalog.%s] request_id=%s count=%s",
        operation,
        _request_id(request),
        count,
    )


def _log_failure(operation: str, request: Request, category: str) -> None:
    logger.warning(
        "[openapi_v1.bot_catalog.%s] request_id=%s failure=%s",
        operation,
        _request_id(request),
        category,
    )


def _normalize_catalog_values(
    values: Sequence[str] | None,
    *,
    allowed: frozenset[str],
) -> tuple[str, ...]:
    """Normalize repeated and comma-separated optional Catalog filters."""
    if values is None:
        return ()
    normalized: list[str] = []
    for raw_value in values:
        for value in raw_value.split(","):
            candidate = value.strip()
            if not candidate or candidate not in allowed:
                raise ValueError("invalid catalog filter value")
            if candidate not in normalized:
                normalized.append(candidate)
    return tuple(normalized)


def _optional_catalog_value(
    value: str | None,
    *,
    allowed: frozenset[str],
) -> str | None:
    if value is None:
        return None
    candidate = value.strip()
    if not candidate or candidate not in allowed:
        raise ValueError("invalid catalog filter value")
    return candidate


def _catalog_search_filters(
    *,
    visibility: Sequence[str] | None,
    user_visibility: Sequence[str] | None,
    status: str | None,
    viewer_actor_type: str | None,
    viewer_actor_id: str | None,
    friendship: str | None,
) -> BotCatalogSearchFilters:
    """Validate frontend filters before sending the fixed BCS query."""
    normalized_viewer_type = _optional_catalog_value(
        viewer_actor_type, allowed=_CATALOG_VIEWER_TYPES
    )
    normalized_viewer_id = viewer_actor_id.strip() if viewer_actor_id else None
    if viewer_actor_id is not None and not normalized_viewer_id:
        raise ValueError("invalid catalog viewer")
    if (normalized_viewer_type is None) != (normalized_viewer_id is None):
        raise ValueError("catalog viewer must be supplied as a pair")

    normalized_friendship = _optional_catalog_value(
        friendship, allowed=_CATALOG_FRIENDSHIPS
    )
    if normalized_friendship in {"friends", "non_friends"} and normalized_viewer_id is None:
        raise ValueError("catalog friendship filter requires viewer")

    return BotCatalogSearchFilters(
        visibility=_normalize_catalog_values(
            visibility, allowed=_CATALOG_VISIBILITIES
        ),
        user_visibility=_normalize_catalog_values(
            user_visibility, allowed=_CATALOG_VISIBILITIES
        ),
        status=_optional_catalog_value(status, allowed=_CATALOG_STATUSES),
        viewer_actor_type=normalized_viewer_type,
        viewer_actor_id=normalized_viewer_id,
        friendship=normalized_friendship,
    )


def _public_bot(record: Mapping[str, Any]) -> PublicBot:
    # COSEC: Explicitly project only catalog fields and approved BCS metadata so
    # service records cannot expose bindings, device data, credentials, or environment data.
    return PublicBot(
        bot_id=str(record.get("bot_id") or ""),
        bot_uuid=record.get("bot_uuid"),
        entity_id=str(record.get("entity_id") or record.get("owner_id") or ""),
        bot_type=record["bot_type"],
        name=str(record.get("bot_name") or ""),
        description=str(record.get("bot_desc") or ""),
        owner_name=record.get("owner_name"),
        is_friend=record.get("is_friend"),
        visibility=record.get("visibility"),
        is_online=record.get("is_online"),
        actor_kind=record.get("actor_kind"),
        friend_ext=record.get("friend_ext"),
        friend_check_in_strategy=record.get("friend_check_in_strategy"),
        user_visibility=record.get("user_visibility"),
        engine=str(record.get("active_engine") or ""),
        status=str(record.get("status") or ""),
    )


@router.get(
    "/search",
    response_model=Envelope[Page[PublicBot]],
    response_model_exclude_none=True,
    responses={
        502: {
            "model": ErrorEnvelope,
            "description": "Catalog service unavailable",
            **error_example(502, "Catalog service unavailable"),
        }
    },
)
async def search_public_bots(
    request: Request,
    principal: PrincipalDep,
    search: str | None = Query(default=None, description="Bot or owner name keyword."),
    page_number: int = Query(
        default=1, alias="page", ge=1, description="1-based page number."
    ),
    page_size: int = Query(default=20, ge=1, le=100, description="Items per page."),
    visibility: list[str] | None = Query(
        default=None,
        description="Optional BCS visibility filter; repeat or comma-separate public, protected, private.",
    ),
    user_visibility: list[str] | None = Query(
        default=None,
        description="Optional BCS user visibility filter; repeat or comma-separate public, protected, private.",
    ),
    status: str | None = Query(
        default=None, description="Optional BCS status filter: online or hidden."
    ),
    viewer_actor_type: str | None = Query(
        default=None, description="Explicit BCS viewer actor type: human or bot."
    ),
    viewer_actor_id: str | None = Query(
        default=None, description="Explicit BCS viewer actor id; requires viewer_actor_type."
    ),
    friendship: str | None = Query(
        default=None,
        description="Optional BCS friendship filter: all, friends, or non_friends.",
    ),
    service: BotPublicServiceProtocol = Injected(BotPublicServiceProtocol),
) -> Envelope[Page[PublicBot]]:
    try:
        filters = _catalog_search_filters(
            visibility=visibility,
            user_visibility=user_visibility,
            status=status,
            viewer_actor_type=viewer_actor_type,
            viewer_actor_id=viewer_actor_id,
            friendship=friendship,
        )
    except ValueError:
        _log_failure("search", request, "invalid_filters")
        return error_response(422, "Invalid Catalog Search filters", request)
    try:
        result = service.search_catalog_public_bots_by_keyword(
            search=search,
            page=page_number,
            page_size=page_size,
            filters=filters,
            caller=BotCatalogCaller(
                tenant_id=principal.tenant,
                user_id=principal.user_id or None,
                app_id=principal.app_id,
            ),
            request_id=_request_id(request),
        )
    except BotCatalogSearchUnavailableError:
        _log_failure("search", request, "bcs_unavailable")
        return error_response(502, "Catalog service unavailable", request)
    except Exception:  # noqa: BLE001 - the public error must remain fixed
        _log_failure("search", request, "service_failure")
        return error_response(500, "Internal Server Error", request)
    try:
        items = [
            _public_bot(item)
            for item in result.get("items", [])
            if isinstance(item, Mapping)
        ]
    except (KeyError, TypeError, ValidationError):
        _log_failure("search", request, "invalid_service_result")
        return error_response(500, "Internal Server Error", request)
    _log_result("search", request, count=len(items))
    return page(int(result.get("total", 0)), items, request)


@router.get(
    "/discover",
    response_model=Envelope[Page[DiscoveredPublicBot]],
    response_model_exclude_none=True,
)
async def discover_public_bots(
    request: Request,
    principal: PrincipalDep,
    keyword: str = Query(min_length=1, description="Keyword used for discovery."),
    top_k: int = Query(default=10, ge=1, le=20, description="Maximum recommendations."),
    min_score: float = Query(
        default=0.1, ge=0, le=1, description="Minimum recommendation score."
    ),
    runtime_state: RuntimeState = Query(
        default="online", description="Runtime state filter."
    ),
    viewer_actor_type: str | None = Query(
        default=None, description="Explicit BCS viewer actor type: human or bot."
    ),
    viewer_actor_id: str | None = Query(
        default=None, description="Explicit BCS viewer actor id; requires viewer_actor_type."
    ),
    service: BotDiscoverServiceProtocol = Injected(BotDiscoverServiceProtocol),
) -> Envelope[Page[DiscoveredPublicBot]]:
    try:
        catalog_filters = _catalog_search_filters(
            visibility=None,
            user_visibility=None,
            status="online" if runtime_state == "online" else None,
            viewer_actor_type=viewer_actor_type,
            viewer_actor_id=viewer_actor_id,
            friendship=None,
        )
    except ValueError:
        _log_failure("discover", request, "invalid_filters")
        return error_response(422, "Invalid Catalog Search filters", request)
    try:
        result = service.search_by_keyword(
            keyword=keyword,
            top_k=top_k,
            min_score=min_score,
            filters={"runtime_state": [runtime_state]},
            catalog_filters=catalog_filters,
            caller=BotCatalogCaller(
                tenant_id=principal.tenant,
                user_id=principal.user_id or None,
                app_id=principal.app_id,
            ),
            request_id=_request_id(request),
        )
    except Exception:  # noqa: BLE001 - the public error must remain fixed
        _log_failure("discover", request, "recommender_failure")
        return error_response(502, "Recommendation service unavailable", request)
    context = result.get("context") if isinstance(result, Mapping) else None
    if not isinstance(context, Mapping) or context.get("recommend_response") is None:
        _log_failure("discover", request, "recommender_unavailable")
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
                        "reasons": recommendation.get("reasons", []),
                        "short_profile": recommendation.get("short_profile"),
                    },
                )
            )
    except (KeyError, TypeError, ValidationError):
        _log_failure("discover", request, "invalid_recommender_result")
        return error_response(502, "Recommendation service unavailable", request)
    _log_result("discover", request, count=len(items))
    return page(int(result.get("total", 0)), items, request)
