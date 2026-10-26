"""Legacy skillsets mcp routes."""

from pathlib import Path
from typing import Optional

from agentclaw.community.adapters.http.dependencies import (
    RequestContext,
    get_request_context,
)
from agentclaw.community.adapters.http.skill_center.schemas import (
    AddMCPRequest,
    AddMCPResponse,
    FixGitPathRequest,
    FixGitPathResponse,
    MCPServerInSetResponse,
    MessageResponse,
    SkillSetMCPsResponse,
)
from agentclaw.community.api.skill_service_factory import SkillServiceFactoryProtocol
from agentclaw.community.api.skill_set_management_service import (
    SkillSetManagementServiceProtocol,
)
from agentclaw.community.api.skill_set_service_factory import (
    SkillSetServiceFactoryProtocol,
)
from agentclaw.community.core.bot_collaborator.interceptor import (
    CollaboratorPermissionInterceptor,
    with_interceptors,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE
from agentclaw.community.di import Injected
from fastapi import Depends, Query

from .skillsets_common import (
    _get_skill_set_path_params,
    _legacy_actor,
    logger,
)


from fastapi import APIRouter
router = APIRouter(prefix="/api/skillsets", tags=["skillsets"])

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

    legacy_set = control_plane.get_set(
        bot_id=effective_bot_id,
        owner_id=effective_entity_id,
        user_id=_legacy_actor(ctx, owner_id_hint),
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
            user_id=_legacy_actor(ctx, owner_id_hint),
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
