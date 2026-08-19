"""Canonical public adapter for the governed aiworkbench Repo catalog."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope, Page, PageParamsDep
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.adapters.http.openapi_v1.responses import envelope, envelope_errors, page as page_envelope
from agentclaw.community.api.skill_market_service import SkillMarketSearchQuery, SkillMarketServiceProtocol
from agentclaw.community.api.skill_service_factory import SkillServiceFactoryProtocol
from agentclaw.community.core.skill_center.constants import LOCK_HELD_ERRORS
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
    service: SkillMarketServiceProtocol = Injected(SkillMarketServiceProtocol),
) -> Envelope[Page[dict[str, Any]]]:
    result = service.search(SkillMarketSearchQuery(keyword=keyword, page_num=1, page_size=10000))
    items = [item for item in result.items if not path or str(item.get("git_path") or "")[6:].startswith(path.rstrip("/"))]
    if sort == "latest":
        items.sort(key=lambda item: str(item.get("gmt_created") or ""), reverse=True)
    start = (page.page - 1) * page.page_size
    return page_envelope(len(items), list(items[start:start + page.page_size]), request)


@router.get("/repository/tree", response_model=Envelope[list[dict[str, Any]]])
@envelope_errors
async def repository_tree(request: Request, _actor_id: UserIdDep, factory: SkillServiceFactoryProtocol = Injected(SkillServiceFactoryProtocol)) -> Envelope[list[dict[str, Any]]]:
    # Preserve the legacy filesystem + market-tree-cache wire representation.
    return envelope(factory.create().get_market_tree(), request)


@router.get("/{skill_id}", response_model=Envelope[dict[str, Any]])
@envelope_errors
async def get_repository_skill(skill_id: str, request: Request, _actor_id: UserIdDep, service: SkillMarketServiceProtocol = Injected(SkillMarketServiceProtocol)) -> Envelope[dict[str, Any]]:
    skill = service.get_repository_skill(skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="Repository skill not found")
    return envelope(skill, request)


@router.post("/repository/sync", response_model=Envelope[dict[str, Any]])
@envelope_errors
async def sync_repository_skills(request: Request, _actor_id: UserIdDep, factory: SkillServiceFactoryProtocol = Injected(SkillServiceFactoryProtocol)) -> Envelope[dict[str, Any]]:
    """Synchronously run the established global master fetch and DB scan once."""
    service = factory.create()
    result = await run_in_threadpool(service.sync_repo_with_lock, min_interval=0)
    if result.get("error") in LOCK_HELD_ERRORS:
        raise HTTPException(status_code=409, detail="SYNC_IN_PROGRESS")
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("message", "Repository sync failed"))
    # GitSyncService owns fetch/extract/DB scan/cache refresh as one operation.
    # Calling sync_skills_from_git here would scan the same master tree twice.
    return envelope({"synced": bool(result.get("synced"))}, request)
