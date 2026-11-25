"""Legacy skills asset routes."""

from typing import Any, Dict, Optional

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.dependencies import (
    RequestContext,
    get_request_context,
)
from agentclaw.community.adapters.http.skill_center.schemas import (
    MessageResponse,
    SkillDetailResponse,
    SkillMetadataResponse,
    UpdateMCPDependenciesRequest,
    UpdateRiskTagsRequest,
    UpdateSkillRequest,
)
from agentclaw.community.api.local_skill_delete_service import (
    LocalSkillDeleteServiceProtocol,
)
from agentclaw.community.api.skill_service_factory import SkillServiceFactoryProtocol
from agentclaw.community.core.access.admin_scopes import skill_admin
from agentclaw.community.core.bot_collaborator.interceptor import (
    with_interceptors,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.skill_center import SkillRepository
from agentclaw.community.core.skill_center.errors import (
    LocalSkillActiveError,
    LocalSkillEditBusyError,
    LocalSkillEditLockUnavailableError,
    LocalSkillEditPausedError,
    LocalSkillLayoutRollbackError,
    LocalSkillNotFoundError,
    LocalSkillNotReadyError,
    LocalSkillStorageError,
    SkillAssetInUseError,
    SkillReferencedBySkillSetError,
)
from agentclaw.community.di import Injected
from fastapi import (
    Depends,
    HTTPException,
    Query,
    Request,
)

from .skills_common import (
    DEFAULT_ENGINE_TYPE,
    _extract_skill_mutation_permission,
    _FailClosedSkillMutationPermissionInterceptor,
    extract_from_skill_id,
    logger,
    parse_tags,
)


from fastapi import APIRouter
router = APIRouter(prefix="/api/skills", tags=["skills"])

@router.get("/{skill_id}", response_model=SkillDetailResponse)
async def get_skill(
    skill_id: str,
    user_id: Optional[str] = Query(None, description="User ID for permission check"),
    skill_service_factory: SkillServiceFactoryProtocol = Injected(
        SkillServiceFactoryProtocol
    ),
) -> SkillDetailResponse:
    """Get a skill by ID or link_name."""
    service = skill_service_factory.create()

    # Try to get by ID first (if it's a digit)
    skill = None
    if skill_id.isdigit():
        skill = service.get_skill(skill_id, user_id=user_id)

    # If not found by ID, try by link_name
    if not skill:
        skill = service.get_skill_by_link_name(skill_id)

    if not skill:
        raise HTTPException(
            status_code=500,
            detail=f"Skill lookup failed: the identifier '{skill_id}' does not match any existing skill ID or link_name",
        )
    return SkillDetailResponse(
        success=True,
        data=SkillMetadataResponse(
            id=str(skill.get("id")) if skill.get("id") is not None else "",
            name=skill.get("name"),
            description=skill.get("description"),
            git_path=skill.get("git_path"),
            link_name=skill.get("link_name"),
            category=skill.get("category"),
            tags=parse_tags(skill.get("tags")),
            risk_tags=skill.get("risk_tags") or [],
            mcp_dependencies=skill.get("mcp_dependencies") or [],
            input_schema=skill.get("input_schema"),
            output_schema=skill.get("output_schema"),
            is_public=skill.get("is_public"),
            is_builtin=skill.get("is_builtin"),
            user_id=str(skill.get("user_id"))
            if skill.get("user_id") is not None
            else None,
            gmt_created=skill.get("gmt_created") if skill.get("gmt_created") else "",
            gmt_modified=skill.get("gmt_modified") if skill.get("gmt_modified") else "",
        ),
    )


@router.put("/{skill_id}", response_model=SkillDetailResponse)
async def update_skill(
    skill_id: str,
    request: UpdateSkillRequest,
    skill_service_factory: SkillServiceFactoryProtocol = Injected(
        SkillServiceFactoryProtocol
    ),
) -> SkillDetailResponse:
    """Update a skill."""
    service = skill_service_factory.create()
    try:
        update_data = request.model_dump(exclude_unset=True, exclude={"user_id"})
        skill = service.update_skill(skill_id, user_id=request.user_id, **update_data)
        if not skill:
            raise HTTPException(status_code=404, detail="Skill not found")
        return SkillDetailResponse(
            success=True,
            data=SkillMetadataResponse(
                id=str(skill.get("id")) if skill.get("id") is not None else "",
                name=skill.get("name"),
                description=skill.get("description"),
                git_path=skill.get("git_path"),
                link_name=skill.get("link_name"),
                category=skill.get("category"),
                tags=parse_tags(skill.get("tags")),
                risk_tags=skill.get("risk_tags") or [],
                mcp_dependencies=skill.get("mcp_dependencies") or [],
                input_schema=skill.get("input_schema"),
                output_schema=skill.get("output_schema"),
                is_public=skill.get("is_public"),
                is_builtin=skill.get("is_builtin"),
                user_id=str(skill.get("user_id"))
                if skill.get("user_id") is not None
                else None,
                bot_id=skill.get("bolt_id") if skill.get("bolt_id") else "default",
                gmt_created=skill.get("gmt_created")
                if skill.get("gmt_created")
                else "",
                gmt_modified=skill.get("gmt_modified")
                if skill.get("gmt_modified")
                else "",
            ),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{skill_id}/risk-tags", response_model=SkillDetailResponse)
async def update_skill_risk_tags(
    skill_id: str,
    request: UpdateRiskTagsRequest,
    skill_service_factory: SkillServiceFactoryProtocol = Injected(
        SkillServiceFactoryProtocol
    ),
) -> SkillDetailResponse:
    """Update skill risk tags by ID."""
    service = skill_service_factory.create()
    try:
        skill = service.update_skill(
            skill_id, user_id=request.user_id, risk_tags=request.risk_tags
        )
        if not skill:
            raise HTTPException(status_code=404, detail="Skill not found")
        return SkillDetailResponse(
            success=True,
            data=SkillMetadataResponse(
                id=str(skill.get("id")) if skill.get("id") is not None else "",
                name=skill.get("name"),
                description=skill.get("description"),
                git_path=skill.get("git_path"),
                link_name=skill.get("link_name"),
                category=skill.get("category"),
                tags=parse_tags(skill.get("tags")),
                risk_tags=skill.get("risk_tags") or [],
                mcp_dependencies=skill.get("mcp_dependencies") or [],
                input_schema=skill.get("input_schema"),
                output_schema=skill.get("output_schema"),
                is_public=skill.get("is_public"),
                is_builtin=skill.get("is_builtin"),
                user_id=str(skill.get("user_id"))
                if skill.get("user_id") is not None
                else None,
                bot_id=skill.get("bolt_id") if skill.get("bolt_id") else "default",
                gmt_created=skill.get("gmt_created")
                if skill.get("gmt_created")
                else "",
                gmt_modified=skill.get("gmt_modified")
                if skill.get("gmt_modified")
                else "",
            ),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{skill_id}/mcp-dependencies", response_model=SkillDetailResponse)
@with_interceptors(
    _FailClosedSkillMutationPermissionInterceptor(
        params_extractor=_extract_skill_mutation_permission,
        extractor_params={"skill_id": "$skill_id"},
        persist_audit_log=True,
        authorization_state_key="skill_mcp_collaborator_authorized",
    )
)
async def update_skill_mcp_dependencies(
    skill_id: str,
    request: UpdateMCPDependenciesRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    http_request: Request = None,
    skill_service_factory: SkillServiceFactoryProtocol = Injected(
        SkillServiceFactoryProtocol
    ),
) -> SkillDetailResponse:
    """Update skill MCP dependencies by ID.

    权限控制：只有技能的创建者或管理员可以修改 MCP 依赖。
    """
    service = skill_service_factory.create()
    try:
        existing = service.get_skill(skill_id, user_id=user.staffId)
        if not existing:
            raise HTTPException(status_code=404, detail="Skill not found")
        skill_owner = str(existing.get("user_id", ""))
        is_local_skill = str(existing.get("git_path") or "").startswith("local://")
        collaborator_authorized = bool(
            http_request
            and getattr(
                http_request.state,
                "skill_mcp_collaborator_authorized",
                False,
            )
        )
        if (
            user.staffId not in skill_admin()
            and skill_owner
            and skill_owner != user.staffId
            and not (is_local_skill and collaborator_authorized)
        ):
            raise HTTPException(
                status_code=403,
                detail="无权修改此技能的MCP依赖，仅技能创建者、已授权协作者或管理员可操作",
            )
        # 使用认证用户ID替代请求体中的user_id，防止越权
        if request.user_id and request.user_id != user.staffId:
            logger.warning(
                f"[update_skill_mcp_dependencies] 权限拒绝: user={user.staffId} 尝试以 user_id={request.user_id} 修改 skill_id={skill_id} 的MCP依赖，已使用认证用户ID"
            )
        skill = service.update_skill(
            skill_id, user_id=user.staffId, mcp_dependencies=request.mcp_dependencies
        )
        if not skill:
            raise HTTPException(status_code=404, detail="Skill not found")
        return SkillDetailResponse(
            success=True,
            data=SkillMetadataResponse(
                id=str(skill.get("id")) if skill.get("id") is not None else "",
                name=skill.get("name"),
                description=skill.get("description"),
                git_path=skill.get("git_path"),
                link_name=skill.get("link_name"),
                category=skill.get("category"),
                tags=parse_tags(skill.get("tags")),
                risk_tags=skill.get("risk_tags") or [],
                mcp_dependencies=skill.get("mcp_dependencies") or [],
                input_schema=skill.get("input_schema"),
                output_schema=skill.get("output_schema"),
                is_public=skill.get("is_public"),
                is_builtin=skill.get("is_builtin"),
                user_id=str(skill.get("user_id"))
                if skill.get("user_id") is not None
                else None,
                bot_id=skill.get("bolt_id") if skill.get("bolt_id") else "default",
                gmt_created=skill.get("gmt_created")
                if skill.get("gmt_created")
                else "",
                gmt_modified=skill.get("gmt_modified")
                if skill.get("gmt_modified")
                else "",
            ),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{link_name}/id", response_model=dict[str, Any])
async def get_skill_id_by_link_name(
    link_name: str,
    skill_service_factory: SkillServiceFactoryProtocol = Injected(
        SkillServiceFactoryProtocol
    ),
) -> Dict[str, Any]:
    """Get skill ID by link_name (e.g., 'infra_demo_odps-sql-generator')."""
    service = skill_service_factory.create()
    skill = service.get_skill_by_link_name(link_name)
    if not skill:
        raise HTTPException(
            status_code=404, detail=f"Skill not found with link_name: {link_name}"
        )
    return {
        "success": True,
        "id": str(skill.get("id")),
        "link_name": skill.get("link_name"),
        "name": skill.get("name"),
    }


@router.delete("/{skill_id}", response_model=MessageResponse)
@with_interceptors(
    _FailClosedSkillMutationPermissionInterceptor(
        params_extractor=extract_from_skill_id,
        extractor_params={"skill_id": "$skill_id"},  # 表达式：从路由参数取值
        persist_audit_log=True,  # 记录操作审计日志
        authorization_state_key="skill_delete_collaborator_authorized",
    )
)
async def delete_skill(
    skill_id: str,
    user_id: str | None = Query(
        None,
        description="User ID for permission check (optional, uses current user if not provided)",
    ),
    entity_id: str | None = Query(
        None, description="Entity ID (pure ID, no prefix needed)"
    ),
    entity_type: str | None = Query(
        None, description="Entity type (staff/proj/team, default: staff)"
    ),
    bot_id: str | None = Query(None, description="Bot ID"),
    engine_type: str | None = Query(
        None,
        description="Legacy compatibility hint; when provided it must match the Bot active_engine",
    ),
    ctx: RequestContext = Depends(get_request_context),
    skill_repo: SkillRepository = Injected(SkillRepository),
    bot_repo: BotRepository = Injected(BotRepository),
    skill_service_factory: SkillServiceFactoryProtocol = Injected(
        SkillServiceFactoryProtocol
    ),
    local_delete: LocalSkillDeleteServiceProtocol = Injected(
        LocalSkillDeleteServiceProtocol
    ),
) -> MessageResponse:
    """Delete a skill.

    权限控制：
    - Owner 可直接删除自己的 skill
    - 协作者（admin 角色）在持锁时可删除
    - 协作者（member 角色）返回 403
    - 管理员可直接删除（Service 层双重保障）
    """
    # 删除的物理上下文必须以已持久化 Skill 所属 Bot 为准。历史客户端
    # 不传 bot_id/entity_id，不能因此回退到 default/openclaw 并删错路径。
    # ``user_id`` is a legacy compatibility hint supplied by the caller and
    # must never override the authenticated actor.  In particular, shared
    # market Skills have no owner row, so trusting the query parameter here
    # would let any authenticated caller impersonate a configured Skill admin.
    current_user_id = ctx.user_id
    if not current_user_id:
        raise HTTPException(status_code=401, detail="未认证用户无法删除技能")
    if not skill_id.isdigit():
        raise HTTPException(status_code=400, detail="Invalid skill ID")

    skill = skill_repo.get_by_id(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    persisted_git_path = str(skill.get("git_path") or "")
    is_governed_content_source = persisted_git_path.startswith(("git://", "center://"))
    if is_governed_content_source:
        persisted_owner_id = str(skill.get("user_id") or "")
        persisted_bot_id = str(skill.get("bolt_id") or "")
        collaborator_authorized = bool(
            ctx.metadata.get("skill_delete_collaborator_authorized")
        )
        if persisted_owner_id:
            if not persisted_bot_id:
                raise HTTPException(
                    status_code=409,
                    detail="Skill is missing its Bot identity",
                )
            if bot_id and bot_id != persisted_bot_id:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error_code": "SKILL_BOT_CONTEXT_MISMATCH",
                        "message": "删除技能必须使用 Skill 持久化归属的 Bot",
                        "bot_id": persisted_bot_id,
                    },
                )
            scoped_bot = bot_repo.get_by_id_and_entity(
                persisted_bot_id, persisted_owner_id
            )
            if not scoped_bot:
                raise HTTPException(status_code=404, detail="Bot not found")
            active_engine = str(scoped_bot.get("active_engine") or DEFAULT_ENGINE_TYPE)
            if engine_type and engine_type != active_engine:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error_code": "SKILL_ENGINE_CONTEXT_MISMATCH",
                        "message": "删除技能必须使用 Bot 当前的生效引擎",
                        "active_engine": active_engine,
                    },
                )
            service = skill_service_factory.create(
                bot_id=persisted_bot_id,
                bot_owner_id=persisted_owner_id,
                engine_type=active_engine,
            )
        else:
            service = skill_service_factory.create()
        try:
            success = await service.delete_skill(
                skill_id,
                user_id=current_user_id,
                authorized_bot_owner_id=persisted_owner_id or None,
                collaborator_authorization_verified=collaborator_authorized,
            )
            if not success:
                raise HTTPException(status_code=404, detail="Skill not found")
            return MessageResponse(
                success=True,
                message="Skill deleted successfully",
            )
        except SkillReferencedBySkillSetError as e:
            raise HTTPException(
                status_code=409,
                detail={
                    "error_code": "SKILL_REFERENCED_BY_SKILL_SET",
                    "message": "请先从所有技能集中移除该技能，再删除技能",
                    "skill_set_ids": e.skill_set_ids,
                },
            ) from e
        except SkillAssetInUseError as e:
            raise HTTPException(
                status_code=409,
                detail={
                    "error_code": "SKILL_ASSET_IN_USE",
                    "message": "请先解除该 Skill 的生效或能力集引用，再删除 Skill",
                    "blockers": e.blocker_counts,
                },
            ) from e
        except ValueError as e:
            error_msg = str(e)
            if "无权删除" in error_msg or "Permission denied" in error_msg:
                raise HTTPException(status_code=403, detail=error_msg) from e
            raise HTTPException(status_code=400, detail=error_msg) from e

    effective_bot_id = str(skill.get("bolt_id") or "")
    if not effective_bot_id:
        raise HTTPException(status_code=409, detail="Skill is missing its Bot identity")
    if bot_id and bot_id != effective_bot_id:
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "SKILL_BOT_CONTEXT_MISMATCH",
                "message": "删除技能必须使用 Skill 持久化归属的 Bot",
                "bot_id": effective_bot_id,
            },
        )

    if persisted_git_path.startswith("local://"):
        if engine_type:
            scoped_bot = bot_repo.get_by_id_and_entity(
                effective_bot_id, str(skill.get("user_id") or "")
            )
            if not scoped_bot:
                raise HTTPException(status_code=404, detail="Bot not found")
            active_engine = str(scoped_bot.get("active_engine") or DEFAULT_ENGINE_TYPE)
            if engine_type != active_engine:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error_code": "SKILL_ENGINE_CONTEXT_MISMATCH",
                        "message": "删除技能必须使用 Bot 当前的生效引擎",
                        "active_engine": active_engine,
                    },
                )
        try:
            await local_delete.delete_local_skill(
                skill_id=skill_id,
                owner_id=str(skill.get("user_id") or ""),
                user_id=current_user_id,
            )
            return MessageResponse(
                success=True,
                message="Skill deleted successfully",
            )
        except SkillAssetInUseError as e:
            raise HTTPException(
                status_code=409,
                detail={
                    "error_code": "SKILL_ASSET_IN_USE",
                    "message": "请先解除该 Skill 的生效或能力集引用，再删除 Skill",
                    "blockers": e.blocker_counts,
                },
            ) from e
        except LocalSkillNotFoundError as e:
            raise HTTPException(status_code=404, detail="Skill not found") from e
        except (
            LocalSkillActiveError,
            LocalSkillEditBusyError,
            LocalSkillEditPausedError,
            LocalSkillLayoutRollbackError,
        ) as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        except LocalSkillNotReadyError as e:
            raise HTTPException(status_code=409, detail="Bot is not ready") from e
        except LocalSkillEditLockUnavailableError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e
        except LocalSkillStorageError as e:
            raise HTTPException(
                status_code=502, detail="Skill storage operation failed"
            ) from e

    raise HTTPException(
        status_code=409,
        detail={
            "error_code": "SKILL_SOURCE_IDENTITY_INVALID",
            "message": "历史 Skill 缺少可安全删除的内容定位，请联系管理员处理",
        },
    )
