"""Legacy skillsets delivery routes."""

import time
from typing import Optional

from agentclaw.community.adapters.http.dependencies import (
    RequestContext,
    get_request_context,
)
from agentclaw.community.adapters.http.skill_center.schemas import (
    MessageResponse,
    SetSkillSetActiveRequest,
    SetSkillSetActiveResponse,
)
from agentclaw.community.api.skill_set_service_factory import (
    SkillSetServiceFactoryProtocol,
)
from agentclaw.community.core.bot_collaborator.interceptor import (
    CollaboratorPermissionInterceptor,
    with_interceptors,
)
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.core.mcp.errors import McpIdentityUnresolvedError
from agentclaw.community.core.mcp.services.cli_passport_scope import (
    build_passport_resource_scope,
)
from agentclaw.community.core.mcp.services.passport_scope import (
    filter_passport_mcp_codes,
    passport_mcp_items_from_codes,
    resolve_mcp_identity_modes,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.identity import (
    CallerIdentityRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.skill_center import (
    SkillSetRepository,
)
from agentclaw.community.di import Injected
from agentclaw.community.plugin_api.device_sync_dispatcher import DeviceSyncDispatcher
from agentclaw.community.plugin_api.passport import PassportPlugin, extract_cli_items
from fastapi import Depends, HTTPException, Query

from .skillsets_common import (
    _get_path_params,
    logger,
)


from fastapi import APIRouter
router = APIRouter(prefix="/api/skillsets", tags=["skillsets"])

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

    scope_started = time.monotonic()
    stage = "snapshot"
    logger.info(
        "agentpass_default_cli_scope_update_requested bot_id=%s engine_type=%s "
        "branch=remove_default_cli stage=%s target_cli_code=%s mcp_count=%s cli_count=%s duration_ms=%s",
        effective_bot_id,
        effective_engine,
        stage,
        resource_code,
        "unknown",
        "unknown",
        0,
    )
    try:
        passport = passport_plugin.query_agent_passport(
            effective_bot_id, effective_entity_id
        )
        clis = extract_cli_items(passport)
    except Exception as exc:
        logger.warning(
            "agentpass_default_cli_scope_update_failed bot_id=%s engine_type=%s "
            "branch=remove_default_cli stage=%s target_cli_code=%s status=failed "
            "error_type=%s duration_ms=%s",
            effective_bot_id,
            effective_engine,
            stage,
            resource_code,
            type(exc).__name__,
            int((time.monotonic() - scope_started) * 1000),
        )
        raise HTTPException(
            status_code=500, detail="Failed to query CLI scope"
        ) from None
    if not any(cli.get("cli_code") == resource_code for cli in clis):
        raise HTTPException(status_code=404, detail="CLI not found in passport scope")
    remaining = [cli for cli in clis if cli.get("cli_code") != resource_code]
    # 删除 CLI 也是覆盖式更新；先补齐当前 MCP，避免只传 CLI 时覆盖掉 MCP 范围。
    stage = "collect_mcp"
    try:
        mcp_codes = service.get_bot_mcp_codes(
            entity_id=effective_entity_id,
            bot_id=effective_bot_id,
            user_id=effective_entity_id,
            entity_type=effective_entity_type,
            engine_type=effective_engine,
        )
        passport_mcp_codes = filter_passport_mcp_codes(mcp_codes)
    except Exception as exc:
        logger.warning(
            "agentpass_default_cli_scope_update_failed bot_id=%s engine_type=%s "
            "branch=remove_default_cli stage=%s target_cli_code=%s status=failed "
            "error_type=%s duration_ms=%s",
            effective_bot_id,
            effective_engine,
            stage,
            resource_code,
            type(exc).__name__,
            int((time.monotonic() - scope_started) * 1000),
        )
        raise HTTPException(
            status_code=500, detail="Failed to collect MCP scope"
        ) from None

    # 覆盖式更新连 identityMode 一起替换：只传 code 等于把每个 MCP 断言成
    # owner，会静默抹掉 caller 授权。删 CLI 不该动 MCP 身份，所以这里必须
    # 把当前身份原样带上；查不到身份就整体失败，不猜默认值。
    stage = "build"
    try:
        bot = bot_repo.get_by_id_and_entity(effective_bot_id, effective_entity_id)
        identity_modes = resolve_mcp_identity_modes(
            caller_identity_repo,
            bot_pk=(bot or {}).get("id"),
            engine_type=effective_engine,
            bot_id=effective_bot_id,
        )
        desired_mcp_items = passport_mcp_items_from_codes(
            passport_mcp_codes, identity_modes=identity_modes
        )
        resource_scope = build_passport_resource_scope(
            passport,
            desired_mcp_items=desired_mcp_items,
            mcp_identity_modes=identity_modes,
            additional_cli_items=remaining,
            removed_cli_codes={resource_code},
        )
    except (McpIdentityUnresolvedError, ValueError) as exc:
        logger.warning(
            "agentpass_default_cli_scope_update_failed bot_id=%s engine_type=%s "
            "branch=remove_default_cli stage=%s target_cli_code=%s status=failed "
            "error_type=%s duration_ms=%s",
            effective_bot_id,
            effective_engine,
            stage,
            resource_code,
            type(exc).__name__,
            int((time.monotonic() - scope_started) * 1000),
        )
        raise HTTPException(
            status_code=500, detail="Failed to resolve MCP execution identity"
        ) from None

    stage = "update"
    try:
        # resource_scope 成套提交 MCP+CLI；不涉及 Skill，Skill 由 AgentPass 侧自行订正。
        passport_plugin.update_passport(
            bot_id=effective_bot_id,
            user_id=effective_entity_id,
            resource_scope=resource_scope,
        )
    except Exception as exc:
        logger.warning(
            "agentpass_default_cli_scope_update_failed bot_id=%s engine_type=%s "
            "branch=remove_default_cli stage=%s target_cli_code=%s status=failed "
            "error_type=%s duration_ms=%s",
            effective_bot_id,
            effective_engine,
            stage,
            resource_code,
            type(exc).__name__,
            int((time.monotonic() - scope_started) * 1000),
        )
        raise HTTPException(
            status_code=500, detail="Failed to update CLI scope"
        ) from None

    logger.info(
        "agentpass_default_cli_scope_update_succeeded bot_id=%s engine_type=%s "
        "branch=remove_default_cli stage=%s target_cli_code=%s status=succeeded "
        "mcp_count=%s cli_count=%s duration_ms=%s",
        effective_bot_id,
        effective_engine,
        stage,
        resource_code,
        len(resource_scope["mcp_items"]),
        len(resource_scope["cli_items"]),
        int((time.monotonic() - scope_started) * 1000),
    )

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
