"""

Migrated from: services/openclawserver/server/routers/skillsets.py
SkillSet router for managing capability sets and their skills.
"""

from pathlib import Path
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Query

from agentclaw.community.adapters.http.skill_center.schemas import (
    CreateSkillSetRequest,
    UpdateSkillSetRequest,
    AddSkillsRequest,
    SetDefaultSkillsRequest,
    SetDefaultSkillsFastRequest,
    FixGitPathRequest,
    AddMCPRequest,
    SetSkillSetActiveRequest,
    SkillInSetSummary,
    SkillSetResponse,
    SkillSetListResponse,
    SkillSetDetailResponse,
    SkillInSetResponse,
    SkillSetSkillsResponse,
    CLIInSetResponse,
    MCPServerInSetResponse,
    SkillSetResourceItem,
    SkillSetResourcesResponse,
    SkillSetWithMCPsItem,
    SkillSetsWithMCPsResponse,
    AddSkillsResponse,
    MessageResponse,
    InitAndSyncResponse,
    SetDefaultSkillsResponse,
    SetDefaultSkillsFastResponse,
    FixGitPathResponse,
    SkillSetMCPsResponse,
    AddMCPResponse,
    SetSkillSetActiveResponse,
)
from agentclaw.community.core.skill_center.legacy_skill_set_compatibility import (
    recover_legacy_skill_set_scope,
)
from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
from agentclaw.community.adapters.http.dependencies import (
    get_request_context,
    RequestContext,
)
from agentclaw.community.core.bot_collaborator.interceptor import (
    CollaboratorPermissionInterceptor,
    with_interceptors,
)

from agentclaw.community.core.repository.protocols.skill_center import (
    SkillSetRepository,
)
from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.bot_management.services.engine_resolver import (
    resolve_engine_for_bot,
    resolve_runtime_engine_for_bot,
)
from agentclaw.community.core.mcp.errors import McpIdentityUnresolvedError
from agentclaw.community.core.mcp.services.passport_scope import (
    filter_passport_mcp_codes,
    passport_mcp_items_from_codes,
    resolve_mcp_identity_modes,
)
from agentclaw.community.core.repository.protocols.identity import (
    CallerIdentityRepositoryProtocol,
)
from agentclaw.community.di import Injected
from agentclaw.community.api.skill_service_factory import SkillServiceFactoryProtocol
from agentclaw.community.api.skill_set_service_factory import (
    SkillSetServiceFactoryProtocol,
)
from agentclaw.community.api.skill_set_management_service import (
    SkillSetManagementServiceProtocol,
)
from agentclaw.community.core.skill_center.errors import (
    LocalSkillNotFoundError,
    SkillSetControlPlaneConflictError,
    SkillSetControlPlaneNotFoundError,
)
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.plugin_api.device_sync_dispatcher import DeviceSyncDispatcher
from agentclaw.community.plugin_api.passport import PassportPlugin
from agentclaw.community.log import get_logger

logger = get_logger()

router = APIRouter(prefix="/api/skillsets", tags=["skillsets"])


# ==================== Helper Functions ====================


def _legacy_actor(ctx: RequestContext, requested_user_id: str | None) -> str:
    """Use the authenticated subject, retaining the pre-auth test fallback.

    Legacy wire bodies contain ``user_id`` for historical routing.  It is not
    an authority grant: normal requests always use the request principal and
    the control plane checks the addressed Bot ACL.
    """
    return str(ctx.user_id or requested_user_id or "")


def _legacy_skill_set(item: dict) -> SkillSetResponse:
    """Project the canonical item back to the published legacy response."""
    return SkillSetResponse(
        **{
            **item,
            "bot_id": item.get("bolt_id") or item.get("bot_id") or "default",
            "skills": item.get("skills", []),
            "type": item.get("type", "custom"),
        }
    )


def _get_path_params(
    ctx: RequestContext,
    entity_id: Optional[str] = None,
    entity_type: Optional[str] = None,
    bot_id: Optional[str] = None,
    engine_type: Optional[str] = None,
    *,
    bot_repo: BotRepository,
) -> tuple:
    """Get effective path parameters.

    Priority:
    1. If entity_id provided: use it directly (pure ID, no prefix needed)
    2. Otherwise: use ctx.user_id
    3. If entity_type provided: use it directly
    4. Otherwise: default to "staff"

    Also looks up ``ac_bots.bot_type`` to decide ``is_desktop``: desktop bots
    route to the agentbox engine-view path; personal/service bots stay on the
    cloud OSS-view path.

    NOTE: do NOT use ``device_provider == "baas"`` for this decision — service
    bots' device_provider is also baas but their bot_type is ``"service"``, not
    ``"desktop"``, and they MUST stay on the cloud branch.

    Returns:
        Tuple of (effective_entity_id, effective_bot_id,
                  effective_engine_type, runtime_engine_type, effective_entity_type, is_desktop)
    """
    # entity_id: use provided or default to user_id (pure ID, no prefix)
    effective_entity_id = (
        entity_id if entity_id else (ctx.user_id if ctx.user_id else "default")
    )

    # entity_type: use provided or default to "staff"
    effective_entity_type = entity_type if entity_type else "staff"

    # bot_id: use provided, or fallback to ctx.bot_id, or default to "default"
    effective_bot_id = bot_id if bot_id else (ctx.bot_id if ctx.bot_id else "default")

    # Use entity_id as owner when supplied (entity_id = bot owner; ctx.user_id = current viewer).
    owner_id_for_lookup = entity_id if entity_id else (ctx.user_id or "")

    effective_engine = resolve_engine_for_bot(
        bot_id=effective_bot_id,
        owner_id=owner_id_for_lookup,
        override=engine_type,
        bot_repo=bot_repo,
    )
    runtime_engine = resolve_runtime_engine_for_bot(
        bot_id=effective_bot_id,
        owner_id=owner_id_for_lookup,
        override=engine_type,
        bot_repo=bot_repo,
    )
    is_desktop = False
    try:
        bot = bot_repo.get_by_id_and_owner(effective_bot_id, owner_id_for_lookup)
        if bot and bot.get("bot_type") == "desktop":
            is_desktop = True
    except Exception as e:
        logger.warning(
            "[_get_path_params] bot_type lookup failed for "
            "bot_id=%s owner=%s: %s — defaulting is_desktop=False",
            effective_bot_id,
            owner_id_for_lookup,
            e,
        )

    return effective_entity_id, effective_bot_id, effective_engine, runtime_engine, effective_entity_type, is_desktop


def _get_skill_set_path_params(
    ctx: RequestContext,
    *,
    set_id: str,
    entity_id: Optional[str],
    entity_type: Optional[str],
    bot_id: Optional[str],
    engine_type: Optional[str],
    bot_repo: BotRepository,
    control_plane: SkillSetManagementServiceProtocol,
) -> tuple:
    """Resolve the deprecated optional Bot address before normal path handling."""
    entity_id, bot_id = recover_legacy_skill_set_scope(
        set_id=set_id,
        actor_id=_legacy_actor(ctx, entity_id),
        owner_id_hint=entity_id,
        bot_id_hint=bot_id,
        control_plane=control_plane,
    )
    return _get_path_params(
        ctx,
        entity_id,
        entity_type,
        bot_id,
        engine_type,
        bot_repo=bot_repo,
    )


# ==================== SkillSet Management APIs ====================


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
        entity_id=entity_id,
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
        entity_id=entity_id,
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
        entity_id=entity_id,
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


# ==================== SkillSet-Skill Association APIs ====================


@router.get("/{skill_set_id}/skills", response_model=SkillSetSkillsResponse)
async def get_skill_set_skills(
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
) -> SkillSetSkillsResponse:
    """Get all skills in a skill set."""
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
        entity_id=entity_id,
        entity_type=entity_type,
        bot_id=bot_id,
        engine_type=engine_type,
        bot_repo=bot_repo,
        control_plane=control_plane,
    )

    skills = control_plane.list_skills(
        bot_id=effective_bot_id,
        owner_id=effective_entity_id,
        user_id=_legacy_actor(ctx, user_id or entity_id),
        set_id=skill_set_id,
    )
    return SkillSetSkillsResponse(
        success=True,
        data=[
            SkillInSetResponse(
                id=str(s.get("id")) if s.get("id") is not None else "",
                name=s.get("name"),
                description=s.get("description"),
                category=s.get("category"),
                git_path=s.get("git_path"),
                tags=s.get("tags") if isinstance(s.get("tags"), list) else [],
            )
            for s in skills
        ],
        count=len(skills),
    )


@router.post("/{skill_set_id}/skills", response_model=AddSkillsResponse)
@with_interceptors(
    CollaboratorPermissionInterceptor(
        bot_id="$request.bot_id",
        owner_id="$request.user_id",
    )
)
async def add_skills_to_set(
    skill_set_id: str,
    request: AddSkillsRequest,
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
) -> AddSkillsResponse:
    """Add skills to a skill set."""
    # Preserve omission so the legacy resolver can recover a non-default Bot.
    bot_id_hint = request.bot_id or bot_id
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
        entity_id=entity_id,
        entity_type=entity_type,
        bot_id=bot_id_hint,
        engine_type=engine_type,
        bot_repo=bot_repo,
        control_plane=control_plane,
    )

    # Historical batch wire permits partial success.  Each member remains
    # an atomic control-plane command; the adapter only serializes results.
    results: dict[str, list] = {
        "success": [],
        "failed": [],
        "activation_failed": [],
    }
    actor_id = _legacy_actor(ctx, request.user_id or entity_id)
    # Validate the target before the legacy resolver may materialise a
    # missing Repo asset.  A missing or immutable Set must not leave an
    # orphan ac_skill row behind.
    target_set = control_plane.get_set(
        bot_id=effective_bot_id,
        owner_id=effective_entity_id,
        user_id=actor_id,
        set_id=skill_set_id,
    )
    if target_set.get("is_default"):
        raise SkillSetControlPlaneConflictError("SYSTEM_DEFAULT_IMMUTABLE")
    for skill_id in request.skill_ids:
        try:
            stable_skill_id = control_plane.resolve_legacy_skill_id(
                bot_id=effective_bot_id,
                owner_id=effective_entity_id,
                actor_id=actor_id,
                identifier=skill_id,
            )
            await control_plane.add_skill(
                bot_id=effective_bot_id,
                owner_id=effective_entity_id,
                user_id=actor_id,
                set_id=skill_set_id,
                skill_id=stable_skill_id,
            )
            results["success"].append(
                {"skill_id": str(stable_skill_id), "name": str(skill_id)}
            )
        except (
            LocalSkillNotFoundError,
            SkillSetControlPlaneNotFoundError,
        ) as exc:
            results["failed"].append({"skill_id": skill_id, "error": str(exc)})
        except SkillSetControlPlaneConflictError as exc:
            if str(exc) not in {
                "RESOURCE_DIRECT_ACTIVE",
                "RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET",
            }:
                raise
            results["failed"].append({"skill_id": skill_id, "error": str(exc)})
    success_count = len(results["success"])
    failed_count = len(results["failed"])
    return AddSkillsResponse(
        success=True,
        data=results,
        message=f"成功添加 {success_count} 个技能，失败 {failed_count} 个",
    )


@router.delete("/{skill_set_id}/skills/{skill_id}", response_model=MessageResponse)
@with_interceptors(
    CollaboratorPermissionInterceptor(
        bot_id="$bot_id",
        owner_id="$user_id",
    )
)
async def remove_skill_from_set(
    skill_set_id: str,
    skill_id: str,
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
    """Remove a skill from a skill set."""
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
        entity_id=entity_id,
        entity_type=entity_type,
        bot_id=bot_id,
        engine_type=engine_type,
        bot_repo=bot_repo,
        control_plane=control_plane,
    )

    result = await control_plane.remove_skill(
        bot_id=effective_bot_id,
        owner_id=effective_entity_id,
        user_id=_legacy_actor(ctx, user_id or entity_id),
        set_id=skill_set_id,
        skill_id=skill_id,
    )
    if not result.get("changed"):
        raise SkillSetControlPlaneNotFoundError("Skill not found in skill set")
    return MessageResponse(success=True, message="Skill removed from skill set")


# ==================== Default SkillSet APIs ====================


@router.post("/default/ensure", response_model=SkillSetDetailResponse)
async def ensure_default_skill_set(
    user_id: Optional[str] = Query(None, description="User ID"),
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
    skill_set_service_factory: SkillSetServiceFactoryProtocol = Injected(
        SkillSetServiceFactoryProtocol
    ),
) -> SkillSetDetailResponse:
    """Ensure default skill set exists."""
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

    service = skill_set_service_factory.create(
        entity_id=effective_entity_id,
        bot_id=effective_bot_id,
        engine_type=effective_engine,
        entity_type=effective_entity_type,
    )
    skill_set = service.ensure_default_skill_set(
        user_id=ctx.user_id, bolt_id=effective_bot_id
    )
    # Remove 'skills' key if present to avoid Pydantic validation error
    skill_set_clean = {k: v for k, v in skill_set.items() if k != "skills"}
    skill_set_clean["is_active"] = True
    # Map bolt_id to bot_id for frontend
    skill_set_clean["bot_id"] = skill_set_clean.get("bolt_id") or "default"
    # Remove original bolt_id from response
    skill_set_clean.pop("bolt_id", None)
    return SkillSetDetailResponse(
        success=True, data=SkillSetResponse(**skill_set_clean)
    )


@router.get("/default/current", response_model=SkillSetDetailResponse)
async def get_default_skill_set(
    user_id: Optional[str] = Query(None, description="User ID"),
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
    skill_set_service_factory: SkillSetServiceFactoryProtocol = Injected(
        SkillSetServiceFactoryProtocol
    ),
) -> SkillSetDetailResponse:
    """Get the default skill set."""
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

    service = skill_set_service_factory.create(
        entity_id=effective_entity_id,
        bot_id=effective_bot_id,
        engine_type=effective_engine,
        entity_type=effective_entity_type,
    )
    skill_set = service.get_default_skill_set(
        user_id=ctx.user_id, bolt_id=effective_bot_id
    )
    if not skill_set:
        raise HTTPException(status_code=404, detail="No default skill set found")
    # Remove 'skills' key if present to avoid Pydantic validation error
    skill_set_clean = {k: v for k, v in skill_set.items() if k != "skills"}
    skill_set_clean["is_active"] = True
    # Map bolt_id to bot_id for frontend
    skill_set_clean["bot_id"] = skill_set_clean.get("bolt_id") or "default"
    # Remove original bolt_id from response
    skill_set_clean.pop("bolt_id", None)
    return SkillSetDetailResponse(
        success=True, data=SkillSetResponse(**skill_set_clean)
    )


@router.get("/admin/init-and-sync", response_model=InitAndSyncResponse)
async def init_and_sync_default_skills(
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
    path_factory: WorkspacePathFactory = Injected(WorkspacePathFactory),
    skill_service_factory: SkillServiceFactoryProtocol = Injected(
        SkillServiceFactoryProtocol
    ),
    skill_set_service_factory: SkillSetServiceFactoryProtocol = Injected(
        SkillSetServiceFactoryProtocol
    ),
) -> InitAndSyncResponse:
    """Admin interface: Initialize default skill set and sync all Git skills into it.

    This endpoint will:
    1. Create default skill set if not exists
    2. Scan all skills in skills-repo
    3. Add all git skills to default skill set
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

    # Get user-specific paths using new directory structure
    skills_dir = path_factory.get_bot_skills_dir(
        effective_entity_id, effective_bot_id, effective_engine, effective_entity_type
    )
    local_dir = path_factory.get_bot_skills_local_dir(
        effective_entity_id,
        effective_bot_id,
        effective_engine,
        effective_entity_type,
        is_desktop=is_desktop,
    )
    path_factory.get_bot_engine_dir(
        effective_entity_id, effective_bot_id, effective_engine, effective_entity_type
    )
    repo_dir = path_factory.get_bot_skills_repo_dir(
        effective_entity_id,
        effective_bot_id,
        effective_engine,
        effective_entity_type,
        is_desktop=is_desktop,
    )

    set_service = skill_set_service_factory.create(
        entity_id=effective_entity_id,
        bot_id=effective_bot_id,
        engine_type=effective_engine,
        entity_type=effective_entity_type,
    )
    skill_service = skill_service_factory.create(
        active_dir=skills_dir,
        repo_dir=repo_dir,
        local_dir=local_dir,
        entity_id=effective_entity_id,
        bot_id=effective_bot_id,
        engine_type=runtime_engine,
    )

    # Step 1: Ensure default skill set exists
    skill_set = set_service.ensure_default_skill_set(user_id=None)
    skill_set_id = skill_set.get("id")

    # Add type field for response
    skill_set["type"] = "default"

    # Step 2: Scan all git skills
    sync_result = {
        "scanned": 0,
        "created": 0,
        "added": 0,
        "skipped": 0,
        "failed": 0,
        "errors": [],
    }

    try:
        # Get all skills from git repo
        git_skills = skill_service.get_skills_in_path("")
        sync_result["scanned"] = len(git_skills)

        # Get existing skills in default set
        existing_skills = set_service.get_set_skills(skill_set_id, user_id=None)
        existing_skill_ids = {str(s.get("id")) for s in existing_skills if s.get("id")}

        # Step 3: Process each git skill
        skill_ids_to_add = []
        for gs in git_skills:
            try:
                skill_id = gs.get("id")
                full_path = gs.get("full_path", "")

                if not skill_id or not full_path:
                    continue

                # Check if skill exists in database
                skill = skill_service.get_skill_by_path(f"git://{skill_id}")

                if not skill:
                    # Create skill from market data
                    skill = set_service._create_skill_from_market(gs, user_id=None)
                    if skill:
                        sync_result["created"] += 1

                if skill:
                    skill_db_id = str(skill.get("id"))
                    if skill_db_id in existing_skill_ids:
                        sync_result["skipped"] += 1
                    else:
                        skill_ids_to_add.append(skill_db_id)

            except Exception as e:
                sync_result["failed"] += 1
                sync_result["errors"].append(
                    f"Failed to process {gs.get('id', 'unknown')}: {str(e)}"
                )

        # Step 4: Add skills to default set (bypass user restriction)
        if skill_ids_to_add:
            try:
                for skill_id in skill_ids_to_add:
                    try:
                        # Check association exists
                        existing = set_service.skill_set_repo.get_skills_in_set(
                            skill_set_id
                        )
                        if not any(str(s.get("id")) == skill_id for s in existing):
                            set_service.skill_set_repo.add_skill_to_set(
                                skill_set_id, skill_id
                            )
                            sync_result["added"] += 1
                    except Exception as e:
                        sync_result["failed"] += 1
                        sync_result["errors"].append(
                            f"Failed to add skill {skill_id}: {str(e)}"
                        )

            except Exception as e:
                sync_result["errors"].append(f"Batch add failed: {str(e)}")

    except Exception as e:
        sync_result["errors"].append(f"Sync failed: {str(e)}")

    return InitAndSyncResponse(
        success=True,
        skill_set={
            "id": str(skill_set_id),
            "name": skill_set.get("name"),
            "type": "default",
        },
        sync=sync_result,
    )


@router.post("/admin/set-default-skills", response_model=SetDefaultSkillsResponse)
async def set_default_skills(
    request: SetDefaultSkillsRequest,
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
    path_factory: WorkspacePathFactory = Injected(WorkspacePathFactory),
    skill_service_factory: SkillServiceFactoryProtocol = Injected(
        SkillServiceFactoryProtocol
    ),
    skill_set_service_factory: SkillSetServiceFactoryProtocol = Injected(
        SkillSetServiceFactoryProtocol
    ),
) -> SetDefaultSkillsResponse:
    """Admin interface: Set specific skills as the default skill set content.

    This endpoint will:
    1. Create default skill set if not exists
    2. Clear existing skills in default set
    3. Add specified skills to default set
    4. Sync skills to skills-default directory
    """
    from agentclaw.community.core.workspace.path_factory import (
        get_global_skills_repo_dir,
        get_global_skills_default_dir,
    )
    import shutil

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
        effective_entity_id, effective_bot_id, effective_engine, effective_entity_type
    )
    local_dir = path_factory.get_bot_skills_local_dir(
        effective_entity_id,
        effective_bot_id,
        effective_engine,
        effective_entity_type,
        is_desktop=is_desktop,
    )
    path_factory.get_bot_engine_dir(
        effective_entity_id, effective_bot_id, effective_engine, effective_entity_type
    )
    repo_dir = path_factory.get_bot_skills_repo_dir(
        effective_entity_id,
        effective_bot_id,
        effective_engine,
        effective_entity_type,
        is_desktop=is_desktop,
    )

    set_service = skill_set_service_factory.create(
        entity_id=effective_entity_id,
        bot_id=effective_bot_id,
        engine_type=effective_engine,
        entity_type=effective_entity_type,
    )
    skill_service = skill_service_factory.create(
        active_dir=skills_dir,
        repo_dir=repo_dir,
        local_dir=local_dir,
        entity_id=effective_entity_id,
        bot_id=effective_bot_id,
        engine_type=runtime_engine,
    )

    # Step 1: Ensure default skill set exists
    skill_set = set_service.ensure_default_skill_set(user_id=None)
    skill_set_id = skill_set.get("id")

    # Add type field for response
    skill_set["type"] = "default"

    # Step 2: Get existing skills in default set
    existing_skills = set_service.get_set_skills(skill_set_id, user_id=None)
    existing_skill_ids = {str(s.get("id")) for s in existing_skills if s.get("id")}

    sync_result = {
        "requested": len(request.skill_ids),
        "added": 0,
        "removed": 0,
        "skipped": 0,
        "not_found": 0,
        "copied": 0,
        "deleted": 0,
        "errors": [],
    }

    # Step 3: Remove skills not in the new list
    skill_ids_to_keep = set(request.skill_ids)
    for existing in existing_skills:
        existing_id = str(existing.get("id"))
        if existing_id not in skill_ids_to_keep:
            try:
                set_service.skill_set_repo.remove_skill_from_set(
                    skill_set_id, existing_id
                )
                sync_result["removed"] += 1
            except Exception as e:
                sync_result["errors"].append(
                    f"Failed to remove skill {existing_id}: {str(e)}"
                )

    # Step 4: Add new skills and collect skill info for directory sync
    skills_to_sync = []  # [(skill_name, git_path), ...]

    # Pre-load market skills for lookup
    market_skills = skill_service.get_skills_in_path("")
    market_skills_map = {}
    for ms in market_skills:
        # Build lookup map by link_name and git_path
        link_name = ms.get("link_name", "")
        ms.get("full_path", "")
        ms_id = ms.get("id", "")
        if link_name:
            market_skills_map[link_name] = ms
        if ms_id:
            market_skills_map[ms_id] = ms

    for skill_id in request.skill_ids:
        try:
            # Check if skill exists (try by ID -> link_name -> git_path -> market)
            skill = None

            # 1. Try by numeric ID
            if skill_id.isdigit():
                try:
                    skill = skill_service.get_skill(skill_id, user_id=None)
                except (ValueError, AttributeError):
                    pass

            # 2. Try by link_name (e.g., infra_demo_odps-sql-generator)
            if not skill:
                skill = skill_service.get_skill_by_link_name(skill_id)

            # 3. Try by git_path (fallback)
            if not skill:
                skill = skill_service.get_skill_by_path(f"git://{skill_id}")
                if not skill:
                    all_skills = skill_service.list_skills()
                    for s in all_skills:
                        git_path = s.get("git_path", "")
                        if (
                            git_path.endswith(f"/{skill_id}")
                            or git_path == f"git://{skill_id}"
                        ):
                            skill = s
                            break

            # 4. Try from market (skills-repo) and create if not found in DB
            if not skill:
                market_skill = market_skills_map.get(skill_id)
                if market_skill:
                    # Create skill record from market data
                    skill = set_service._create_skill_from_market(
                        market_skill, user_id=None
                    )
                    if skill:
                        logger.info(
                            f"[set_default_skills] Created skill from market: {skill_id} -> {skill.get('id')}"
                        )

            if not skill:
                sync_result["not_found"] += 1
                sync_result["errors"].append(f"Skill not found: {skill_id}")
                continue

            skill_db_id = str(skill.get("id"))
            git_path = skill.get("git_path", "")
            skill_name = skill.get("name", "")

            # Check if already in set
            if skill_db_id in existing_skill_ids:
                sync_result["skipped"] += 1
                # Still add to sync list
                if git_path:
                    skills_to_sync.append((skill_name, git_path))
                continue

            # Add to set
            set_service.skill_set_repo.add_skill_to_set(skill_set_id, skill_db_id)
            sync_result["added"] += 1

            # Add to sync list
            if git_path:
                skills_to_sync.append((skill_name, git_path))

        except Exception as e:
            sync_result["errors"].append(f"Failed to add skill {skill_id}: {str(e)}")

    # Step 5: Sync skills to skills-default directory
    try:
        # Get paths
        skills_repo_path = get_global_skills_repo_dir()
        skills_default_path = get_global_skills_default_dir()

        logger.info(
            f"[set_default_skills] Syncing to skills-default: {skills_default_path}"
        )
        logger.info(f"[set_default_skills] Skills repo: {skills_repo_path}")

        # Ensure skills-default directory exists
        skills_default_path.mkdir(parents=True, exist_ok=True)

        # Get list of skill names to keep
        skill_names_to_keep = set()
        for skill_name, git_path in skills_to_sync:
            skill_names_to_keep.add(skill_name)

        # Remove skills not in the list
        if skills_default_path.exists():
            for item in skills_default_path.iterdir():
                if item.is_dir() and item.name not in skill_names_to_keep:
                    try:
                        shutil.rmtree(item)
                        sync_result["deleted"] += 1
                        logger.info(f"[set_default_skills] Deleted: {item.name}")
                    except Exception as e:
                        sync_result["errors"].append(
                            f"Failed to delete {item.name}: {str(e)}"
                        )

        # Copy skills to skills-default
        for skill_name, git_path in skills_to_sync:
            if git_path.startswith("git://"):
                rel_path = git_path[6:]  # Remove 'git://' prefix
                src_dir = skills_repo_path / rel_path
                dst_dir = skills_default_path / skill_name

                if src_dir.exists():
                    try:
                        # Remove existing if exists
                        if dst_dir.exists():
                            shutil.rmtree(dst_dir)
                        # Copy the skill directory
                        shutil.copytree(src_dir, dst_dir)
                        sync_result["copied"] += 1
                        logger.info(f"[set_default_skills] Copied: {skill_name}")
                    except Exception as e:
                        sync_result["errors"].append(
                            f"Failed to copy {skill_name}: {str(e)}"
                        )
                else:
                    sync_result["errors"].append(f"Source skill not found: {rel_path}")

    except Exception as e:
        logger.error(f"[set_default_skills] Failed to sync to skills-default: {e}")
        sync_result["errors"].append(f"Directory sync failed: {str(e)}")

    return SetDefaultSkillsResponse(
        success=True,
        skill_set={
            "id": str(skill_set_id),
            "name": skill_set.get("name"),
            "type": "default",
        },
        sync=sync_result,
    )


@router.post(
    "/admin/set-default-skills-fast", response_model=SetDefaultSkillsFastResponse
)
async def set_default_skills_fast(
    request: SetDefaultSkillsFastRequest,
    entity_id: Optional[str] = Query(None, description="Entity ID (纯ID，不需要前缀)"),
    entity_type: Optional[str] = Query(
        None, description="Entity type (staff/proj/team, default: staff)"
    ),
    bot_id: Optional[str] = Query(None, description="Bot ID (default: default)"),
    engine_type: Optional[str] = Query(None, description="引擎类型 (默认: moltis)"),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    path_factory: WorkspacePathFactory = Injected(WorkspacePathFactory),
    skill_service_factory: SkillServiceFactoryProtocol = Injected(
        SkillServiceFactoryProtocol
    ),
    skill_set_service_factory: SkillSetServiceFactoryProtocol = Injected(
        SkillSetServiceFactoryProtocol
    ),
) -> SetDefaultSkillsFastResponse:
    """Admin interface: Set specific skills as the default skill set content (FAST VERSION).

    This endpoint will:
    1. Create default skill set if not exists
    2. Clear existing skills in default set
    3. Add specified skills to default set (DB only, no file operations)

    Note: File sync to skills-default directory is no longer needed.
    Skill symlinks are generated dynamically from DB via get_symlink_mappings().
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

    # Get user-specific paths using new directory structure
    skills_dir = path_factory.get_bot_skills_dir(
        effective_entity_id, effective_bot_id, effective_engine, effective_entity_type
    )
    local_dir = path_factory.get_bot_skills_local_dir(
        effective_entity_id,
        effective_bot_id,
        effective_engine,
        effective_entity_type,
        is_desktop=is_desktop,
    )
    repo_dir = path_factory.get_bot_skills_repo_dir(
        effective_entity_id,
        effective_bot_id,
        effective_engine,
        effective_entity_type,
        is_desktop=is_desktop,
    )

    set_service = skill_set_service_factory.create(
        entity_id=effective_entity_id,
        bot_id=effective_bot_id,
        engine_type=effective_engine,
        entity_type=effective_entity_type,
    )
    skill_service = skill_service_factory.create(
        active_dir=skills_dir,
        repo_dir=repo_dir,
        local_dir=local_dir,
        entity_id=effective_entity_id,
        bot_id=effective_bot_id,
        engine_type=runtime_engine,
    )

    # Step 1: Ensure default skill set exists
    skill_set = set_service.ensure_default_skill_set(user_id=None)
    skill_set_id = skill_set.get("id")

    # Add type field for response
    skill_set["type"] = "default"

    # Step 2: Get existing skills in default set
    existing_skills = set_service.get_set_skills(skill_set_id, user_id=None)
    existing_skill_ids = {str(s.get("id")) for s in existing_skills if s.get("id")}

    sync_result = {
        "requested": len(request.skill_ids),
        "added": 0,
        "removed": 0,
        "skipped": 0,
        "not_found": 0,
        "copied": 0,  # 保留字段，但不再使用
        "deleted": 0,  # 保留字段，但不再使用
        "errors": [],
    }

    # Step 3: Remove skills not in the new list
    skill_ids_to_keep = set(request.skill_ids)
    for existing in existing_skills:
        existing_id = str(existing.get("id"))
        if existing_id not in skill_ids_to_keep:
            try:
                set_service.skill_set_repo.remove_skill_from_set(
                    skill_set_id, existing_id
                )
                sync_result["removed"] += 1
            except Exception as e:
                sync_result["errors"].append(
                    f"Failed to remove skill {existing_id}: {str(e)}"
                )

    # Step 4: Add new skills (DB only)
    # Pre-load market skills for lookup
    market_skills = skill_service.get_skills_in_path("")
    market_skills_map = {}
    for ms in market_skills:
        link_name = ms.get("link_name", "")
        ms_id = ms.get("id", "")
        if link_name:
            market_skills_map[link_name] = ms
        if ms_id:
            market_skills_map[ms_id] = ms

    for skill_id in request.skill_ids:
        try:
            # Check if skill exists (try by ID -> link_name -> market)
            skill = None

            # 1. Try by numeric ID
            if skill_id.isdigit():
                try:
                    skill = skill_service.get_skill(skill_id, user_id=None)
                except (ValueError, AttributeError):
                    pass

            # 2. Try by link_name
            if not skill:
                skill = skill_service.get_skill_by_link_name(skill_id)

            # 3. Try from market and create if not found in DB
            if not skill:
                market_skill = market_skills_map.get(skill_id)
                if market_skill:
                    skill = set_service._create_skill_from_market(
                        market_skill, user_id=None
                    )
                    if skill:
                        logger.info(
                            f"[set_default_skills_fast] Created skill from market: {skill_id} -> {skill.get('id')}"
                        )

            if not skill:
                sync_result["not_found"] += 1
                sync_result["errors"].append(f"Skill not found: {skill_id}")
                continue

            skill_db_id = str(skill.get("id"))

            # Check if already in set
            if skill_db_id in existing_skill_ids:
                sync_result["skipped"] += 1
                continue

            # Add to set
            set_service.skill_set_repo.add_skill_to_set(skill_set_id, skill_db_id)
            sync_result["added"] += 1

        except Exception as e:
            sync_result["errors"].append(f"Failed to add skill {skill_id}: {str(e)}")

    # Step 5: 文件同步已移除
    # 软链通过 get_symlink_mappings() 从 DB 动态生成，不再需要复制文件到 skills-default 目录
    logger.info(
        "[set_default_skills_fast] DB update completed, skipping file sync "
        "(symlinks generated dynamically)"
    )

    return SetDefaultSkillsFastResponse(
        success=True,
        skill_set={
            "id": str(skill_set_id),
            "name": skill_set.get("name"),
            "type": "default",
        },
        sync=sync_result,
    )


# ==================== Admin Fix APIs ====================


@router.post("/admin/fix-git-path", response_model=FixGitPathResponse)
async def fix_git_path(
    request: FixGitPathRequest = FixGitPathRequest(),
    ctoken: Optional[str] = Query(None),
    ctx: RequestContext = Depends(get_request_context),
    skill_service_factory: SkillServiceFactoryProtocol = Injected(
        SkillServiceFactoryProtocol
    ),
    skill_set_service_factory: SkillSetServiceFactoryProtocol = Injected(
        SkillSetServiceFactoryProtocol
    ),
) -> FixGitPathResponse:
    """Admin interface: Fix incorrect git_path in ac_skill table.

    This endpoint will:
    1. Build correct path mapping from skills-repo directory structure
    2. Match database records by skill directory name
    3. Update incorrect git_path (unless dry_run=True)

    Args:
        dry_run: If True, only report what would be changed without making changes
    """
    from agentclaw.community.core.workspace.path_factory import (
        get_global_skills_repo_dir,
    )

    # Get global skills repo directory
    global_repo_dir = get_global_skills_repo_dir()

    # Create skill service for market scanning
    skill_service = skill_service_factory.create(
        active_dir=Path("/tmp/skills"),
        repo_dir=global_repo_dir,
        local_dir=Path("/tmp/skills-local"),
    )

    # Create skill set service for repo access
    set_service = skill_set_service_factory.create(
        entity_id="",
        bot_id="default",
        engine_type=DEFAULT_ENGINE_TYPE,
        runtime_engine_type=DEFAULT_ENGINE_TYPE,
        entity_type="staff",
    )

    # Step 1: Build correct path mapping from skills-repo
    # key: skill_dir_name (e.g., query-user-trade-record-list)
    # value: correct_git_path (e.g., git://business/riskinsight/query-user-trade-record-list)
    market_skills = skill_service.get_skills_in_path("")
    correct_path_map = {}
    for ms in market_skills:
        link_name = ms.get("link_name", "")
        if link_name:
            # link_name: business_riskinsight_query-user-trade-record-list
            # correct_git_path: git://business/riskinsight/query-user-trade-record-list
            correct_git_path = f"git://{link_name.replace('_', '/')}"
            # skill_dir_name is the last part
            skill_dir_name = link_name.split("_")[-1] if "_" in link_name else link_name
            correct_path_map[skill_dir_name] = correct_git_path

    logger.info(
        f"[fix_git_path] Built correct path map with {len(correct_path_map)} skills"
    )

    # Step 2: Get all skills from database
    all_db_skills = set_service.skill_repo.list_skills()

    result = {"checked": 0, "fixed": 0, "unchanged": 0, "details": []}

    # Step 3: Check and fix each database record
    for db_skill in all_db_skills:
        existing_git_path = db_skill.get("git_path", "")
        if not existing_git_path.startswith("git://"):
            continue

        result["checked"] += 1

        # Extract skill_dir_name from existing git_path
        # git://riskinsight/query-user-trade-record-list -> query-user-trade-record-list
        existing_rel_path = existing_git_path[6:]  # Remove 'git://'
        skill_dir_name = existing_rel_path.split("/")[-1]

        # Find correct git_path from market
        correct_git_path = correct_path_map.get(skill_dir_name)

        if not correct_git_path:
            # Skill not found in market, skip
            continue

        if existing_git_path != correct_git_path:
            existing_id = db_skill.get("id")
            detail = {
                "id": existing_id,
                "name": db_skill.get("name"),
                "skill_dir": skill_dir_name,
                "old_git_path": existing_git_path,
                "new_git_path": correct_git_path,
                "action": "needs_fix" if request.dry_run else "will_fix",
            }

            if not request.dry_run:
                # Update the record
                set_service.skill_repo.update(
                    str(existing_id), {"git_path": correct_git_path}
                )
                detail["action"] = "fixed"
                logger.info(
                    f"[fix_git_path] Fixed: {skill_dir_name}: {existing_git_path} -> {correct_git_path}"
                )
                result["fixed"] += 1
            else:
                result["fixed"] += 1  # Count as would-be fixed in dry run

            result["details"].append(detail)
        else:
            result["unchanged"] += 1

    logger.info(
        f"[fix_git_path] Completed: checked={result['checked']}, fixed={result['fixed']}, unchanged={result['unchanged']}"
    )

    return FixGitPathResponse(
        success=True,
        checked=result["checked"],
        fixed=result["fixed"],
        unchanged=result["unchanged"],
        details=result["details"][:100],  # Limit details to first 100
    )


# ==================== SkillSet-MCP Association APIs ====================


@router.get("/{skill_set_id}/mcps", response_model=SkillSetMCPsResponse)
async def get_skill_set_mcps(
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
    skill_set_service_factory: SkillSetServiceFactoryProtocol = Injected(
        SkillSetServiceFactoryProtocol
    ),
    control_plane: SkillSetManagementServiceProtocol = Injected(
        SkillSetManagementServiceProtocol
    ),
) -> SkillSetMCPsResponse:
    """Get all MCP servers in a skill set."""
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
        entity_id=entity_id,
        entity_type=entity_type,
        bot_id=bot_id,
        engine_type=engine_type,
        bot_repo=bot_repo,
        control_plane=control_plane,
    )

    legacy_set = control_plane.get_set(
        bot_id=effective_bot_id,
        owner_id=effective_entity_id,
        user_id=_legacy_actor(ctx, entity_id),
        set_id=skill_set_id,
    )
    if legacy_set.get("is_default"):
        service = skill_set_service_factory.create(
            entity_id=effective_entity_id,
            bot_id=effective_bot_id,
            engine_type=effective_engine,
            entity_type=effective_entity_type,
        )
        mcps = service.get_set_mcp_servers(skill_set_id, user_id=ctx.user_id)
    else:
        mcps = control_plane.list_mcps(
            bot_id=effective_bot_id,
            owner_id=effective_entity_id,
            user_id=_legacy_actor(ctx, entity_id),
            set_id=skill_set_id,
        )
    return SkillSetMCPsResponse(
        success=True,
        data=[
            MCPServerInSetResponse(
                id=str(m.get("id")),
                server_code=m.get("server_code", ""),
                name=m.get("name", ""),
                description=m.get("description"),
                icon=m.get("icon"),
                status=m.get("status", "ONLINE"),
            )
            for m in mcps
        ],
        count=len(mcps),
    )


@router.post("/{skill_set_id}/mcps", response_model=AddMCPResponse)
@with_interceptors(
    CollaboratorPermissionInterceptor(
        bot_id="$bot_id",
        owner_id="$entity_id",
    )
)
async def add_mcp_to_skill_set(
    skill_set_id: str,
    request: AddMCPRequest,
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
    skill_set_service_factory: SkillSetServiceFactoryProtocol = Injected(
        SkillSetServiceFactoryProtocol
    ),
    control_plane: SkillSetManagementServiceProtocol = Injected(
        SkillSetManagementServiceProtocol
    ),
) -> AddMCPResponse:
    """Add an MCP server to a skill set."""
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
        entity_id=entity_id,
        entity_type=entity_type,
        bot_id=bot_id,
        engine_type=engine_type,
        bot_repo=bot_repo,
        control_plane=control_plane,
    )

    await control_plane.add_mcp(
        bot_id=effective_bot_id,
        owner_id=effective_entity_id,
        user_id=_legacy_actor(ctx, entity_id),
        set_id=skill_set_id,
        server_code=request.server_code,
    )
    return AddMCPResponse(
        success=True,
        message="MCP server added to skill set successfully",
        server_code=request.server_code,
    )


@router.delete("/{skill_set_id}/mcps/{server_code}", response_model=MessageResponse)
@with_interceptors(
    CollaboratorPermissionInterceptor(
        bot_id="$bot_id",
        owner_id="$entity_id",
    )
)
async def remove_mcp_from_skill_set(
    skill_set_id: str,
    server_code: str,
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
    skill_set_service_factory: SkillSetServiceFactoryProtocol = Injected(
        SkillSetServiceFactoryProtocol
    ),
    control_plane: SkillSetManagementServiceProtocol = Injected(
        SkillSetManagementServiceProtocol
    ),
) -> MessageResponse:
    """Remove an MCP server from a skill set."""
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
        entity_id=entity_id,
        entity_type=entity_type,
        bot_id=bot_id,
        engine_type=engine_type,
        bot_repo=bot_repo,
        control_plane=control_plane,
    )

    await control_plane.remove_mcp(
        bot_id=effective_bot_id,
        owner_id=effective_entity_id,
        user_id=_legacy_actor(ctx, entity_id),
        set_id=skill_set_id,
        server_code=server_code,
    )
    return MessageResponse(
        success=True, message="MCP server removed from skill set successfully"
    )


@router.delete("/{skill_set_id}/clis/{resource_code}", response_model=MessageResponse)
@with_interceptors(
    CollaboratorPermissionInterceptor(
        bot_id="$bot_id",
        owner_id="$entity_id",
    )
)
async def remove_cli_from_default_skill_set(
    skill_set_id: str,
    resource_code: str,
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
    skill_set_service_factory: SkillSetServiceFactoryProtocol = Injected(
        SkillSetServiceFactoryProtocol
    ),
    passport_plugin: PassportPlugin = Injected(PassportPlugin),
    caller_identity_repo: CallerIdentityRepositoryProtocol = Injected(
        CallerIdentityRepositoryProtocol
    ),
) -> MessageResponse:
    """Remove a CLI from the default skill set by updating AgentPass CLI scope."""
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

    service = skill_set_service_factory.create(
        entity_id=effective_entity_id,
        bot_id=effective_bot_id,
        engine_type=effective_engine,
        entity_type=effective_entity_type,
    )
    skill_set = service.get_skill_set(skill_set_id, user_id=effective_entity_id)
    if not skill_set:
        raise HTTPException(status_code=404, detail="Skill set not found")
    if not bool(skill_set.get("is_default")):
        raise HTTPException(
            status_code=400, detail="CLI can only be removed from the default skill set"
        )

    try:
        clis = passport_plugin.query_passport_clis(
            effective_bot_id, effective_entity_id
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to query CLI scope") from e
    if not any(cli.get("cli_code") == resource_code for cli in clis):
        raise HTTPException(status_code=404, detail="CLI not found in passport scope")
    remaining = [cli for cli in clis if cli.get("cli_code") != resource_code]
    # 删除 CLI 也是覆盖式更新；先补齐当前 MCP，避免只传 CLI 时覆盖掉 MCP 范围。
    try:
        mcp_codes = service.get_bot_mcp_codes(
            entity_id=effective_entity_id,
            bot_id=effective_bot_id,
            user_id=effective_entity_id,
            entity_type=effective_entity_type,
            engine_type=effective_engine,
        )
        passport_mcp_codes = filter_passport_mcp_codes(mcp_codes)
    except Exception as e:
        logger.warning(
            "[remove_cli_from_default_skill_set] collect MCP scope failed: bot_id=%s, cli=%s, error=%s",
            effective_bot_id,
            resource_code,
            e,
        )
        raise HTTPException(
            status_code=500, detail="Failed to collect MCP scope"
        ) from e

    # 覆盖式更新连 identityMode 一起替换：只传 code 等于把每个 MCP 断言成
    # owner，会静默抹掉 caller 授权。删 CLI 不该动 MCP 身份，所以这里必须
    # 把当前身份原样带上；查不到身份就整体失败，不猜默认值。
    try:
        bot = bot_repo.get_by_id_and_owner(effective_bot_id, effective_entity_id)
        identity_modes = resolve_mcp_identity_modes(
            caller_identity_repo,
            bot_pk=(bot or {}).get("id"),
            engine_type=effective_engine,
            bot_id=effective_bot_id,
        )
        mcp_items = passport_mcp_items_from_codes(
            passport_mcp_codes, identity_modes=identity_modes
        )
    except (McpIdentityUnresolvedError, ValueError) as e:
        logger.warning(
            "[remove_cli_from_default_skill_set] resolve MCP identity failed: "
            "bot_id=%s, cli=%s, error=%s",
            effective_bot_id,
            resource_code,
            e,
        )
        raise HTTPException(
            status_code=500, detail="Failed to resolve MCP execution identity"
        ) from e

    try:
        # resource_scope 成套提交 MCP+CLI；不涉及 Skill，Skill 由 AgentPass 侧自行订正。
        passport_plugin.update_passport(
            bot_id=effective_bot_id,
            user_id=effective_entity_id,
            resource_scope={
                # mcp_codes 由 mcp_items 派生：unpack_resource_scope 在有
                # mcp_items 时忽略 mcp_codes，两份独立列表只会悄悄分叉。
                "mcp_codes": [item["mcp_code"] for item in mcp_items],
                "mcp_items": mcp_items,
                "cli_items": remaining,
            },
        )
    except Exception as e:
        logger.warning(
            "[remove_cli_from_default_skill_set] updatePassport CLI scope failed: bot_id=%s, cli=%s, error=%s",
            effective_bot_id,
            resource_code,
            e,
        )
        raise HTTPException(status_code=500, detail="Failed to update CLI scope") from e

    return MessageResponse(
        success=True, message="CLI removed from passport scope successfully"
    )


@router.post("/sync-skills", summary="同步 Skill symlinks 到本地设备")
async def sync_skills_to_device(
    entity_id: Optional[str] = Query(None, description="Entity ID"),
    entity_type: Optional[str] = Query(None, description="Entity type"),
    bot_id: Optional[str] = Query(None, description="Bot ID"),
    engine_type: Optional[str] = Query(None, description="Engine type"),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    skill_set_service_factory: SkillSetServiceFactoryProtocol = Injected(
        SkillSetServiceFactoryProtocol
    ),
    resolver: DeviceContextResolver = Injected(DeviceContextResolver),
    device_sync_dispatcher: DeviceSyncDispatcher = Injected(DeviceSyncDispatcher),
):
    """切换 Bot 时触发 skill symlink 同步。

    Dispatcher 根据设备上下文选择 Core ``DeviceSync`` 服务，并以 full-sync
    语义将当前 bot 的 active skill-set 软链接同步到目标运行时。
    """
    try:
        (
            effective_entity_id,
            effective_bot_id,
            effective_engine,
            runtime_engine,
            effective_entity_type,
            _is_desktop,
        ) = _get_path_params(
            ctx, entity_id, entity_type, bot_id, engine_type, bot_repo=bot_repo
        )
        user_id = ctx.user_id or "default"

        service = skill_set_service_factory.create(
            entity_id=effective_entity_id,
            bot_id=effective_bot_id,
            engine_type=effective_engine,
            runtime_engine_type=runtime_engine,
            entity_type=effective_entity_type,
        )

        # 获取当前 bot 所有 active 能力集的合并 symlink mappings
        # (Installation-keyed on the Bot's OWNER, not the acting user)
        symlinks = service.get_symlink_mappings(
            user_id=effective_entity_id,
            bolt_id=effective_bot_id,
        )
        symlinks_dict = [sm.to_dict() for sm in symlinks]

        # 通过 provider-specific DeviceSync 服务同步到设备。
        ctx_dev = resolver.resolve_for_bot(effective_bot_id, user_id)
        device_sync = device_sync_dispatcher.dispatch(ctx_dev)
        sync_result = device_sync.sync_symlinks(symlinks_dict)

        logger.info(
            f"[sync_skills_to_device] bot_id={effective_bot_id}, "
            f"symlinks={len(symlinks_dict)}, result={sync_result}"
        )

        return {
            "success": True,
            "bot_id": effective_bot_id,
            "symlinks_count": len(symlinks_dict),
            "sync_result": sync_result,
        }
    except Exception as e:
        logger.error(f"[sync_skills_to_device] Failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Internal error during skill sync: {str(e)}"
        )


# ==================== Admin Temporary APIs (DEPRECATED) ====================


@router.post("/admin/set-skillset-active", response_model=SetSkillSetActiveResponse)
async def set_skillset_active(
    request: SetSkillSetActiveRequest,
    ctx: RequestContext = Depends(get_request_context),
    skill_set_repo: SkillSetRepository = Injected(SkillSetRepository),
) -> SetSkillSetActiveResponse:
    """[DEPRECATED] 临时接口：设置指定能力集的激活状态

    注意：此接口仅用于预发环境数据调整，后续会删除

    Args:
        env: 环境标识
        skillset_id: 技能集 ID
        is_active: 激活状态，0 或 1

    Returns:
        更新的记录数
    """
    # 验证 is_active 只能是 0 或 1
    if request.is_active not in (0, 1):
        raise HTTPException(status_code=400, detail="is_active must be 0 or 1")

    logger.info(
        f"[set_skillset_active] Admin request: env={request.env}, skillset_id={request.skillset_id}, is_active={request.is_active}"
    )

    try:
        # 使用 repository 直接更新（通过 DI 工厂，自动选择 local/prod 实现）
        updated_count = skill_set_repo.set_skillset_active(
            env=request.env,
            skillset_id=request.skillset_id,
            is_active=request.is_active,
        )

        logger.info(f"[set_skillset_active] Updated {updated_count} skillsets")

        action = "激活" if request.is_active == 1 else "取消激活"
        return SetSkillSetActiveResponse(
            success=True,
            message=f"已{action} {updated_count} 个能力集",
            updated_count=updated_count,
        )
    except Exception as e:
        logger.error(f"[set_skillset_active] Failed: {e}")
        raise HTTPException(status_code=500, detail=f"设置能力集状态失败: {str(e)}")
