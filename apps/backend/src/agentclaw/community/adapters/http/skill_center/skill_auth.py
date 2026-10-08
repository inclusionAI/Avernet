"""

Migrated from: services/openclawserver/server/routers/skill_auth.py
Skill / SkillSet / Bot 维度鉴权 & 批量权限申请 Router
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from agentclaw.community.adapters.http.skill_center.schemas import (
    ApplyPermissionRequest, ApplySkillSetPermissionRequest, ApplyBotPermissionRequest,
    SkillPermissionResponse, ApplySkillPermissionResponse,
    SkillSetPermissionResponse,
    ApplySkillSetPermissionResponse, BotPermissionResponse, ApplyBotPermissionResponse,
)
from agentclaw.community.adapters.http.dependencies import get_request_context, RequestContext
from agentclaw.community.di import Injected
from agentclaw.community.api.skill_auth_service import SkillAuthServiceProtocol
from agentclaw.community.log import get_logger

router = APIRouter(prefix="/api/skill", tags=["skill-auth"])
logger = get_logger()
logger.info("[NEW-ARCH] skill_auth router loaded from api.skill_center (Facade layer)")


# ==================== Skill 维度 ====================


@router.get("/permission", response_model=SkillPermissionResponse)
async def check_skill_permission(
    skill_id: str = Query(..., description="Skill ID"),
    ctx: RequestContext = Depends(get_request_context),
    svc: SkillAuthServiceProtocol = Injected(SkillAuthServiceProtocol),
) -> SkillPermissionResponse:
    """检查用户对 Skill 所有 MCP 依赖的权限状态。"""
    try:
        return svc.check_skill_permission(user_id=ctx.user_id, skill_id=skill_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("[skill_auth] check_skill_permission failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/permission/apply", response_model=ApplySkillPermissionResponse)
async def apply_skill_permission(
    request: ApplyPermissionRequest,
    ctx: RequestContext = Depends(get_request_context),
    svc: SkillAuthServiceProtocol = Injected(SkillAuthServiceProtocol),
) -> ApplySkillPermissionResponse:
    """为用户批量申请 Skill 所有 MCP 依赖的权限。"""
    try:
        return svc.apply_skill_permission(
            user_id=ctx.user_id,
            skill_id=request.skill_id,
            reason=request.reason,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("[skill_auth] apply_skill_permission failed")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== SkillSet 维度 ====================


@router.get("/set/permission", response_model=SkillSetPermissionResponse)
async def check_skill_set_permission(
    skill_set_id: str = Query(..., description="SkillSet ID"),
    ctx: RequestContext = Depends(get_request_context),
    svc: SkillAuthServiceProtocol = Injected(SkillAuthServiceProtocol),
) -> SkillSetPermissionResponse:
    """检查用户对 SkillSet 内所有 Skill 的 MCP 依赖权限。"""
    try:
        return svc.check_skill_set_permission(user_id=ctx.user_id, skill_set_id=skill_set_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("[skill_auth] check_skill_set_permission failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/set/permission/apply", response_model=ApplySkillSetPermissionResponse)
async def apply_skill_set_permission(
    request: ApplySkillSetPermissionRequest,
    ctx: RequestContext = Depends(get_request_context),
    svc: SkillAuthServiceProtocol = Injected(SkillAuthServiceProtocol),
) -> ApplySkillSetPermissionResponse:
    """为用户批量申请 SkillSet 内所有 MCP 依赖的权限。"""
    try:
        return svc.apply_skill_set_permission(
            user_id=ctx.user_id,
            skill_set_id=request.skill_set_id,
            reason=request.reason,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("[skill_auth] apply_skill_set_permission failed")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Bot 维度 ====================


@router.get("/bot/permission", response_model=BotPermissionResponse)
async def check_bot_permission(
    bot_id: str = Query(..., description="Bot ID (对应 ac_skill_set.bolt_id)"),
    ctx: RequestContext = Depends(get_request_context),
    svc: SkillAuthServiceProtocol = Injected(SkillAuthServiceProtocol),
) -> BotPermissionResponse:
    """查询用户对 Bot 所有 MCP 依赖的权限状态。"""
    try:
        return svc.check_bot_permission(user_id=ctx.user_id, bot_id=bot_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("[skill_auth] check_bot_permission failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/bot/permission/apply", response_model=ApplyBotPermissionResponse)
async def apply_bot_permission(
    request: ApplyBotPermissionRequest,
    ctx: RequestContext = Depends(get_request_context),
    svc: SkillAuthServiceProtocol = Injected(SkillAuthServiceProtocol),
) -> ApplyBotPermissionResponse:
    """为用户批量申请 Bot 所有 MCP 依赖的权限。"""
    try:
        return svc.apply_bot_permission(
            user_id=ctx.user_id,
            bot_id=request.bot_id,
            reason=request.reason,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("[skill_auth] apply_bot_permission failed")
        raise HTTPException(status_code=500, detail=str(e))
