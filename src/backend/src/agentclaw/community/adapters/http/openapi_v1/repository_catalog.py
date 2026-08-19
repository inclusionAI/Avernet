"""Canonical public adapter for the governed aiworkbench Repo catalog."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope, Page, PageParamsDep
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.adapters.http.openapi_v1.responses import envelope, envelope_errors, page as page_envelope
from agentclaw.community.api.repository_catalog_service import RepositoryCatalogServiceProtocol
from agentclaw.community.di import Injected

router = APIRouter(prefix="/openapi/v1/skills", tags=["repository-skills"])


@router.get("/repository", response_model=Envelope[Page[dict[str, Any]]])
@envelope_errors
async def list_repository_skills(
    request: Request,
    _actor_id: UserIdDep,
    page: PageParamsDep,
    keyword: str = Query(default="", max_length=200),
    path: str | None = Query(default=None, max_length=500),
    sort: str = Query(default="latest", pattern="^(latest|hottest)$"),
    service: RepositoryCatalogServiceProtocol = Injected(RepositoryCatalogServiceProtocol),
) -> Envelope[Page[dict[str, Any]]]:
    # Existing `hotest` is the persisted installation-count ordering; do not invent one.
    items = service.list(path=path, orderby="hotest" if sort == "hottest" else "latest")
    if keyword:
        lowered = keyword.lower()
        items = [item for item in items if lowered in " ".join(str(item.get(key) or "") for key in ("name", "description", "category")).lower()]
    start = (page.page - 1) * page.page_size
    return page_envelope(len(items), list(items[start:start + page.page_size]), request)


@router.get("/repository/tree", response_model=Envelope[list[dict[str, Any]]])
@envelope_errors
async def repository_tree(request: Request, _actor_id: UserIdDep, service: RepositoryCatalogServiceProtocol = Injected(RepositoryCatalogServiceProtocol)) -> Envelope[list[dict[str, Any]]]:
    # Preserve the legacy filesystem + market-tree-cache wire representation.
    return envelope(service.tree(), request)


@router.get("/{skill_id}", response_model=Envelope[dict[str, Any]])
@envelope_errors
async def get_repository_skill(skill_id: str, request: Request, _actor_id: UserIdDep, service: RepositoryCatalogServiceProtocol = Injected(RepositoryCatalogServiceProtocol)) -> Envelope[dict[str, Any]]:
    skill = service.detail(skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="Repository skill not found")
    return envelope(skill, request)


@router.post("/repository/sync", response_model=Envelope[dict[str, Any]])
@envelope_errors
async def sync_repository_skills(request: Request, _actor_id: UserIdDep, service: RepositoryCatalogServiceProtocol = Injected(RepositoryCatalogServiceProtocol)) -> Envelope[dict[str, Any]]:
    """Synchronously run the established global master fetch and DB scan once."""
    result = await run_in_threadpool(service.sync)
    if result["status"] == "in_progress":
        raise HTTPException(status_code=409, detail="SYNC_IN_PROGRESS")
    if result["status"] == "failed":
        raise HTTPException(status_code=500, detail=result["message"])
    # GitSyncService owns fetch/extract/DB scan/cache refresh as one operation.
    # Calling sync_skills_from_git here would scan the same master tree twice.
    return envelope({"synced": bool(result["result"].get("synced"))}, request)
