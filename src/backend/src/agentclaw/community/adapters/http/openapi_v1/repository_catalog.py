"""Canonical public adapter for the governed aiworkbench Repo catalog."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Path, Query, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    Envelope,
    Page,
    PageParamsDep,
)
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
    page as page_envelope,
)
from agentclaw.community.api.repository_catalog_service import (
    RepositoryCatalogServiceProtocol,
)
from agentclaw.community.core.skill_center.errors import (
    RepositoryCatalogNotFoundError,
    RepositoryCatalogSyncFailedError,
    RepositoryCatalogSyncInProgressError,
)
from agentclaw.community.di import Injected
from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute

router = APIRouter(prefix="/openapi/v1/bots/skills", tags=["repository-skills"], route_class=PublicAPIRoute)


@router.get("/repository", response_model=Envelope[Page[dict[str, Any]]])
@envelope_errors
async def list_repository_skills(
    request: Request,
    _actor_id: UserIdDep,
    page: PageParamsDep,
    keyword: str = Query(
        default="",
        max_length=200,
        description="Case-insensitive name, description, or category filter.",
    ),
    path: str | None = Query(
        default=None,
        max_length=500,
        description="Optional governed Repo-relative path prefix.",
    ),
    sort: str = Query(
        default="latest",
        pattern="^(latest|hottest)$",
        description="Catalog ordering: latest or persisted hottest.",
    ),
    service: RepositoryCatalogServiceProtocol = Injected(
        RepositoryCatalogServiceProtocol
    ),
) -> Envelope[Page[dict[str, Any]]]:
    # Existing `hotest` is the persisted installation-count ordering; do not invent one.
    total, items = await asyncio.to_thread(
        service.list_page,
        path=path,
        orderby="hotest" if sort == "hottest" else "latest",
        keyword=keyword,
        page=page.page,
        page_size=page.page_size,
    )
    return page_envelope(total, items, request)


@router.get("/repository/tree", response_model=Envelope[list[dict[str, Any]]])
@envelope_errors
async def repository_tree(
    request: Request,
    _actor_id: UserIdDep,
    service: RepositoryCatalogServiceProtocol = Injected(
        RepositoryCatalogServiceProtocol
    ),
) -> Envelope[list[dict[str, Any]]]:
    # Preserve the legacy filesystem + market-tree-cache wire representation.
    return envelope(await asyncio.to_thread(service.tree), request)


@router.get("/repository/{skill_id}", response_model=Envelope[dict[str, Any]])
@envelope_errors
async def get_repository_skill(
    skill_id: Annotated[
        str, Path(description="Decimal public identifier of the shared Repo Skill.")
    ],
    request: Request,
    _actor_id: UserIdDep,
    service: RepositoryCatalogServiceProtocol = Injected(
        RepositoryCatalogServiceProtocol
    ),
) -> Envelope[dict[str, Any]]:
    skill = await asyncio.to_thread(service.detail, skill_id)
    if skill is None:
        raise RepositoryCatalogNotFoundError()
    return envelope(skill, request)


@router.post("/repository/sync", response_model=Envelope[dict[str, Any]])
@envelope_errors
async def sync_repository_skills(
    request: Request,
    _actor_id: UserIdDep,
    service: RepositoryCatalogServiceProtocol = Injected(
        RepositoryCatalogServiceProtocol
    ),
) -> Envelope[dict[str, Any]]:
    """Synchronously run the established global master fetch and DB scan once."""
    result = await asyncio.to_thread(service.sync)
    if result["status"] == "in_progress":
        raise RepositoryCatalogSyncInProgressError()
    if result["status"] == "failed":
        raise RepositoryCatalogSyncFailedError()
    # GitSyncService owns fetch/extract/DB scan/cache refresh as one operation.
    # Calling sync_skills_from_git here would scan the same master tree twice.
    return envelope({"synced": bool(result["result"].get("synced"))}, request)
