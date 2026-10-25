"""Legacy skills activation routes."""

from agentclaw.community.adapters.http.dependencies import (
    RequestContext,
    get_request_context,
)
from agentclaw.community.adapters.http.skill_center.schemas import (
    ActivateSkillSetRequest,
    ActivateSkillSetResponse,
    ActiveSkillSetsResponse,
    ActiveSkillsResponse,
    CurrentSkillSetResponse,
    DeactivateSkillSetRequest,
    DeactivateSkillSetResponse,
    SwitchSkillSetResponse,
    SyncSkillSetRequest,
)
from agentclaw.community.api.runtime_layout_probe_service import (
    RuntimeLayoutProbeServiceProtocol,
)
from agentclaw.community.api.skill_service_factory import SkillServiceFactoryProtocol
from agentclaw.community.api.skill_set_management_service import (
    SkillSetManagementServiceProtocol,
)
from agentclaw.community.core.bot_collaborator.interceptor import (
    CollaboratorPermissionInterceptor,
    with_interceptors,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
from agentclaw.community.di import Injected
from fastapi import (
    Depends,
    Query,
)

from .skills_common import (
    _FILESYSTEM_LAYOUT_ENGINES,
    _desktop_active_root_from_probe,
    _get_path_params,
    _get_skill_set_path_params,
    _legacy_runtime_projection,
    logger,
)


from fastapi import APIRouter
router = APIRouter(prefix="/api/skills", tags=["skills"])

@router.get("/active/list", response_model=ActiveSkillsResponse)
async def get_active_skills(
    entity_id: str | None = Query(None, description="Entity ID (纯ID，不需要前缀)"),
    entity_type: str | None = Query(
        None, description="Entity type (staff/proj/team, default: staff)"
    ),
    bot_id: str | None = Query(None, description="Bot ID (default: default)"),
    engine_type: str | None = Query(
        None, description="Engine type override; defaults to bot's active_engine"
    ),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    path_factory: WorkspacePathFactory = Injected(WorkspacePathFactory),
    skill_service_factory: SkillServiceFactoryProtocol = Injected(
        SkillServiceFactoryProtocol
    ),
    runtime_layout_probe: RuntimeLayoutProbeServiceProtocol = Injected(
        RuntimeLayoutProbeServiceProtocol
    ),
) -> ActiveSkillsResponse:
    """Get list of currently active skills (symlinked in skills directory)."""
    logger.info(
        f"[skills.get_active_skills] Request: user_id={ctx.user_id}, bot_id={ctx.bot_id}, entity_id={entity_id}"
    )

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
    runtime_skills_dir = skills_dir
    if is_desktop and runtime_engine in _FILESYSTEM_LAYOUT_ENGINES:
        probe = await runtime_layout_probe.probe_bot(
            bot_id=effective_bot_id,
            user_id=effective_entity_id,
            engine=runtime_engine,
        )
        # Probe before creating the service so invalid/unbound desktop runtime
        # layouts fail closed and never fall back to the management filesystem.
        runtime_skills_dir = _desktop_active_root_from_probe(
            probe,
            legacy_fallback=skills_dir,
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

    # Create per-request service instance with user-specific paths.  The service
    # keeps the management active_dir for compatibility; desktop reads use the
    # authoritative runtime_skills_dir computed above.
    service = skill_service_factory.create(
        active_dir=skills_dir,
        repo_dir=repo_dir,
        local_dir=local_dir,
        entity_id=effective_entity_id,
        bot_id=effective_bot_id,
        engine_type=effective_engine,
    )

    if is_desktop:
        active_skills = await service.get_active_skills_from_device(
            bot_id=effective_bot_id,
            owner_id=effective_entity_id,
            active_dir=runtime_skills_dir,
        )
    else:
        active_skills = service.get_active_skills()
    logger.info(f"[skills.get_active_skills] Found {len(active_skills)} active skills")
    return ActiveSkillsResponse(
        success=True,
        data=[s.to_dict() for s in active_skills],
        count=len(active_skills),
    )


@router.get(
    "/skillset/current", response_model=CurrentSkillSetResponse, deprecated=True
)
async def get_current_skill_set(
    entity_id: str | None = Query(None, description="Entity ID (纯ID，不需要前缀)"),
    entity_type: str | None = Query(
        None, description="Entity type (staff/proj/team, default: staff)"
    ),
    bot_id: str | None = Query(None, description="Bot ID (default: default)"),
    engine_type: str | None = Query(
        None, description="Engine type override; defaults to bot's active_engine"
    ),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    control_plane: SkillSetManagementServiceProtocol = Injected(
        SkillSetManagementServiceProtocol
    ),
) -> CurrentSkillSetResponse:
    """[DEPRECATED] Get the currently active skill set.

    请使用 GET /skillset/active 获取所有激活的能力集列表。
    此接口将在未来版本中移除。

    Multiple Sets activate independently now; this wire's "the current
    Set" is answered as the first ordinary active Set, else null.
    """
    (
        effective_entity_id,
        effective_bot_id,
        _effective_engine,
        _runtime_engine,
        _effective_entity_type,
        _is_desktop,
    ) = _get_path_params(
        ctx, entity_id, entity_type, bot_id, engine_type, bot_repo=bot_repo
    )

    sets = control_plane.list_sets(
        bot_id=effective_bot_id,
        owner_id=effective_entity_id,
        user_id=ctx.user_id or effective_entity_id,
    )
    current = next(
        (item for item in sets if item.get("is_active") and not item.get("is_default")),
        None,
    )
    if current is not None:
        # The historical payload named the id ``skill_set_id``; keep the
        # alias so deprecated-wire readers survive the re-sourcing.
        current = {**current, "skill_set_id": current.get("id")}
    return CurrentSkillSetResponse(success=True, data=current)


@router.post("/skillset/sync", response_model=SwitchSkillSetResponse)
async def sync_skill_set(
    request: SyncSkillSetRequest,
    entity_id: str | None = Query(None, description="Entity ID (纯ID，不需要前缀)"),
    entity_type: str | None = Query(
        None, description="Entity type (staff/proj/team, default: staff)"
    ),
    bot_id: str | None = Query(None, description="Bot ID (default: default)"),
    engine_type: str | None = Query(
        None, description="Engine type override; defaults to bot's active_engine"
    ),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    control_plane: SkillSetManagementServiceProtocol = Injected(
        SkillSetManagementServiceProtocol
    ),
) -> SwitchSkillSetResponse:
    """Sync a skill set to active skills without deactivating others."""
    # Get effective path parameters
    (
        effective_entity_id,
        effective_bot_id,
        effective_engine,
        runtime_engine,
        effective_entity_type,
        is_desktop,
    ) = _get_skill_set_path_params(
        ctx,
        set_id=request.skill_set_id,
        entity_id=entity_id,
        entity_type=entity_type,
        bot_id=bot_id,
        engine_type=engine_type,
        bot_repo=bot_repo,
        control_plane=control_plane,
    )

    result = await control_plane.legacy_activate(
        bot_id=effective_bot_id,
        owner_id=effective_entity_id,
        actor_id=ctx.user_id,
        set_id=request.skill_set_id,
    )
    return SwitchSkillSetResponse(
        success=True,
        message="Skill set reconciled",
        data={
            "activated": result.get("activated", []),
            "deactivated": result.get("deactivated", []),
            "failed": [],
        },
    )


@router.post("/skillset/activate", response_model=ActivateSkillSetResponse)
@with_interceptors(
    CollaboratorPermissionInterceptor(
        bot_id="$request.bot_id",
        owner_id="$request.entity_id",
    )
)
async def activate_skill_set(
    request: ActivateSkillSetRequest,
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    control_plane: SkillSetManagementServiceProtocol = Injected(
        SkillSetManagementServiceProtocol
    ),
) -> ActivateSkillSetResponse:
    """激活单个能力集（增量激活，不清除其他已激活的能力集）

    支持多个能力集同时激活。默认能力集始终处于激活状态，无需调用此接口。

    Args:
        skill_set_id: 要激活的能力集 ID
        proxy_token: agentclawproxy token，用于同步软链到远程设备
    """
    # Get effective path parameters from request body
    (
        effective_entity_id,
        effective_bot_id,
        effective_engine,
        runtime_engine,
        effective_entity_type,
        is_desktop,
    ) = _get_skill_set_path_params(
        ctx,
        set_id=request.skill_set_id,
        entity_id=request.entity_id,
        entity_type=request.entity_type,
        bot_id=request.bot_id,
        engine_type=request.engine_type,
        bot_repo=bot_repo,
        control_plane=control_plane,
    )

    item = await control_plane.activate(
        bot_id=effective_bot_id,
        owner_id=effective_entity_id,
        user_id=ctx.user_id,
        set_id=request.skill_set_id,
    )

    runtime_message, runtime_projection = _legacy_runtime_projection(item)
    data = {"activated": [item["id"]] if item.get("changed") else [], "failed": []}
    if runtime_projection is not None:
        data["runtime_projection"] = runtime_projection
    return ActivateSkillSetResponse(
        success=True,
        message=runtime_message or "Skill set activated",
        data=data,
    )


@router.post("/skillset/deactivate", response_model=DeactivateSkillSetResponse)
@with_interceptors(
    CollaboratorPermissionInterceptor(
        bot_id="$request.bot_id",
        owner_id="$request.entity_id",
    )
)
async def deactivate_skill_set(
    request: DeactivateSkillSetRequest,
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    control_plane: SkillSetManagementServiceProtocol = Injected(
        SkillSetManagementServiceProtocol
    ),
) -> DeactivateSkillSetResponse:
    """取消激活单个能力集

    注意：默认能力集不允许取消激活

    Args:
        skill_set_id: 要取消激活的能力集 ID
        proxy_token: agentclawproxy token，用于同步软链到远程设备
    """
    # Get effective path parameters from request body
    (
        effective_entity_id,
        effective_bot_id,
        effective_engine,
        runtime_engine,
        effective_entity_type,
        is_desktop,
    ) = _get_skill_set_path_params(
        ctx,
        set_id=request.skill_set_id,
        entity_id=request.entity_id,
        entity_type=request.entity_type,
        bot_id=request.bot_id,
        engine_type=request.engine_type,
        bot_repo=bot_repo,
        control_plane=control_plane,
    )

    item = await control_plane.deactivate(
        bot_id=effective_bot_id,
        owner_id=effective_entity_id,
        user_id=ctx.user_id,
        set_id=request.skill_set_id,
    )

    runtime_message, runtime_projection = _legacy_runtime_projection(item)
    data = {"deactivated": [item["id"]] if item.get("changed") else [], "failed": []}
    if runtime_projection is not None:
        data["runtime_projection"] = runtime_projection
    return DeactivateSkillSetResponse(
        success=True,
        message=runtime_message or "Skill set deactivated",
        data=data,
    )


@router.get("/skillset/active", response_model=ActiveSkillSetsResponse)
@with_interceptors(
    CollaboratorPermissionInterceptor(
        bot_id="$bot_id",
        owner_id="$entity_id",
        persist_audit_log=False,  # 只读操作，不记录日志
    )
)
async def get_active_skill_sets(
    entity_id: str | None = Query(None, description="Entity ID (纯ID，不需要前缀)"),
    entity_type: str | None = Query(
        None, description="Entity type (staff/proj/team, default: staff)"
    ),
    bot_id: str | None = Query(None, description="Bot ID (default: default)"),
    engine_type: str | None = Query(
        None, description="Engine type override; defaults to bot's active_engine"
    ),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    control_plane: SkillSetManagementServiceProtocol = Injected(
        SkillSetManagementServiceProtocol
    ),
) -> ActiveSkillSetsResponse:
    """获取当前 bot 的所有激活能力集列表

    返回用户激活的能力集 + 默认能力集（始终在列表中）
    """
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

    active_sets = [
        item
        for item in control_plane.list_sets(
            bot_id=effective_bot_id,
            owner_id=effective_entity_id,
            user_id=ctx.user_id,
        )
        if item.get("is_active") or item.get("is_default")
    ]

    # 处理返回数据，移除 skills 字段
    for s in active_sets:
        s.pop("skills", None)
        s["bot_id"] = s.pop("bolt_id", effective_bot_id)
        s.pop("type", None)

    return ActiveSkillSetsResponse(
        success=True, data=active_sets, count=len(active_sets)
    )
