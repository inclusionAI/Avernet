"""Legacy skills market routes."""

import asyncio
from typing import Optional

from agentclaw.community.adapters.http.dependencies import (
    RequestContext,
    get_request_context,
)
from agentclaw.community.adapters.http.skill_center.schemas import (
    ActivateSkillFailedItem,
    ActivateSkillsRequest,
    ActivateSkillsResponse,
    ActivateSkillsResults,
    ActivateSkillSuccessItem,
    MarketListResponse,
    MarketTreeResponse,
    SearchRequest,
    SearchResponse,
    SkillListResponse,
    SkillMetadataResponse,
    SyncStatusResponse,
)
from agentclaw.community.api.direct_activation_service import (
    DirectActivationServiceProtocol,
)
from agentclaw.community.api.repository_catalog_service import (
    RepositoryCatalogServiceProtocol,
)
from agentclaw.community.api.skill_query_service import SkillQueryServiceProtocol
from agentclaw.community.api.skill_service_factory import SkillServiceFactoryProtocol
from agentclaw.community.core.bot_collaborator.interceptor import (
    CollaboratorPermissionInterceptor,
    with_interceptors,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.skill_center.errors import (
    LocalSkillNotFoundError,
    LocalSkillRuntimeSyncError,
    SkillRuntimeNameConflictError,
    SkillSetControlPlaneConflictError,
)
from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
from agentclaw.community.di import Injected
from fastapi import (
    Depends,
    HTTPException,
    Query,
)

from .skills_common import (
    _get_path_params,
    logger,
    parse_tags,
)


from fastapi import APIRouter
router = APIRouter(prefix="/api/skills", tags=["skills"])

@router.get("/market/local", response_model=MarketListResponse)
async def list_local_market_skills(
    entity_id: str | None = Query(None, description="Entity ID (纯ID，不需要前缀)"),
    entity_type: str | None = Query(
        None, description="Entity type (staff/proj/team, default: staff)"
    ),
    bot_id: str | None = Query(None, description="Bot ID (default: default)"),
    engine_type: str | None = Query(
        None, description="Engine type override; defaults to bot's active_engine"
    ),
    ctx: RequestContext = Depends(get_request_context),
    repository_catalog: RepositoryCatalogServiceProtocol = Injected(
        RepositoryCatalogServiceProtocol
    ),
) -> MarketListResponse:
    """Compatibility alias for the governed shared Repo catalog."""
    # Keep the historical Bot-shaped query wire.  Repo Catalog has always
    # been environment-wide, so the adapter intentionally ignores it.
    _ = (entity_id, entity_type, bot_id, engine_type, ctx)
    items = repository_catalog.list()
    return MarketListResponse(success=True, data=items, count=len(items))


@router.get("/market/tree", response_model=MarketTreeResponse)
async def get_market_tree(
    entity_id: Optional[str] = Query(None, description="Entity ID (纯ID，不需要前缀)"),
    entity_type: Optional[str] = Query(
        None, description="Entity type (staff/proj/team, default: staff)"
    ),
    bot_id: Optional[str] = Query(None, description="Bot ID"),
    engine_type: Optional[str] = Query(
        None, description="Engine type override; defaults to bot's active_engine"
    ),
    ctx: RequestContext = Depends(get_request_context),
    repository_catalog: RepositoryCatalogServiceProtocol = Injected(
        RepositoryCatalogServiceProtocol
    ),
) -> MarketTreeResponse:
    """Get skill market tree structure from git repo."""
    _ = (entity_id, entity_type, bot_id, engine_type, ctx)
    return MarketTreeResponse(success=True, data=repository_catalog.tree())


@router.get("/market/list", response_model=MarketListResponse)
async def list_market_skills(
    path: str = "",
    orderby: str | None = Query(
        None, description="排序方式: 'latest'(最新), 'hotest'(最热)，默认按创建时间倒序"
    ),
    entity_id: Optional[str] = Query(None, description="Entity ID (纯ID，不需要前缀)"),
    entity_type: Optional[str] = Query(
        None, description="Entity type (staff/proj/team, default: staff)"
    ),
    bot_id: Optional[str] = Query(None, description="Bot ID"),
    engine_type: Optional[str] = Query(
        None, description="Engine type override; defaults to bot's active_engine"
    ),
    ctx: RequestContext = Depends(get_request_context),
    repository_catalog: RepositoryCatalogServiceProtocol = Injected(
        RepositoryCatalogServiceProtocol
    ),
) -> MarketListResponse:
    """Get all skills in marketplace (only git:// skills from database).

    排序参数：
    - orderby=latest: 按创建时间倒序
    - orderby=hotest: 按被添加到能力集的次数倒序
    """
    _ = (entity_id, entity_type, bot_id, engine_type, ctx)
    if orderby and orderby not in ("latest", "hotest"):
        raise HTTPException(
            status_code=400, detail="orderby must be 'latest' or 'hotest'"
        )
    canonical_order = "hotest" if orderby == "hotest" else orderby
    items = repository_catalog.list(path=path or None, orderby=canonical_order)
    return MarketListResponse(success=True, data=items, count=len(items))


@router.post("/market/activate-batch", response_model=ActivateSkillsResponse)
async def activate_skills_batch(
    request: ActivateSkillsRequest,
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    entity_id: Optional[str] = Query(None, description="Entity ID (纯ID，不需要前缀)"),
    entity_type: Optional[str] = Query(
        None, description="Entity type (staff/proj/team, default: staff)"
    ),
    bot_id: Optional[str] = Query(None, description="Bot ID"),
    engine_type: Optional[str] = Query(
        None, description="Engine type override; defaults to bot's active_engine"
    ),
    query_service: SkillQueryServiceProtocol = Injected(SkillQueryServiceProtocol),
    direct_activation: DirectActivationServiceProtocol = Injected(
        DirectActivationServiceProtocol
    ),
) -> ActivateSkillsResponse:
    """Compatibility batch adapter over the canonical Direct control plane."""
    effective_owner_id, effective_bot_id, _, _, _, _ = _get_path_params(
        ctx,
        entity_id,
        entity_type,
        bot_id,
        engine_type,
        bot_repo=bot_repo,
    )
    results: dict[str, list[dict[str, str]]] = {"success": [], "failed": []}
    for path in request.skill_paths:
        try:
            skill_id = query_service.resolve_legacy_skill_id(
                skill_reference=path,
                source_path=path,
                bot_id=effective_bot_id,
                owner_id=effective_owner_id,
                user_id=ctx.user_id,
            )
            item = await direct_activation.activate_skill(
                skill_id=skill_id,
                bot_id=effective_bot_id,
                owner_id=effective_owner_id,
                actor_id=ctx.user_id,
            )
            results["success"].append(
                {
                    "id": str(item.get("id") or skill_id),
                    "link_name": str(
                        item.get("link_name") or item.get("name") or skill_id
                    ),
                    "path": str(item.get("git_path") or path),
                }
            )
        except (
            LocalSkillNotFoundError,
            # The R1 refusal (Set-managed capability) and the one-name-per-
            # active invariant now surface from the UoW as the control-plane
            # conflict family; each is one item's failure, never the batch's.
            SkillSetControlPlaneConflictError,
            SkillRuntimeNameConflictError,
        ) as exc:
            results["failed"].append(
                {"path": path, "error": str(exc) or type(exc).__name__}
            )
        except LocalSkillRuntimeSyncError as exc:
            raise HTTPException(
                status_code=502,
                detail="Failed to synchronize activated skills to runtime",
            ) from exc

    return ActivateSkillsResponse(
        success=True,
        data=ActivateSkillsResults(
            success=[ActivateSkillSuccessItem(**item) for item in results["success"]],
            failed=[ActivateSkillFailedItem(**item) for item in results["failed"]],
        ),
        message=f"Activated {len(results['success'])} skills, {len(results['failed'])} failed",
    )


@router.post("/market/search", response_model=SearchResponse)
async def search_market_skills(
    request: SearchRequest,
    entity_id: Optional[str] = Query(None, description="Entity ID (纯ID，不需要前缀)"),
    entity_type: Optional[str] = Query(
        None, description="Entity type (staff/proj/team, default: staff)"
    ),
    bot_id: Optional[str] = Query(None, description="Bot ID"),
    engine_type: Optional[str] = Query(
        None, description="Engine type override; defaults to bot's active_engine"
    ),
    ctx: RequestContext = Depends(get_request_context),
    repository_catalog: RepositoryCatalogServiceProtocol = Injected(
        RepositoryCatalogServiceProtocol
    ),
) -> SearchResponse:
    """Search skills in marketplace."""
    _ = (entity_id, entity_type, bot_id, engine_type, ctx)
    results = repository_catalog.search(keyword=request.query, limit=100)
    return SearchResponse(success=True, data=results, count=len(results))


@router.post("/market/sync", response_model=SyncStatusResponse)
async def sync_market(
    entity_id: str | None = Query(None, description="Entity ID (纯ID，不需要前缀)"),
    entity_type: str | None = Query(
        None, description="Entity type (staff/proj/team, default: staff)"
    ),
    bot_id: str | None = Query(None, description="Bot ID (default: default)"),
    engine_type: str | None = Query(None, description="Engine type (default: moltis)"),
    ctx: RequestContext = Depends(get_request_context),
    repository_catalog: RepositoryCatalogServiceProtocol = Injected(
        RepositoryCatalogServiceProtocol
    ),
) -> SyncStatusResponse:
    """Compatibility adapter for the one governed environment-wide sync."""
    _ = (entity_id, entity_type, bot_id, engine_type, ctx)
    result = await asyncio.to_thread(repository_catalog.sync)
    if result["status"] == "in_progress":
        return SyncStatusResponse(
            success=True, data={"synced": False, "message": "同步进行中，请稍后再试"}
        )
    if result["status"] == "failed":
        sync_result = result.get("result") or {}
        # The pre-Gateway response published the completed DB scan verbatim in
        # ``data.db_sync``.  Keep that diagnostic wire for partial scans even
        # though canonical sync now correctly fails as one operation.
        if isinstance(sync_result.get("database"), dict):
            return SyncStatusResponse(
                success=True,
                data={
                    "synced": False,
                    "last_sync": sync_result.get("last_sync"),
                    "next_sync_in": sync_result.get("next_sync_in", 0),
                    "db_sync": sync_result["database"],
                },
                message=result["message"],
            )
        raise HTTPException(status_code=500, detail=result["message"])
    sync_result = result["result"]
    return SyncStatusResponse(
        success=True,
        data={
            "synced": bool(sync_result.get("synced")),
            "last_sync": sync_result.get("last_sync"),
            "next_sync_in": sync_result.get("next_sync_in", 0),
            "db_sync": sync_result.get("database"),
        },
        message=sync_result.get("message", "Sync completed"),
    )


@router.get("/market/sync-status", response_model=SyncStatusResponse)
async def get_sync_status(
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    path_factory: WorkspacePathFactory = Injected(WorkspacePathFactory),
    entity_id: Optional[str] = Query(None, description="Entity ID (纯ID，不需要前缀)"),
    entity_type: Optional[str] = Query(
        None, description="Entity type (staff/proj/team, default: staff)"
    ),
    bot_id: Optional[str] = Query(None, description="Bot ID"),
    engine_type: Optional[str] = Query(
        None, description="Engine type override; defaults to bot's active_engine"
    ),
    skill_service_factory: SkillServiceFactoryProtocol = Injected(
        SkillServiceFactoryProtocol
    ),
) -> SyncStatusResponse:
    """Get current sync status."""
    logger.info(
        f"[skills.get_sync_status] Request: user_id={ctx.user_id}, bot_id={ctx.bot_id}, entity_id={entity_id}"
    )

    # Get user-specific paths
    # Get effective path parameters
    (
        effective_entity_id,
        effective_bot_id,
        effective_engine,
        runtime_engine,
        effective_entity_type,
        is_desktop,
    ) = _get_path_params(
        ctx, entity_id, entity_type, bot_id, engine_type, bot_repo=bot_repo
    )

    # Get user-specific paths using new directory structure
    skills_dir = path_factory.get_bot_skills_dir(
        effective_entity_id, effective_bot_id, runtime_engine, effective_entity_type
    )
    local_dir = path_factory.get_bot_skills_local_dir(
        effective_entity_id,
        effective_bot_id,
        runtime_engine,
        effective_entity_type,
        is_desktop=is_desktop,
    )
    path_factory.get_bot_engine_dir(
        effective_entity_id, effective_bot_id, runtime_engine, effective_entity_type
    )
    repo_dir = path_factory.get_bot_skills_repo_dir(
        effective_entity_id,
        effective_bot_id,
        runtime_engine,
        effective_entity_type,
        is_desktop=is_desktop,
    )

    # Create per-request service instance with user-specific paths
    service = skill_service_factory.create(
        active_dir=skills_dir,
        repo_dir=repo_dir,
        local_dir=local_dir,
        entity_id=effective_entity_id,
        bot_id=effective_bot_id,
        engine_type=effective_engine,
    )

    status = service.get_sync_status()
    logger.info(f"[skills.get_sync_status] Status: last_sync={status.get('last_sync')}")
    return SyncStatusResponse(success=True, data=status, message="获取同步状态成功")


@router.get("/user/my-skills", response_model=SkillListResponse)
@with_interceptors(
    CollaboratorPermissionInterceptor(
        bot_id="$bot_id",
        owner_id="$user_id",
        persist_audit_log=False,  # 只读操作，不记录日志
    )
)
async def list_user_skills(
    user_id: Optional[str] = Query(None, description="User ID"),
    bot_id: Optional[str] = Query(None, description="Bot ID (default: default)"),
    ctx: RequestContext = Depends(get_request_context),
    skill_service_factory: SkillServiceFactoryProtocol = Injected(
        SkillServiceFactoryProtocol
    ),
    entity_id: Optional[str] = Query(None, description="Stable Bot entity locator"),
    query_service: SkillQueryServiceProtocol = Injected(SkillQueryServiceProtocol),
) -> SkillListResponse:
    """List skills uploaded by user."""
    effective_bot_id = bot_id or ctx.bot_id or "default"
    actor_id = str(ctx.user_id or user_id or "")
    skills = query_service.list_uploaded_skills(
        bot_id=effective_bot_id,
        owner_id=entity_id or user_id or actor_id,
        actor_id=actor_id,
    )
    return SkillListResponse(
        success=True,
        data=[
            SkillMetadataResponse(
                id=str(s.get("id")) if s.get("id") is not None else "",
                name=s.get("name"),
                description=s.get("description"),
                git_path=s.get("git_path"),
                link_name=s.get("link_name"),
                category=s.get("category"),
                tags=parse_tags(s.get("tags")),
                risk_tags=s.get("risk_tags") or [],
                mcp_dependencies=s.get("mcp_dependencies") or [],
                input_schema=s.get("input_schema"),
                output_schema=s.get("output_schema"),
                is_public=s.get("is_public"),
                is_builtin=s.get("is_builtin"),
                user_id=str(s.get("user_id")) if s.get("user_id") is not None else None,
                bot_id=s.get("bolt_id") if s.get("bolt_id") else "default",
                gmt_created=s.get("gmt_created") if s.get("gmt_created") else "",
                gmt_modified=s.get("gmt_modified") if s.get("gmt_modified") else "",
            )
            for s in skills
        ],
        count=len(skills),
    )
