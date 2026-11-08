"""Legacy skillsets collection routes."""

from typing import Optional

from agentclaw.community.adapters.http.dependencies import (
    RequestContext,
    get_request_context,
)
from agentclaw.community.adapters.http.skill_center.schemas import (
    CLIInSetResponse,
    CreateSkillSetRequest,
    MCPServerInSetResponse,
    MessageResponse,
    SkillInSetSummary,
    SkillSetDetailResponse,
    SkillSetListResponse,
    SkillSetResourceItem,
    SkillSetResourcesResponse,
    SkillSetsWithMCPsResponse,
    SkillSetWithMCPsItem,
    UpdateSkillSetRequest,
)
from agentclaw.community.api.skill_set_management_service import (
    SkillSetManagementServiceProtocol,
)
from agentclaw.community.core.bot_collaborator.interceptor import (
    CollaboratorPermissionInterceptor,
    with_interceptors,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.di import Injected
from fastapi import Depends, Query

from .skillsets_common import (
    _get_path_params,
    _get_skill_set_path_params,
    _legacy_actor,
    _legacy_skill_set,
)


from fastapi import APIRouter
router = APIRouter(prefix="/api/skillsets", tags=["skillsets"])

@router.get("", response_model=SkillSetListResponse)
@with_interceptors(
    CollaboratorPermissionInterceptor(
        bot_id="$bot_id",
        owner_id="$user_id",
        persist_audit_log=False,  # 只读操作，不记录日志
    )
)
async def list_skill_sets(
    user_id: Optional[str] = Query(None, description="User ID for filtering"),
    entity_id: Optional[str] = Query(None, description="Entity ID (纯ID，不需要前缀)"),
    entity_type: Optional[str] = Query(
        None, description="Entity type (staff/proj/team, default: staff)"
    ),
    bot_id: Optional[str] = Query(None, description="Bot ID (default: default)"),
    engine_type: Optional[str] = Query(
        None, description="Engine type (default: moltis)"
    ),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    control_plane: SkillSetManagementServiceProtocol = Injected(
        SkillSetManagementServiceProtocol
    ),
) -> SkillSetListResponse:
    """List all skill sets with their skills. Default skill set is listed first."""
    # Get effective path parameters
    (
        effective_entity_id,
        effective_bot_id,
        effective_engine,
        runtime_engine,
        effective_entity_type,
        _is_desktop,
    ) = _get_path_params(
        ctx, user_id, entity_type, bot_id, engine_type, bot_repo=bot_repo
    )

    actor_id = _legacy_actor(ctx, user_id or entity_id)
    skill_sets = control_plane.list_sets(
        bot_id=effective_bot_id,
        owner_id=effective_entity_id,
        user_id=actor_id,
    )
    data = []
    for item in skill_sets:
        skills = control_plane.list_skills(
            bot_id=effective_bot_id,
            owner_id=effective_entity_id,
            user_id=actor_id,
            set_id=item["id"],
        )
        skill_set_dict = {
            **item,
            "is_active": True if item.get("is_default") else item.get("is_active"),
            "skills": [
                SkillInSetSummary(
                    id=str(skill.get("id")) if skill.get("id") is not None else "",
                    name=skill.get("name"),
                    description=skill.get("description"),
                    path=skill.get(
                        "git_path"
                    ),  # git_path stores both git:// and local:// paths
                )
                for skill in skills
            ],
        }
        data.append(_legacy_skill_set(skill_set_dict))

    return SkillSetListResponse(success=True, data=data, count=len(data))


@router.post("", response_model=SkillSetDetailResponse)
@with_interceptors(
    CollaboratorPermissionInterceptor(
        bot_id="$request.bot_id",
        owner_id="$request.user_id",
    )
)
async def create_skill_set(
    request: CreateSkillSetRequest,
    entity_id: Optional[str] = Query(None, description="Entity ID (纯ID，不需要前缀)"),
    entity_type: Optional[str] = Query(
        None, description="Entity type (staff/proj/team, default: staff)"
    ),
    bot_id: Optional[str] = Query(None, description="Bot ID (default: default)"),
    engine_type: Optional[str] = Query(
        None, description="Engine type (default: moltis)"
    ),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    control_plane: SkillSetManagementServiceProtocol = Injected(
        SkillSetManagementServiceProtocol
    ),
) -> SkillSetDetailResponse:
    """Create a new skill set."""
    # Priority: query param bot_id > request body bot_id > ctx.bot_id > default
    effective_bot_id = bot_id or request.bot_id or ctx.bot_id or "default"
    # Get effective path parameters (pass effective_bot_id directly)
    (
        effective_entity_id,
        _,
        effective_engine,
        runtime_engine,
        effective_entity_type,
        _is_desktop,
    ) = _get_path_params(
        ctx,
        request.user_id,
        entity_type,
        effective_bot_id,
        engine_type,
        bot_repo=bot_repo,
    )

    skill_set = control_plane.create_set(
        bot_id=effective_bot_id,
        owner_id=effective_entity_id,
        user_id=_legacy_actor(ctx, request.user_id),
        name=request.name,
        description=request.description,
    )
    return SkillSetDetailResponse(success=True, data=_legacy_skill_set(skill_set))


@router.get("/with-mcps", response_model=SkillSetsWithMCPsResponse, deprecated=True)
@with_interceptors(
    CollaboratorPermissionInterceptor(
        bot_id="$bot_id",
        owner_id="$user_id",
        persist_audit_log=False,  # 只读操作，不记录日志
    )
)
async def list_skill_sets_with_mcps(
    user_id: Optional[str] = Query(None, description="User ID for filtering"),
    entity_id: Optional[str] = Query(None, description="Entity ID (纯ID，不需要前缀)"),
    entity_type: Optional[str] = Query(
        None, description="Entity type (staff/proj/team, default: staff)"
    ),
    bot_id: Optional[str] = Query(None, description="Bot ID (default: default)"),
    engine_type: Optional[str] = Query(
        None, description="Engine type (default: moltis)"
    ),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    control_plane: SkillSetManagementServiceProtocol = Injected(
        SkillSetManagementServiceProtocol
    ),
) -> SkillSetsWithMCPsResponse:
    """获取用户所有 skillset 及其关联的 MCP 列表。"""
    # entity_id 优先使用 user_id，否则使用传入的 entity_id
    effective_entity_id_param = user_id or entity_id
    (
        effective_entity_id,
        effective_bot_id,
        effective_engine,
        runtime_engine,
        effective_entity_type,
        is_desktop,
    ) = _get_path_params(
        ctx,
        effective_entity_id_param,
        entity_type,
        bot_id,
        engine_type,
        bot_repo=bot_repo,
    )

    data = [
        SkillSetWithMCPsItem(
            id=str(item["id"]),
            name=item.get("name", ""),
            description=item.get("description"),
            is_default=bool(item.get("is_default")),
            user_id=item.get("user_id"),
            bot_id=item.get("bolt_id") or effective_bot_id,
            mcps=[
                MCPServerInSetResponse(**{**m, "id": str(m["id"])})
                for m in item.get("mcps", [])
            ],
        )
        for item in control_plane.list_resources(
            bot_id=effective_bot_id,
            owner_id=effective_entity_id,
            user_id=_legacy_actor(ctx, user_id or entity_id),
        )
    ]

    return SkillSetsWithMCPsResponse(success=True, data=data, count=len(data))


@router.get("/resources", response_model=SkillSetResourcesResponse)
@with_interceptors(
    CollaboratorPermissionInterceptor(
        bot_id="$bot_id",
        owner_id="$user_id",
        persist_audit_log=False,
    )
)
async def list_skill_set_resources(
    user_id: Optional[str] = Query(None, description="User ID for filtering"),
    entity_id: Optional[str] = Query(None, description="Entity ID (纯ID，不需要前缀)"),
    entity_type: Optional[str] = Query(
        None, description="Entity type (staff/proj/team, default: staff)"
    ),
    bot_id: Optional[str] = Query(None, description="Bot ID (default: default)"),
    engine_type: Optional[str] = Query(
        None, description="Engine type (default: moltis)"
    ),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    control_plane: SkillSetManagementServiceProtocol = Injected(
        SkillSetManagementServiceProtocol
    ),
) -> SkillSetResourcesResponse:
    """获取能力集资源聚合视图（MCP + 默认能力集 CLI）。"""
    effective_entity_id_param = user_id or entity_id
    (
        effective_entity_id,
        effective_bot_id,
        effective_engine,
        runtime_engine,
        effective_entity_type,
        is_desktop,
    ) = _get_path_params(
        ctx,
        effective_entity_id_param,
        entity_type,
        bot_id,
        engine_type,
        bot_repo=bot_repo,
    )

    data = [
        SkillSetResourceItem(
            id=str(item["id"]),
            name=item.get("name", ""),
            description=item.get("description"),
            is_default=bool(item.get("is_default")),
            user_id=item.get("user_id"),
            bot_id=item.get("bolt_id") or effective_bot_id,
            mcps=[
                MCPServerInSetResponse(**{**m, "id": str(m["id"])})
                for m in item.get("mcps", [])
            ],
            clis=[CLIInSetResponse(**cli) for cli in item.get("clis", [])],
        )
        for item in control_plane.list_resources(
            bot_id=effective_bot_id,
            owner_id=effective_entity_id,
            user_id=_legacy_actor(ctx, user_id or entity_id),
        )
    ]

    return SkillSetResourcesResponse(success=True, data=data, count=len(data))


@router.get("/{skill_set_id}", response_model=SkillSetDetailResponse)
async def get_skill_set(
    skill_set_id: str,
    user_id: Optional[str] = Query(None, description="User ID for permission check"),
    entity_id: Optional[str] = Query(None, description="Entity ID (纯ID，不需要前缀)"),
    entity_type: Optional[str] = Query(
        None, description="Entity type (staff/proj/team, default: staff)"
    ),
    bot_id: Optional[str] = Query(None, description="Bot ID (default: default)"),
    engine_type: Optional[str] = Query(
        None, description="Engine type (default: moltis)"
    ),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    control_plane: SkillSetManagementServiceProtocol = Injected(
        SkillSetManagementServiceProtocol
    ),
) -> SkillSetDetailResponse:
    """Get a skill set by ID."""
    owner_id_hint = user_id or entity_id
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
        set_id=skill_set_id,
        entity_id=owner_id_hint,
        entity_type=entity_type,
        bot_id=bot_id,
        engine_type=engine_type,
        bot_repo=bot_repo,
        control_plane=control_plane,
    )

    return SkillSetDetailResponse(
        success=True,
        data=_legacy_skill_set(
            control_plane.get_set(
                bot_id=effective_bot_id,
                owner_id=effective_entity_id,
                user_id=_legacy_actor(ctx, user_id or entity_id),
                set_id=skill_set_id,
            )
        ),
    )


@router.put("/{skill_set_id}", response_model=SkillSetDetailResponse)
async def update_skill_set(
    skill_set_id: str,
    request: UpdateSkillSetRequest,
    entity_id: Optional[str] = Query(None, description="Entity ID (纯ID，不需要前缀)"),
    entity_type: Optional[str] = Query(
        None, description="Entity type (staff/proj/team, default: staff)"
    ),
    bot_id: Optional[str] = Query(None, description="Bot ID (default: default)"),
    engine_type: Optional[str] = Query(
        None, description="Engine type (default: moltis)"
    ),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    control_plane: SkillSetManagementServiceProtocol = Injected(
        SkillSetManagementServiceProtocol
    ),
) -> SkillSetDetailResponse:
    """Update a skill set."""
    owner_id_hint = request.user_id or entity_id
    # Preserve omission so the legacy resolver can recover a non-default Bot.
    bot_id_hint = bot_id or request.bot_id
    (
        effective_entity_id,
        effective_bot_id,
        effective_engine,
        runtime_engine,
        effective_entity_type,
        _is_desktop,
    ) = _get_skill_set_path_params(
        ctx,
        set_id=skill_set_id,
        entity_id=owner_id_hint,
        entity_type=entity_type,
        bot_id=bot_id_hint,
        engine_type=engine_type,
        bot_repo=bot_repo,
        control_plane=control_plane,
    )

    # ``is_default`` was accepted by the old schema but is not a mutable
    # domain field.  Preserve the wire by ignoring it as the old handler
    # did for immutable system sets.
    skill_set = control_plane.update_set(
        bot_id=effective_bot_id,
        owner_id=effective_entity_id,
        user_id=_legacy_actor(ctx, request.user_id or entity_id),
        set_id=skill_set_id,
        name=request.name,
        description=request.description,
    )
    return SkillSetDetailResponse(success=True, data=_legacy_skill_set(skill_set))


@router.delete("/{skill_set_id}", response_model=MessageResponse)
@with_interceptors(
    CollaboratorPermissionInterceptor(
        bot_id="$bot_id",
        owner_id="$user_id",
    )
)
async def delete_skill_set(
    skill_set_id: str,
    user_id: Optional[str] = Query(None, description="User ID for permission check"),
    entity_id: Optional[str] = Query(None, description="Entity ID (纯ID，不需要前缀)"),
    entity_type: Optional[str] = Query(
        None, description="Entity type (staff/proj/team, default: staff)"
    ),
    bot_id: Optional[str] = Query(None, description="Bot ID (default: default)"),
    engine_type: Optional[str] = Query(
        None, description="Engine type (default: moltis)"
    ),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    control_plane: SkillSetManagementServiceProtocol = Injected(
        SkillSetManagementServiceProtocol
    ),
) -> MessageResponse:
    """Delete a skill set."""
    owner_id_hint = user_id or entity_id
    (
        effective_entity_id,
        effective_bot_id,
        effective_engine,
        runtime_engine,
        effective_entity_type,
        is_desktop,
    ) = _get_skill_set_path_params(
        ctx,
        set_id=skill_set_id,
        entity_id=owner_id_hint,
        entity_type=entity_type,
        bot_id=bot_id,
        engine_type=engine_type,
        bot_repo=bot_repo,
        control_plane=control_plane,
    )

    control_plane.delete_set(
        bot_id=effective_bot_id,
        owner_id=effective_entity_id,
        user_id=_legacy_actor(ctx, user_id or entity_id),
        set_id=skill_set_id,
    )
    return MessageResponse(success=True, message="Skill set deleted successfully")
