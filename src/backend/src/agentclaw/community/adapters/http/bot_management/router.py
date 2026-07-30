"""
Bot management router.

Provides CRUD operations for bots:
- POST /api/bots - Create a new bot
- GET /api/bots/{bot_id} - Get bot details
- GET /api/bots - List bots
- PUT /api/bots/{bot_id} - Update bot
- DELETE /api/bots/{bot_id} - Delete bot

Each bot is associated with an entity (staff, proj, team) and has its own device.
"""
import asyncio
import json
from typing import Any, List, Literal, Optional

from fastapi import APIRouter, Query, Request, Response, Depends, Path
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from agentclaw.community.adapters.http.auth.dependencies import require_operator, get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.core.access.admin_scopes import super_admin
from agentclaw.community.adapters.http.dependencies import RequestContext, get_request_context
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.api.create_bot_for_others_service import (
    CreateBotForOthersServiceProtocol,
)
from agentclaw.community.api.data_init_service import DataInitServiceProtocol
from agentclaw.community.api.default_bot_passport_repair_service import (
    DefaultBotPassportRepairServiceProtocol,
)
from agentclaw.community.api.policy_service import PolicyServiceProtocol
from agentclaw.community.core.bot_collaborator.interceptor import (
    CollaboratorPermissionInterceptor,
    with_interceptors,
)
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.bot_management.services.bot_service import (
    BotServiceError,
    BotInvalidLifecycleStateError,
    BotNotFoundError,
    BotPermissionError,
    DeviceAllocationError,
    BotNameExistsError,
    BotNameInvalidError,
    BotLimitExceededError,
    DeviceLimitError,
    generate_bot_id,
    validate_bot_name,
)
from agentclaw.community.core.bot_management.errors import (
    CreateBotForOthersError,
    DefaultBotPassportRepairError,
)
from agentclaw.community.core.bot_management.utils import (
    clear_baas_publish_failure_ext as _clear_baas_publish_failure_ext,
    is_baas_publish_failure_message as _utils_is_baas_publish_failure_message,
)
from agentclaw.community.core.bot_management.readiness import (
    has_stale_baas_publish_failure,
    is_bot_ready,
)
from agentclaw.community.core.bot_management.services.engine_resolver import resolve_engine_for_bot
from agentclaw.community.core.bot_management.create_flow import (
    AuthPending,
    AuthStatus,
    BotCreateSpec,
    complete_bot_authorization,
    create_bot_with_authorization,
)
# Re-exported so ``test_bot_passport`` can keep importing it from this module.
from agentclaw.community.core.bot_management.create_flow import (  # noqa: F401
    _get_bot_mcp_codes,
)
from agentclaw.community.api.engine_config_service import EngineConfigServiceProtocol
from agentclaw.community.core.skill_center.factories import SkillSetServiceFactory
from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE, _get_engine_types
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.auth import AuthPlugin
from agentclaw.community.plugin_api.auth_relationship import AuthRelationshipPlugin
from agentclaw.community.plugin_api.passport import PassportPlugin
from agentclaw.community.plugin_api.passport import PassportError

logger = get_logger()


def _is_baas_publish_failure_message(message: object) -> bool:
    return _utils_is_baas_publish_failure_message(message)


def _sanitize_baas_status_ext_for_response(
    *,
    bot_status: str,
    binding_status: str,
    device_provider: str,
    ext: dict,
) -> dict:
    if (
        bot_status == "ACTIVE"
        and binding_status == "ACTIVE"
        and device_provider == "baas"
    ):
        return _clear_baas_publish_failure_ext(ext)
    return dict(ext)


def _bot_create_spec(data: dict[str, Any], user_id: str) -> BotCreateSpec:
    """Map this surface's raw JSON body onto the shared create contract.

    The one place the internal API's request keys are read; the shared flow then
    works off typed fields, so a spec field added later must be filled in here
    explicitly rather than silently going missing. ``entity_id`` and
    ``engine_type`` are resolved to concrete values here — the same defaults
    ``create_bot`` would otherwise apply.
    """
    return BotCreateSpec(
        entity_id=data.get("entity_id") or user_id,
        entity_type=data.get("entity_type") or "staff",
        engine_type=data.get("engine_type") or DEFAULT_ENGINE_TYPE,
        bot_name=data.get("bot_name"),
        bot_desc=data.get("bot_desc"),
        bot_type=data.get("bot_type") or "personal",
        avatar_url=data.get("avatar_url"),
        share_policy=data.get("share_policy"),
        template_type=data.get("template_type"),
        template_config=data.get("template_config"),
    )


router = APIRouter(prefix="/api/bots", tags=["bots"])


# ==================== Response Models ====================

class ApiResponse(BaseModel):
    """Unified API response format."""
    success: bool
    message: str = "OK"
    error_code: int = 200
    data: Optional[Any] = None


class BotListData(BaseModel):
    """Bot list data."""
    total: int
    items: list


# ==================== API Endpoints ====================


class CreateBotForOthersRequest(BaseModel):
    """Request model for creating bot for others."""
    target_user_id: str
    target_nick_name: str


class ReleaseBotForOthersRequest(BaseModel):
    """Request model for releasing bot for others."""
    target_user_id: str
    target_bot_id: str


class RestartBotForOthersRequest(BaseModel):
    """Request model for restarting bot for others."""
    target_user_id: str
    target_bot_id: str


class UpdateBotExtForOthersRequest(BaseModel):
    """Request model for updating bot ext for others."""
    target_user_id: str
    target_bot_id: str
    ext_update: dict


class RepairDefaultPassportForOthersRequest(BaseModel):
    """Strict operations request; the bot ID is intentionally fixed in core."""

    model_config = ConfigDict(extra="forbid")

    target_user_id: str
    target_env: Literal["pre", "prod"]

    @field_validator("target_user_id")
    @classmethod
    def validate_target_user_id(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("target_user_id must not be blank")
        return trimmed


@router.post("/release-for-others", response_model=ApiResponse)
async def release_bot_for_others(
    request: Request,
    ctx: RequestContext = Depends(get_request_context),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> ApiResponse:
    """
    Release a bot for another user (admin only).

    POST /api/bots/release-for-others
    Body: {
        "target_user_id": "100000",
        "target_bot_id": "default"
    }

    Only specific staff IDs can call this interface.
    - If target has no such bot: return error
    - If bot is already FAILED: skip
    - Otherwise: release device and update bot status to FAILED
    """
    try:
        data = await request.json()
        target_user_id = data.get("target_user_id")
        target_bot_id = data.get("target_bot_id")

        # Get current user from authentication context
        caller_user_id = ctx.user_id
        caller_nick_name = ctx.nick_name or caller_user_id

        logger.info(f"[bot_router.release_bot_for_others] Caller: {caller_user_id}, "
                   f"Target: {target_user_id}")

        # Permission check: only allowed staff IDs can call this interface
        if caller_user_id not in super_admin():
            logger.warning(f"[bot_router.release_bot_for_others] Permission denied for user: {caller_user_id}")
            return ApiResponse(
                success=False,
                message="权限不足：您没有权限调用此接口",
                error_code=403,
                data=None,
            )

        # Validate required fields
        if not target_user_id or not target_user_id.strip():
            return ApiResponse(
                success=False,
                message="缺少必填参数: target_user_id",
                error_code=400,
                data=None,
            )
        if not target_bot_id or not target_bot_id.strip():
            return ApiResponse(
                success=False,
                message="缺少必填参数: target_bot_id",
                error_code=400,
                data=None,
            )

        target_user_id = target_user_id.strip()
        target_bot_id = target_bot_id.strip()

        # Call service to release bot
        result = bot_service.release_bot_for_others(
            target_user_id=target_user_id,
            target_bot_id=target_bot_id,
            caller_user_id=caller_user_id,
            caller_nick_name=caller_nick_name,
        )

        return ApiResponse(
            success=True,
            message=result.get("message", "Bot释放成功"),
            data=result,
        )

    except BotNotFoundError as e:
        logger.warning(f"[bot_router.release_bot_for_others] Bot not found: {e}")
        return ApiResponse(
            success=False,
            message=f"{str(e)}",
            error_code=404,
            data=None,
        )
    except PassportError as e:
        logger.error(f"[bot_router.release_bot_for_others] TCAuth error: {e}")
        return ApiResponse(
            success=False,
            message=f"销毁授权凭证失败: {str(e)}",
            error_code=500,
            data=None,
        )
    except BotServiceError as e:
        logger.error(f"[bot_router.release_bot_for_others] Bot service error: {e}")
        return ApiResponse(
            success=False,
            message=f"释放Bot失败: {str(e)}",
            error_code=500,
            data=None,
        )
    except Exception as e:
        logger.error(f"[bot_router.release_bot_for_others] Unexpected error: {e}")
        return ApiResponse(
            success=False,
            message=f"释放Bot失败: {str(e)}",
            error_code=500,
            data=None,
        )


@router.post("/restart-for-others", response_model=ApiResponse)
async def restart_bot_for_others(
    request: Request,
    response: Response,
    ctx: RequestContext = Depends(get_request_context),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> ApiResponse:
    """
    Restart a bot for another user (admin only).

    POST /api/bots/restart-for-others
    Body: {
        "target_user_id": "100000",
        "target_bot_id": "default"
    }

    Only specific staff IDs can call this interface.
    This will:
    1. Release the current device bound to the bot
    2. Reset bot status to PENDING
    3. Trigger async device allocation

    Returns updated bot record with PENDING status.
    Use GET /api/bots/{bot_id}/status to poll for device allocation completion.
    """
    try:
        data = await request.json()
        target_user_id = data.get("target_user_id")
        target_bot_id = data.get("target_bot_id")

        # Get current user from authentication context
        caller_user_id = ctx.user_id
        caller_nick_name = data.get("caller_nick_name") or ctx.nick_name or caller_user_id

        logger.info(f"[bot_router.restart_bot_for_others] Caller: {caller_user_id}({caller_nick_name}), "
                   f"Target: {target_user_id}, Bot: {target_bot_id}")

        # Permission check: only allowed staff IDs can call this interface
        if caller_user_id not in super_admin():
            logger.warning(f"[bot_router.restart_bot_for_others] Permission denied for user: {caller_user_id}")
            return ApiResponse(
                success=False,
                message="权限不足：您没有权限调用此接口",
                error_code=403,
                data=None,
            )

        # Validate required fields
        if not target_user_id or not target_user_id.strip():
            return ApiResponse(
                success=False,
                message="缺少必填参数: target_user_id",
                error_code=400,
                data=None,
            )
        if not target_bot_id or not target_bot_id.strip():
            return ApiResponse(
                success=False,
                message="缺少必填参数: target_bot_id",
                error_code=400,
                data=None,
            )

        target_user_id = target_user_id.strip()
        target_bot_id = target_bot_id.strip()

        # Call service to restart bot
        result = bot_service.restart_bot(
            bot_id=target_bot_id,
            user_id=target_user_id,
            nick_name=target_user_id,  # Use user_id as nick_name for admin operations
        )

        if result.get("restart_in_progress"):
            response.status_code = 202

        logger.info(f"[bot_router.restart_bot_for_others] Successfully restarted bot {target_bot_id} for {target_user_id}")

        return ApiResponse(
            success=True,
            message=f"成功为目标用户重启 Bot: {target_bot_id}",
            data={
                "bot": result,
                "target_user_id": target_user_id,
                "target_bot_id": target_bot_id,
            },
        )

    except BotNotFoundError as e:
        logger.warning(f"[bot_router.restart_bot_for_others] Bot not found: {e}")
        return ApiResponse(
            success=False,
            message=f"{str(e)}",
            error_code=404,
            data=None,
        )
    except BotInvalidLifecycleStateError as e:
        logger.warning(
            f"[bot_router.restart_bot_for_others] Invalid lifecycle state: {e}"
        )
        response.status_code = 409
        return ApiResponse(
            success=False,
            message=f"当前状态不允许重启Bot: {str(e)}",
            error_code=409,
            data=None,
        )
    except BotServiceError as e:
        logger.error(f"[bot_router.restart_bot_for_others] Bot service error: {e}")
        return ApiResponse(
            success=False,
            message=f"重启Bot失败: {str(e)}",
            error_code=500,
            data=None,
        )
    except Exception as e:
        logger.error(f"[bot_router.restart_bot_for_others] Unexpected error: {e}")
        return ApiResponse(
            success=False,
            message=f"重启Bot失败: {str(e)}",
            error_code=500,
            data=None,
        )


@router.post("/restart-scheduler", response_model=ApiResponse)
async def restart_scheduler(
    request: Request,
    response: Response,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> ApiResponse:
    """
    Restart a bot by scheduler task

    POST /api/bots/restart-scheduler
    Body: {
        "target_user_id": "100000",
        "target_bot_id": "default"
    }
    """
    try:
        data = await request.json()
        target_user_id = data.get("user_id")
        target_bot_id = data.get("bot_id")

        # Call service to restart bot
        result = bot_service.restart_bot(
            bot_id=target_bot_id,
            user_id=target_user_id,
            nick_name=target_user_id,  # Use user_id as nick_name for admin operations
        )

        if result.get("restart_in_progress"):
            response.status_code = 202

        logger.info(f"[bot_router.restart_scheduler] Successfully restarted bot {target_bot_id} for {target_user_id}")

        return ApiResponse(
            success=True,
            message=f"成功为目标用户重启 Bot: {target_bot_id}",
            data={
                "bot": result,
                "target_user_id": target_user_id,
                "target_bot_id": target_bot_id,
            },
        )

    except BotNotFoundError as e:
        logger.warning(f"[bot_router.restart_scheduler] Bot not found: {e}")
        return ApiResponse(
            success=False,
            message=f"{str(e)}",
            error_code=404,
            data=None,
        )
    except BotInvalidLifecycleStateError as e:
        logger.warning(
            f"[bot_router.restart_scheduler] Invalid lifecycle state: {e}"
        )
        response.status_code = 409
        return ApiResponse(
            success=False,
            message=f"当前状态不允许重启Bot: {str(e)}",
            error_code=409,
            data=None,
        )
    except BotServiceError as e:
        logger.error(f"[bot_router.restart_scheduler] Bot service error: {e}")
        return ApiResponse(
            success=False,
            message=f"重启Bot失败: {str(e)}",
            error_code=500,
            data=None,
        )
    except Exception as e:
        logger.error(f"[bot_router.restart_scheduler] Unexpected error: {e}")
        return ApiResponse(
            success=False,
            message=f"重启Bot失败: {str(e)}",
            error_code=500,
            data=None,
        )


@router.post(
    "/repair-default-passport-for-others",
    response_model=ApiResponse,
)
async def repair_default_passport_for_others(
    request: Request,
    ctx: RequestContext = Depends(get_request_context),
    repair_service: DefaultBotPassportRepairServiceProtocol = Injected(
        DefaultBotPassportRepairServiceProtocol
    ),
) -> ApiResponse:
    """Repair and verify control-plane identity for one default bot."""
    if ctx.user_id not in super_admin():
        return ApiResponse(
            success=False,
            message="权限不足：您没有权限调用此接口",
            error_code=403,
            data=None,
        )

    try:
        payload = RepairDefaultPassportForOthersRequest.model_validate(
            await request.json()
        )
    except (ValidationError, ValueError, TypeError):
        return ApiResponse(
            success=False,
            message="参数错误：target_user_id 必填，target_env 仅支持 pre 或 prod",
            error_code=400,
            data=None,
        )

    request_id = request.headers.get("X-Request-ID")
    trace_id = getattr(request.state, "trace_id", None)
    logger.info(
        "[bot_router.repair_default_passport_for_others] start "
        "request_id=%s trace_id=%s operator=%s target=%s env=%s bot_id=default",
        request_id,
        trace_id,
        ctx.user_id,
        payload.target_user_id,
        payload.target_env,
    )
    try:
        result = repair_service.repair(
            target_user_id=payload.target_user_id,
            target_env=payload.target_env,
            operator_user_id=ctx.user_id,
            operator_name=ctx.nick_name or ctx.user_id,
        )
        logger.info(
            "[bot_router.repair_default_passport_for_others] complete "
            "request_id=%s trace_id=%s operator=%s target=%s env=%s "
            "bot_id=default action=%s passport_source=%s "
            "owner_relationship_verified=%s ext_agent_code_verified=%s",
            request_id,
            trace_id,
            ctx.user_id,
            payload.target_user_id,
            payload.target_env,
            result.get("action"),
            (result.get("passport") or {}).get("source"),
            (result.get("owner_relationship") or {}).get("verified"),
            (result.get("database") or {}).get("ext_agent_code_verified"),
        )
        return ApiResponse(
            success=True,
            message="default bot Passport 修复并校验成功，需在目标环境重启",
            data=result,
        )
    except DefaultBotPassportRepairError as exc:
        logger.warning(
            "[bot_router.repair_default_passport_for_others] request_id=%s "
            "trace_id=%s operator=%s target=%s env=%s error_code=%s error=%s",
            request_id,
            trace_id,
            ctx.user_id,
            payload.target_user_id,
            payload.target_env,
            exc.error_code,
            exc,
        )
        return ApiResponse(
            success=False,
            message=str(exc),
            error_code=exc.error_code,
            data=None,
        )
    except Exception:
        logger.exception(
            "[bot_router.repair_default_passport_for_others] unexpected failure: "
            "request_id=%s trace_id=%s operator=%s target=%s env=%s",
            request_id,
            trace_id,
            ctx.user_id,
            payload.target_user_id,
            payload.target_env,
        )
        return ApiResponse(
            success=False,
            message="default bot Passport 修复失败",
            error_code=500,
            data=None,
        )


@router.post("/create-for-others", response_model=ApiResponse)
async def create_bot_for_others(
    request: Request,
    ctx: RequestContext = Depends(get_request_context),
    create_service: CreateBotForOthersServiceProtocol = Injected(
        CreateBotForOthersServiceProtocol
    ),
) -> ApiResponse:
    """
    Create a bot for another user (admin only).

    POST /api/bots/create-for-others
    Body: {
        "target_user_id": "100000",
        "target_nick_name": "张三"
    }

    Only specific staff IDs can call this interface.
    If target already has a default bot:
    - status is ACTIVE: skip creation
    - status is not ACTIVE: restart the bot
    """
    try:
        data = await request.json()
        target_user_id = data.get("target_user_id")
        target_nick_name = data.get("target_nick_name")
        bot_type = data.get("bot_type")

        # Get current user from authentication context
        caller_user_id = ctx.user_id

        logger.info(f"[bot_router.create_bot_for_others] Caller: {caller_user_id}, "
                   f"Target: {target_user_id}, NickName: {target_nick_name}")

        # Permission check: only allowed staff IDs can call this interface
        if caller_user_id not in super_admin():
            logger.warning(f"[bot_router.create_bot_for_others] Permission denied for user: {caller_user_id}")
            return ApiResponse(
                success=False,
                message="权限不足：您没有权限调用此接口",
                error_code=403,
                data=None,
            )

        # Validate required fields
        if not target_user_id or not target_user_id.strip():
            return ApiResponse(
                success=False,
                message="缺少必填参数: target_user_id",
                error_code=400,
                data=None,
            )
        if not target_nick_name or not target_nick_name.strip():
            return ApiResponse(
                success=False,
                message="缺少必填参数: target_nick_name",
                error_code=400,
                data=None,
            )

        target_user_id = target_user_id.strip()
        target_nick_name = target_nick_name.strip()

        cookie = request.headers.get("cookie", "")
        result = create_service.execute(
            target_user_id=target_user_id,
            target_nick_name=target_nick_name,
            bot_type=bot_type,
            operator_user_id=caller_user_id,
            operator_name=ctx.nick_name or caller_user_id,
            cookie=cookie,
        )
        action = result.get("action")
        if action == "created":
            message = "成功为目标用户创建default bot"
        elif action == "restarted":
            message = (
                f"目标用户已有default bot（状态: {result.get('status')}），已触发重启"
            )
        elif action == "skipped_wait":
            message = (
                f"目标用户default bot（状态: {result.get('status')}）修改时间不足30分钟，"
                f"跳过重启，还需约{result.get('minutes_remaining')}分钟"
            )
        elif action == "repaired":
            message = "目标用户已有活跃的default bot，已补齐Passport，请安排重启"
        else:
            message = "目标用户已有活跃的default bot，Passport已校验，跳过创建"

        logger.info(
            "[bot_router.create_bot_for_others] complete operator=%s target=%s "
            "bot_id=default action=%s passport_source=%s restart_required=%s",
            caller_user_id,
            target_user_id,
            action,
            (result.get("passport") or {}).get("source"),
            (result.get("runtime") or {}).get("restart_required"),
        )

        return ApiResponse(
            success=True,
            message=message,
            data=result,
        )

    except CreateBotForOthersError as e:
        logger.warning(
            "[bot_router.create_bot_for_others] control-plane preparation failed: "
            "error_code=%s error=%s",
            e.error_code,
            e,
        )
        return ApiResponse(
            success=False,
            message=str(e),
            error_code=e.error_code,
            data=None,
        )
    except BotNameExistsError as e:
        logger.warning(f"[bot_router.create_bot_for_others] Bot name exists: {e}")
        return ApiResponse(
            success=False,
            message=f"{str(e)}",
            error_code=409,
            data=None,
        )
    except (BotLimitExceededError, DeviceLimitError) as e:
        logger.warning(f"[bot_router.create_bot_for_others] Device limit reached: {e}")
        return ApiResponse(
            success=False,
            message=f"{str(e)}",
            error_code=429,
            data=None,
        )
    except DeviceAllocationError as e:
        logger.error(f"[bot_router.create_bot_for_others] Device allocation error: {e}")
        return ApiResponse(
            success=False,
            message=f"设备分配失败: {str(e)}",
            error_code=500,
            data=None,
        )
    except BotServiceError as e:
        logger.error(f"[bot_router.create_bot_for_others] Bot service error: {e}")
        return ApiResponse(
            success=False,
            message=f"创建Bot失败: {str(e)}",
            error_code=500,
            data=None,
        )
    except Exception as e:
        logger.error(f"[bot_router.create_bot_for_others] Unexpected error: {e}")
        return ApiResponse(
            success=False,
            message=f"创建Bot失败: {str(e)}",
            error_code=500,
            data=None,
        )


@router.post("/update-bot-ext-for-others", response_model=ApiResponse)
async def update_bot_ext_for_others(
    request: Request,
    ctx: RequestContext = Depends(get_request_context),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> ApiResponse:
    """
    Update bot ext for another user (admin only).

    POST /api/bots/update-bot-ext-for-others
    Body: {
        "target_user_id": "100000",
        "target_bot_id": "default",
        "ext_update": {"key1": "value1", "key2": "value2"}
    }

    Only specific staff IDs can call this interface.
    Merges the provided fields into the existing ext JSON.
    """
    try:
        data = await request.json()
        target_user_id = data.get("target_user_id")
        target_bot_id = data.get("target_bot_id")
        ext_update = data.get("ext_update")

        # Get current user from authentication context
        caller_user_id = ctx.user_id
        caller_nick_name = ctx.nick_name or caller_user_id

        logger.info(f"[bot_router.update_bot_ext_for_others] Caller: {caller_user_id}({caller_nick_name}), "
                   f"Target: {target_user_id}, Bot: {target_bot_id}")

        # Permission check: only allowed staff IDs can call this interface
        if caller_user_id not in super_admin():
            logger.warning(f"[bot_router.update_bot_ext_for_others] Permission denied for user: {caller_user_id}")
            return ApiResponse(
                success=False,
                message="权限不足：您没有权限调用此接口",
                error_code=403,
                data=None,
            )

        # Validate required fields
        if not target_user_id or not target_user_id.strip():
            return ApiResponse(
                success=False,
                message="target_user_id 不能为空",
                error_code=400,
                data=None,
            )
        if not target_bot_id or not target_bot_id.strip():
            return ApiResponse(
                success=False,
                message="target_bot_id 不能为空",
                error_code=400,
                data=None,
            )
        if not ext_update or not isinstance(ext_update, dict):
            return ApiResponse(
                success=False,
                message="ext_update 必须是非空 JSON 对象",
                error_code=400,
                data=None,
            )

        target_user_id = target_user_id.strip()
        target_bot_id = target_bot_id.strip()

        # Call service to update bot ext
        bot_service.update_bot_ext(target_bot_id, target_user_id, ext_update)

        logger.info(f"[bot_router.update_bot_ext_for_others] Successfully updated ext for bot {target_bot_id} "
                   f"of user {target_user_id}")

        return ApiResponse(
            success=True,
            message=f"成功更新 Bot ext: {target_bot_id}",
            data={
                "target_user_id": target_user_id,
                "target_bot_id": target_bot_id,
            },
        )

    except BotNotFoundError:
        return ApiResponse(
            success=False,
            message=f"Bot不存在: {target_bot_id}",
            error_code=404,
            data=None,
        )
    except BotPermissionError as e:
        return ApiResponse(
            success=False,
            message=str(e),
            error_code=403,
            data=None,
        )
    except Exception as e:
        logger.error(f"[bot_router.update_bot_ext_for_others] Unexpected error: {e}")
        return ApiResponse(
            success=False,
            message=f"更新Bot ext失败: {str(e)}",
            error_code=500,
            data=None,
        )


@router.post("", response_model=ApiResponse)
async def create_bot(
    request: Request,
    ctx: RequestContext = Depends(get_request_context),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    bot_repo: BotRepository = Injected(BotRepository),
    passport_plugin: PassportPlugin = Injected(PassportPlugin),
    auth_rel_plugin: AuthRelationshipPlugin = Injected(AuthRelationshipPlugin),
    skill_set_factory: SkillSetServiceFactory = Injected(SkillSetServiceFactory),
) -> ApiResponse:
    """
    Create a new bot with Passport authorization flow.

    POST /api/bots
    Body: {
        "bot_name": "My Bot",
        "bot_desc": "A helpful assistant",
        "entity_id": "xxx",        // optional, defaults to {user_id}
        "entity_type": "staff",    // optional, defaults to "staff"
        "share_policy": {...},     // optional
        "engine_type": "openclaw"  // optional, active engine type, defaults to config default
        "avatar_url": "https://example.com/avatar.png"  // optional, bot avatar URL
        "bot_type": "personal"     // optional, "personal" or "service", defaults to "personal"
    }

    Flow:
    1. Allocate botId (using generate_bot_id)
    2. Apply Passport (first bot: apply_first_agent_passport, non-first: apply_agent_passport)
    3. If token not empty -> continue device allocation
    4. If token is empty -> return iframe_url + bot_id (frontend guides authorization)

    User ID is obtained from Buservice authentication (not from request body for security).
    """
    try:
        data = await request.json()
        user_id = ctx.user_id
        nick_name = ctx.nick_name or user_id

        # botId allocation stays in the router (callers own id allocation and the
        # tests patch generate_bot_id here). The shared flow does the rest:
        # name validation → preflight → passport → create.
        bot_id = generate_bot_id(user_id, bot_repo)
        cookie = request.headers.get("cookie", "")

        outcome = create_bot_with_authorization(
            user_id=user_id,
            nick_name=nick_name,
            bot_id=bot_id,
            spec=_bot_create_spec(data, user_id),
            cookie=cookie,
            bot_service=bot_service,
            passport_plugin=passport_plugin,
            auth_rel_plugin=auth_rel_plugin,
            skill_set_factory=skill_set_factory,
        )

        # Passport not yet issued → guide the user through authorization.
        if isinstance(outcome, AuthPending):
            logger.info(
                f"[bot_router.create_bot] Need authorization: bot_id={outcome.bot_id}, "
                f"iframe_url={outcome.iframe_url}"
            )
            return ApiResponse(
                success=False,
                message="需要授权",
                error_code=401,
                data={
                    "need_authorization": True,
                    "bot_id": outcome.bot_id,
                    "iframe_url": outcome.iframe_url,
                    "redirect_url": outcome.redirect_url,
                },
            )

        return ApiResponse(
            success=True,
            data={
                "bot": outcome.bot,
                "passport": {
                    "token": outcome.passport_token,
                    "status": "ISSUED",
                    "is_first_bot": outcome.is_first_bot,
                },
            },
        )

    except BotNameInvalidError as e:
        logger.warning(f"[bot_router.create_bot] Invalid bot_name: {e}")
        return ApiResponse(
            success=False,
            message=f"{str(e)}",
            error_code=400,
            data=None,
        )
    except BotNameExistsError as e:
        logger.warning(f"[bot_router.create_bot] Bot name exists: {e}")
        return ApiResponse(
            success=False,
            message=f"{str(e)}",
            error_code=409,
            data=None,
        )
    except BotLimitExceededError as e:
        logger.warning(f"[bot_router.create_bot] Bot limit exceeded: {e}")
        return ApiResponse(
            success=False,
            message=f"{str(e)}",
            error_code=429,
            data=None,
        )
    except DeviceLimitError as e:
        logger.warning(f"[bot_router.create_bot] Device limit reached: {e}")
        return ApiResponse(
            success=False,
            message=f"{str(e)}",
            error_code=429,
            data=None,
        )
    except PassportError as e:
        # Preserves the internal contract: a Passport apply failure is 5400
        # (the old inner try mapped it here, not via the generic branch).
        logger.error(f"[bot_router.create_bot] TCAuth error: {e}")
        return ApiResponse(
            success=False,
            message=f"授权申请异常: {e}",
            error_code=5400,
            data=None,
        )
    except DeviceAllocationError as e:
        logger.error(f"[bot_router.create_bot] Device allocation error: {e}")
        return ApiResponse(
            success=False,
            message=f"设备分配失败: {str(e)}",
            error_code=500,
            data=None,
        )
    except BotServiceError as e:
        logger.error(f"[bot_router.create_bot] Bot service error: {e}")
        return ApiResponse(
            success=False,
            message=f"创建Bot失败: {str(e)}",
            error_code=500,
            data=None,
        )
    except Exception as e:
        logger.error(f"[bot_router.create_bot] Unexpected error: {e}")
        return ApiResponse(
            success=False,
            message=f"创建Bot失败: {str(e)}",
            error_code=501,
            data=None,
        )


@router.post("/auth-status", response_model=ApiResponse)
async def get_auth_status(
    request: Request,
    ctx: RequestContext = Depends(get_request_context),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    passport_plugin: PassportPlugin = Injected(PassportPlugin),
    auth_rel_plugin: AuthRelationshipPlugin = Injected(AuthRelationshipPlugin),
) -> ApiResponse:
    """
    轮询授权状态（非首Bot创建时使用）。

    POST /api/bots/auth-status
    Body: {
        "bot_id": "xxx",
        "bot_name": "My Bot",
        "bot_desc": "A helpful assistant",
        "entity_id": "xxx",        // optional, defaults to {user_id}
        "entity_type": "staff",    // optional, defaults to "staff"
        "share_policy": {...},     // optional
        "engine_type": "openclaw", // optional
        "avatar_url": "https://example.com/avatar.png"  // optional
    }

    流程：
    1. 调用 tcauth_client.query_auth_status 获取状态
    2. PENDING → 返回"处理中"
    3. ISSUED → 调用 bot_service.create_bot 设备分配 → 返回成功

    Returns:
        {
            "success": true,
            "data": {
                "status": "PENDING | ISSUED",
                "bot": {...}  // 仅 ISSUED 时返回
            }
        }
    """
    try:
        data = await request.json()
        user_id = ctx.user_id
        nick_name = ctx.nick_name or user_id

        bot_id = data.get("bot_id")
        if not bot_id:
            return ApiResponse(
                success=False,
                message="bot_id 是必填参数",
                error_code=400,
                data=None,
            )

        cookie = request.headers.get("cookie", "")
        result = complete_bot_authorization(
            user_id=user_id,
            nick_name=nick_name,
            bot_id=bot_id,
            spec=_bot_create_spec(data, user_id),
            cookie=cookie,
            bot_service=bot_service,
            passport_plugin=passport_plugin,
            auth_rel_plugin=auth_rel_plugin,
        )

        if result.status == AuthStatus.PENDING:
            return ApiResponse(
                success=True,
                data={"status": "PENDING", "message": "授权处理中"},
            )
        if result.status == AuthStatus.ISSUED:
            return ApiResponse(
                success=True,
                data={"status": "ISSUED", "bot": result.bot},
            )
        # 其他状态（如 REJECTED）
        return ApiResponse(
            success=False,
            message=f"授权状态异常: {result.status}",
            error_code=400,
            data={"status": result.status},
        )

    except BotNameInvalidError as e:
        logger.warning(f"[bot_router.get_auth_status] Invalid bot_name: {e}")
        return ApiResponse(
            success=False,
            message=str(e),
            error_code=400,
            data=None,
        )
    except BotLimitExceededError as e:
        logger.warning(f"[bot_router.get_auth_status] Bot limit exceeded: {e}")
        return ApiResponse(
            success=False,
            message=str(e),
            error_code=429,
            data=None,
        )
    except PassportError as e:
        logger.error(f"[bot_router.get_auth_status] TCAuth error: {e}")
        return ApiResponse(
            success=False,
            message=f"授权状态查询异常: {e}",
            error_code=5400,
            data=None,
        )
    except Exception as e:
        logger.error(f"[bot_router.get_auth_status] Error: {e}")
        return ApiResponse(
            success=False,
            message=f"查询授权状态失败: {str(e)}",
            error_code=500,
            data=None,
        )


@router.get("/{bot_id}/passport", response_model=ApiResponse)
@with_interceptors(
    CollaboratorPermissionInterceptor(
        bot_id="$bot_id",
        owner_id="$owner_id",
        persist_audit_log=False,  # 只读操作，不需要审计和锁检查
    )
)
async def get_bot_passport(
    bot_id: str = Path(..., description="Bot ID"),
    owner_id: Optional[str] = Query(None, description="Bot owner workno; defaults to current user"),
    ctx: RequestContext = Depends(get_request_context),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    passport_plugin: PassportPlugin = Injected(PassportPlugin),
) -> ApiResponse:
    """查询 Bot 的 Agent Passport 信息（tcauthmng）

    GET /api/bots/{bot_id}/passport
    """
    try:
        operator_id = ctx.user_id
        resolved_owner_id = owner_id or operator_id

        bot = bot_service.get_bot(bot_id, resolved_owner_id)
        if not bot:
            return ApiResponse(success=False, message="Bot not found", error_code=404)

        passport = passport_plugin.query_agent_passport(
            bot_id=bot_id,
            owner_workno=resolved_owner_id,
        )

        if passport:
            return ApiResponse(success=True, data=passport)
        else:
            return ApiResponse(success=False, message="Passport not found", error_code=404)

    except PassportError as e:
        logger.error(f"[bot_router.get_bot_passport] TCAuth error: {e}")
        return ApiResponse(success=False, message=f"授权信息查询异常: {e}", error_code=5400)
    except Exception as e:
        logger.error(f"[bot_router.get_bot_passport] Error: {e}")
        return ApiResponse(success=False, message=f"查询失败: {e}", error_code=500)


@router.post("/passport/refresh-token", response_model=ApiResponse)
async def refresh_bot_passport_token(
    request: Request,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> ApiResponse:
    """刷新 Bot 的 Passport Token 并热更新到运行中的设备（供 passport 提供方回调）。

    POST /api/bots/passport/refresh-token
    Body: {
        "bot_id": "default",           // Bot ID
        "owner_workno": "100000",     // bot 所有者工号
        "token": "xxx"                // 必填，调用方传入的最新 token
    }
    """
    try:
        data = await request.json()
        bot_id = data.get("bot_id")
        owner_workno = data.get("owner_workno")
        token = data.get("token")

        if not bot_id:
            return ApiResponse(
                success=False,
                message="缺少必填参数: bot_id",
                error_code=400,
                data=None,
            )
        if not owner_workno:
            return ApiResponse(
                success=False,
                message="缺少必填参数: owner_workno",
                error_code=400,
                data=None,
            )
        if not token or not isinstance(token, str):
            return ApiResponse(
                success=False,
                message="缺少必填参数: token（必须为字符串）",
                error_code=400,
                data=None,
            )

        logger.info(f"[bot_router.refresh_bot_passport_token] Refreshing passport token for bot_id={bot_id}, owner_workno={owner_workno}")
        result = bot_service.hot_update_passport_token_to_device(bot_id=bot_id, user_id=owner_workno, token=token)

        return ApiResponse(
            success=True,
            message="Passport Token 刷新成功",
            data=result,
        )

    except BotNotFoundError:
        return ApiResponse(
            success=False,
            message=f"Bot不存在: {bot_id}",
            error_code=404,
            data=None,
        )
    except BotServiceError as e:
        logger.error(f"[bot_router.refresh_bot_passport_token] Bot service error: {e}")
        return ApiResponse(
            success=False,
            message=str(e),
            error_code=500,
            data=None,
        )
    except PassportError as e:
        logger.error(f"[bot_router.refresh_bot_passport_token] TCAuth error: {e}")
        return ApiResponse(
            success=False,
            message=f"授权查询异常: {e}",
            error_code=5400,
            data=None,
        )
    except Exception as e:
        logger.error(f"[bot_router.refresh_bot_passport_token] Unexpected error: {e}")
        return ApiResponse(
            success=False,
            message=f"刷新 Passport Token 失败: {str(e)}",
            error_code=500,
            data=None,
        )


def _build_bots_by_owner_or_collaborator_data(
    owner_id: str,
    bot_service: BotServiceProtocol,
) -> dict[str, Any]:
    """Build list response data synchronously outside the event loop."""
    engine_types = _get_engine_types()
    result = bot_service.list_bots_by_owner_or_collaborator(
        owner_id=owner_id,
        page=1,
        page_size=100,
    )
    items = result["items"]

    for bot in items:
        entity_id = bot.get("entity_id")
        entity_type = bot.get("entity_type", "staff")
        bot_id = str(bot.get("bot_id"))
        bot_engine_types = bot.get("engine_types")
        if bot_engine_types is None:
            bot_engine_types = engine_types
        bot["engine_paths"] = bot_service.get_engine_paths(
            entity_id,
            bot_id,
            bot_engine_types,
            entity_type,
        )

        active = bot.get("active_engine", DEFAULT_ENGINE_TYPE)
        engine_paths = bot["engine_paths"]
        fallback = list(engine_paths.values())[0] if engine_paths else ""
        bot["bot_work_dir"] = engine_paths.get(active, fallback)

    default_bot = None
    if items:
        first_bot = items[0]
        first_engine = first_bot.get("active_engine", DEFAULT_ENGINE_TYPE)
        default_bot = {
            "entity_id": first_bot.get("entity_id"),
            "bot_id": first_bot.get("bot_id"),
            "entity_type": first_bot.get("entity_type", "staff"),
            "bot_work_dir": first_bot.get("engine_paths", {}).get(
                first_engine,
                first_bot.get("bot_work_dir", ""),
            ),
        }

    return {
        "total": result["total"],
        "items": items,
        "default_bot": default_bot,
    }


@router.get("/by-owner-or-collaborator", response_model=ApiResponse)
async def list_bots_by_owner_or_collaborator(
    ctx: RequestContext = Depends(get_request_context),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> ApiResponse:
    """
    List bots owned by current user plus bots collaboratively managed by current user.

    GET /api/bots/by-owner-or-collaborator

    Other logic is identical to /by-owner, but the query scope is expanded to:
    - bots where owner_id = current authenticated user
    - bots where current authenticated user is a collaborator
    """
    try:
        data = await asyncio.to_thread(
            _build_bots_by_owner_or_collaborator_data,
            ctx.user_id,
            bot_service,
        )
        return ApiResponse(
            success=True,
            data=data,
            message="获取 Bot 列表成功",
        )
    except Exception as e:
        logger.error(f"[bot_router.list_bots_by_owner_or_collaborator] Unexpected error: {e}")
        return ApiResponse(
            success=False,
            message=f"获取 Bot 列表失败: {str(e)}",
            error_code=500,
            data=None,
        )


@router.get("/by-owner", response_model=ApiResponse)
async def list_bots_by_owner(
    ctx: RequestContext = Depends(get_request_context),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> ApiResponse:
    """
    List bots by owner_id with default bot info for management.

    GET /api/bots/by-owner

    Used when entering bot management pages (skills/resources).
    Returns all bots where owner_id = current authenticated user, with:
    - default_bot: first bot's entity_id and bot_id for initial directory
    - bot_work_dir: computed path for each bot

    User ID is obtained from Buservice authentication.

    Response:
    {
        "success": true,
        "data": {
            "total": 2,
            "items": [...],
            "default_bot": {
                "entity_id": "xxx",
                "bot_id": 2026031601234567,
                "entity_type": "staff",
                "bot_work_dir": "/aidesktop/aidesktop_dev/bolt_data/xxx/2026031601234567"
            }
        }
    }
    """
    try:
        owner_id = ctx.user_id

        # Get engine types
        engine_types = _get_engine_types()

        # Get all bots where owner_id = user_id
        result = bot_service.list_bots_by_owner(
            owner_id=owner_id,
            page=1,
            page_size=100,
        )

        items = result["items"]

        # Add engine_paths to each bot
        for bot in items:
            entity_id = bot.get("entity_id")
            entity_type = bot.get("entity_type", "staff")
            bot_id = str(bot.get("bot_id"))
            bot_engine_types = bot.get("engine_types", engine_types)

            # Get paths for all engines
            bot["engine_paths"] = bot_service.get_engine_paths(entity_id, bot_id, bot_engine_types, entity_type)

            # Add legacy bot_work_dir — prefer the bot's active_engine over the global default
            active = bot.get("active_engine", DEFAULT_ENGINE_TYPE)
            engine_paths = bot["engine_paths"]
            fallback = list(engine_paths.values())[0] if engine_paths else ""
            bot["bot_work_dir"] = engine_paths.get(active, fallback)

        # Determine default bot (first bot)
        default_bot = None
        if items:
            first_bot = items[0]
            first_engine = first_bot.get("active_engine", DEFAULT_ENGINE_TYPE)
            default_bot = {
                "entity_id": first_bot.get("entity_id"),
                "bot_id": first_bot.get("bot_id"),
                "entity_type": first_bot.get("entity_type"),
                "engine_type": first_engine,
                "bot_work_dir": first_bot.get("engine_paths", {}).get(first_engine, ""),
                "engine_paths": first_bot.get("engine_paths", {}),
            }

        return ApiResponse(
            success=True,
            data={
                "total": result["total"],
                "items": items,
                "default_bot": default_bot,
                "engine_types": engine_types,
            },
        )

    except Exception as e:
        # 打印堆栈
        logger.error(f"[bot_router.list_bots_by_owner] Error: {e}", exc_info=True)
        # logger.error(f"[bot_router.list_bots_by_owner] Error: {e}")
        return ApiResponse(
            success=False,
            message=f"查询用户Bot列表失败: {str(e)}",
            error_code=500,
            data=None,
        )


class SearchBotsRequest(BaseModel):
    """Request model for searching bots with publish info."""
    key: Optional[str] = None
    bot_status: Optional[str] = None
    public: Optional[str] = None
    owner_id: Optional[str] = None
    service_status_list: Optional[List[str]] = None
    bot_type: Optional[str] = None
    active_engine: Optional[str] = None  # 按引擎类型过滤（openclaw/claude_code/aicoding 等）
    page: int = 1
    page_size: int = 20


# 非 owner 搜索结果只允许返回的基础字段
_SEARCH_BOT_PUBLIC_FIELDS = {"id", "bot_id", "bot_name", "owner_id", "owner_name", "status", "public", "entity_id", "user_role"}


@router.post("/search", response_model=ApiResponse)
async def search_bots(
    request: Request,
    ctx: RequestContext = Depends(get_request_context),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> ApiResponse:
    """
    搜索 bots，关联发布记录。

    POST /api/bots/search
    Body: {
        "key": "关键词",                    // 可选，模糊搜索 bot_name 或 owner_name
        "bot_status": "ACTIVE",            // 可选，ac_bots.status 过滤
        "public": "1",                     // 可选，ac_bots.public 过滤
        "owner_id": "12345",               // 可选，ac_bots.owner_id 过滤
        "service_status_list": ["success", "init"],  // 可选，服务状态过滤
        "bot_type": "personal",            // 可选，ac_bots.bot_type 过滤（"personal" 或 "service"）
        "active_engine": "openclaw",       // 可选，ac_bots.active_engine 过滤（"openclaw"/"claude_code"/"aicoding" 等）
        "collaborator_user_id": "user123", // 可选，协作者用户 ID，用于过滤该用户参与的 bot
        "bot_id": "xxx",                   // 可选，按 bot_id 精确过滤
        "provider": "arca",                // 可选，按 device_provider 过滤（"arca"/"daas"/"local"/"baas"）
        "template_type": "applicationCoding",  // 可选，按 ac_bots.template_type 过滤
        "page": 1,
        "page_size": 20
    }

    权限脱敏:
        - 当前用户的 bot: 返回完整字段（含 publish、ext、engine_types、device_id 等）
        - 其他用户的 bot: 仅返回基础字段（id, bot_id, bot_name, owner_id, owner_name,
          status, public, entity_id, user_role），移除 publish/ext 等敏感数据

    Returns:
        {
            "success": true,
            "data": {
                "total": 100,
                "items": [
                    {
                        "id": 10,
                        "bot_id": "xxx",
                        "bot_name": "test_bot",
                        "owner_id": "12345",
                        "owner_name": "张三",
                        "status": "ACTIVE",
                        "public": "1",
                        "entity_id": "xxx",
                        "user_role": "owner",       // 用户角色（owner/collaborator）
                        "publish": {                // 仅 owner 可见
                            "id": 1,
                            "source_bot_pk": 10,
                            "publish_bot_id": "xxx_pub_1",
                            "status": "success",
                            "version": 1,
                            ...
                        }
                    },
                    {
                        "id": 11,
                        "bot_id": "yyy",
                        "bot_name": "another_bot",
                        "owner_id": "67890",        // 其他用户
                        "owner_name": "李四",
                        "status": "ACTIVE",
                        "public": "1",
                        "entity_id": "yyy",
                        "user_role": "collaborator"
                        // 非当前用户的 bot，无 publish/ext 等字段
                    }
                ]
            }
        }
    """
    try:
        data = await request.json()

        # 参数解析
        key = data.get("key")
        bot_status = data.get("bot_status")
        public = data.get("public")
        owner_id = data.get("owner_id")
        service_status_list = data.get("service_status_list")
        bot_type = data.get("bot_type")
        active_engine = data.get("active_engine")
        collaborator_user_id = data.get("collaborator_user_id")
        bot_id = data.get("bot_id")
        provider = data.get("provider")
        template_type = data.get("template_type")
        page = data.get("page", 1)
        page_size = data.get("page_size", 20)

        # 参数校验
        if page < 1:
            page = 1
        if page_size < 1 or page_size > 100:
            page_size = 20

        result = bot_service.search_bots(
            key=key,
            bot_status=bot_status,
            public=public,
            owner_id=owner_id,
            service_status_list=service_status_list,
            bot_type=bot_type,
            active_engine=active_engine,
            collaborator_user_id=collaborator_user_id,
            bot_id=bot_id,
            provider=provider,
            template_type=template_type,
            page=page,
            page_size=page_size,
        )

        # 权限脱敏：非 owner 且非协作者的 bot 只返回基础字段，移除 publish/ext 等敏感数据
        current_user = ctx.user_id
        for bot in result.get("items", []):
            # owner 或协作者可以查看完整字段，其他用户脱敏
            # user_role 来自协作者表，值为 "admin" 或 "member"
            is_owner = bot.get("owner_id") == current_user
            is_collaborator = bot.get("user_role") is not None
            if not (is_owner or is_collaborator):
                # 移除非基础字段（如 ext, publish, engine_types, device_id, bot_desc 等）
                bot_keys = list(bot.keys())
                for k in bot_keys:
                    if k not in _SEARCH_BOT_PUBLIC_FIELDS:
                        del bot[k]

        return ApiResponse(
            success=True,
            data=result,
        )

    except Exception as e:
        logger.error(f"[bot_router.search_bots] Error: {e}")
        return ApiResponse(
            success=False,
            message=f"搜索Bot失败: {str(e)}",
            error_code=500,
            data=None,
        )


@router.get("/search/domain-bots", response_model=ApiResponse)
async def list_domain_bots(
    request: Request,
    page: Optional[int] = Query(None, ge=1, description="Page number (1-based). Omit for all results"),
    page_size: Optional[int] = Query(None, ge=1, description="Items per page. Omit for all results"),
    keyword: Optional[str] = Query(None, description="Keyword to fuzzy-match on bot name"),
    _bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> ApiResponse:
    """
    List domain bots (bots with ext.is_domain_bot=true).

    GET /api/bots/search/domain-bots
    GET /api/bots/search/domain-bots?page=1&page_size=20&keyword=arch

    Domain bots are secondary architecture domain bots identified by
    checking the ext JSON field for is_domain_bot=true.
    When page/page_size are omitted, returns all matching results.
    """
    try:
        result = _bot_service.list_domain_bots(
            page=page,
            page_size=page_size,
            keyword=keyword,
        )

        # iam_token 是调用方 IAM 凭据(由 cookie 写入 bot.ext,见
        # update_bot_ext/trigger_data_init_api)。此端点不对调用方做 operator
        # 鉴权,公开的域 Bot 列表不应回传该凭据,逐条剔除。
        for item in result.get("items", []):
            ext = item.get("ext") if isinstance(item, dict) else None
            if isinstance(ext, dict):
                ext.pop("iam_token", None)

        return ApiResponse(
            success=True,
            data=result,
        )

    except Exception as e:
        logger.error(f"[bot_router.list_domain_bots] Error: {e}", exc_info=True)
        return ApiResponse(
            success=False,
            message=f"查询二级架构域Bot失败: {str(e)}",
            error_code=500,
            data=None,
        )


@router.get("", response_model=ApiResponse)
async def list_bots(
    request: Request,
    entity_id: Optional[str] = Query(None, description="Filter by entity ID"),
    entity_type: Optional[str] = Query(None, description="Filter by entity type (staff/proj/team)"),
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    auth_service: AuthPlugin = Injected(AuthPlugin),
) -> ApiResponse:
    """
    List bots with optional filtering.

    GET /api/bots?entity_id=xxx&entity_type=staff&page=1&page_size=20

    Security (pre/prod only): requires Buservice SSO authentication.
    - No params → defaults to current user's bots
    - entity_id provided → must match current user, otherwise 403
    Local mode: original behavior preserved (no auth required).
    """
    # AuthPlugin owns the per-runtime entity-access policy. Local impl is
    # passthrough; Prod impl enforces SSO + self-only. Errors raise
    # Unauthorized/Forbidden which the global exception handler maps to
    # 401/403 (api/app.py:_DOMAIN_ERROR_STATUS_MAP).
    from agentclaw.community.adapters.http.auth.dependencies import _build_auth_context
    entity_id, entity_type = await auth_service.authorize_entity_access(
        _build_auth_context(request),
        requested_entity_id=entity_id,
        requested_entity_type=entity_type,
    )

    try:
        result = bot_service.list_bots(
            entity_id=entity_id,
            entity_type=entity_type,
            page=page,
            page_size=page_size,
        )

        return ApiResponse(
            success=True,
            data=result,
        )

    except Exception as e:
        logger.error(f"[bot_router.list_bots] Error: {e}")
        return ApiResponse(
            success=False,
            message=f"查询Bot列表失败: {str(e)}",
            error_code=500,
            data=None,
        )


@router.get("/check/name", response_model=ApiResponse)
async def check_bot_name(
    bot_name: str = Query(..., description="Bot名称"),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> ApiResponse:
    """
    检查Bot名称是否已存在。

    GET /api/bots/check/name?bot_name=xxx

    Args:
        bot_name: Bot名称（必填，需要检查的名称）

    Returns:
        {
            "success": true,
            "data": {
                "exists": true/false,
                "bot_name": "xxx"
            }
        }

    Example:
        GET /api/bots/check/name?bot_name=MyBot
        Response:
        {
            "success": true,
            "message": "OK",
            "error_code": 200,
            "data": {
                "exists": true,
                "bot_name": "MyBot"
            }
        }
    """
    try:
        if not bot_name or not bot_name.strip():
            return ApiResponse(
                success=False,
                message="Bot名称不能为空",
                error_code=400,
                data=None,
            )

        exists = bot_service.check_bot_name_exists(bot_name=bot_name.strip())

        return ApiResponse(
            success=True,
            data={
                "exists": exists,
                "bot_name": bot_name.strip(),
            },
        )

    except Exception as e:
        logger.error(f"[bot_router.check_bot_name] Error: {e}")
        return ApiResponse(
            success=False,
            message=f"检查Bot名称失败: {str(e)}",
            error_code=500,
            data=None,
        )


@router.get("/ceiling", response_model=ApiResponse)
async def get_bots_ceiling(
    user: AuthenticatedUser = Depends(get_current_user),  # noqa: B008
    policy_service: PolicyServiceProtocol = Injected(PolicyServiceProtocol),
) -> ApiResponse:
    """GET /api/bots/ceiling — 返回当前用户的 BOT 数量上限。

    前端用于动态显示上限数值及控制创建按钮禁用状态。
    优先读取 ``ac_access_control_policy.policy.bots_ceiling``，
    无则 fallback 到默认值 5（与 ``device_allocation.max_devices_per_entity`` 一致）。

    注：该静态路由必须注册在 ``/{bot_id}`` 动态路由之前，否则
    ``/ceiling`` 会被 ``/{bot_id}`` 捕获（bot_id="ceiling"）而不可达。
    """
    try:
        ceiling = policy_service.get_bots_ceiling(entity_id=user.staffId)
        return ApiResponse(
            success=True,
            message="OK",
            error_code=200,
            data={"ceiling": ceiling},
        )
    except Exception as e:
        logger.error(f"[bot_router.get_bots_ceiling] error: {e}", exc_info=True)
        return ApiResponse(
            success=False,
            message=f"获取 BOT 上限失败: {str(e)}",
            error_code=500,
            data=None,
        )


@router.get("/{bot_id}", response_model=ApiResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$bot_id",
    owner_id="$owner_id",
    persist_audit_log=False,  # 只读操作，不需要审计和锁检查
))
async def get_bot(
    bot_id: str,
    owner_id: Optional[str] = Query(None, description="Bot owner ID"),
    ctx: RequestContext = Depends(get_request_context),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> ApiResponse:
    """
    Get bot details by ID.

    GET /api/bots/{bot_id}
    """
    try:
        # Get operator from request context; resource lookup uses explicit owner_id
        operator_id = ctx.user_id

        if not operator_id or operator_id == "anonymous":
            return ApiResponse(
                success=False,
                message="无法获取用户信息",
                error_code=400,
                data=None,
            )

        resolved_owner_id = owner_id or operator_id

        result = bot_service.get_bot(bot_id, resolved_owner_id)
        return ApiResponse(
            success=True,
            data=result,
        )

    except BotNotFoundError:
        return ApiResponse(
            success=False,
            message=f"Bot不存在: {bot_id}",
            error_code=404,
            data=None,
        )
    except Exception as e:
        logger.error(f"[bot_router.get_bot] Error: {e}")
        return ApiResponse(
            success=False,
            message=f"获取Bot失败: {str(e)}",
            error_code=500,
            data=None,
        )


@router.get("/{bot_id}/detail-by-owner", response_model=ApiResponse)
async def get_bot_detail_by_owner(
    bot_id: str,
    owner_id: str = Query(..., description="Bot owner ID (工号)"),
    user: AuthenticatedUser = Depends(require_operator),  # noqa: B008  # 运维接口，仅允许操作员调用
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> ApiResponse:
    """
    【运维接口】根据 bot_id + owner_id 查询 bot 详情。

    仅允许 operator 白名单中的用户调用。
    GET /api/bots/{bot_id}/detail-by-owner?owner_id={owner_id}
    """
    try:
        if not owner_id:
            return ApiResponse(
                success=False,
                message="缺少owner_id参数",
                error_code=400,
                data=None,
            )

        result = bot_service.get_bot(bot_id, owner_id)
        return ApiResponse(
            success=True,
            data=result,
        )

    except BotNotFoundError:
        return ApiResponse(
            success=False,
            message=f"Bot不存在: {bot_id}",
            error_code=404,
            data=None,
        )
    except Exception as e:
        logger.error(f"[bot_router.get_bot_detail_by_owner] Error: {e}")
        return ApiResponse(
            success=False,
            message=f"获取Bot失败: {str(e)}",
            error_code=500,
            data=None,
        )


@router.get("/{bot_id}/appcoding-bots", response_model=ApiResponse)
async def list_coding_bots_by_architect(
    bot_id: str,
    ctx: RequestContext = Depends(get_request_context),
    _bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> ApiResponse:
    """
    List application coding bots associated with a domain architect bot.

    GET /api/bots/{bot_id}/appcoding-bots

    A domain architect bot has ac_bots.ext.is_domain_bot == true.
    Application coding bots are linked via ac_templates.ext.architect_bot_id.
    """
    try:
        user_id = ctx.user_id
        if not user_id or user_id == "anonymous":
            return ApiResponse(
                success=False,
                message="无法获取用户信息",
                error_code=400,
                data=None,
            )

        coding_bots = _bot_service.list_coding_bots_by_architect(bot_id)
        return ApiResponse(
            success=True,
            data=coding_bots,
        )
    except Exception as e:
        logger.error(f"[bot_router.list_coding_bots_by_architect] Error: {e}")
        return ApiResponse(
            success=False,
            message=f"获取关联应用Coding Bot失败: {str(e)}",
            error_code=500,
            data=None,
        )


@router.put("/{bot_id}/admin", response_model=ApiResponse)
async def admin_update_bot(
    bot_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(require_operator),  # noqa: B008
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> ApiResponse:
    """
    Admin update bot config — operator only.

    PUT /api/bots/{bot_id}/admin
    Body: {
        "owner_id": "100000",           // Required: target bot's owner
        "bot_name": "New Name",          // Optional
        "bot_desc": "New Description",   // Optional
        "template_config": {             // Optional: sandbox overrides
            "image": "registry.example.com/custom:v2",
            "command": "/bin/bash",
            "envs": {"KEY": "VALUE"},
            "resource_spec": {"cpu": 4, "memory": 8, "disk": 100}
        }
    }

    Only operators (require_operator) can call this endpoint.
    Unlike PUT /api/bots/{bot_id}, this endpoint:
    - Does not require the caller to be the bot owner
    - Supports updating template_config (sandbox overrides)
    - Sandbox config changes take effect on next bot restart
    """
    try:
        data = await request.json()

        owner_id = data.get("owner_id")
        if not owner_id or not owner_id.strip():
            return ApiResponse(
                success=False,
                message="缺少必填参数: owner_id",
                error_code=400,
                data=None,
            )

        template_config = data.get("template_config")

        # bot_name 早校验（与 create_bot/update_bot 对齐）：允许 None（本次不改名），
        # 非 None 时严格校验。必须早退，否则 BotNameInvalidError 会被下方
        # BotServiceError 兜底吞成 500。
        raw_bot_name = data.get("bot_name")
        if raw_bot_name is not None:
            try:
                data["bot_name"] = validate_bot_name(raw_bot_name)
            except BotNameInvalidError as e:
                logger.warning(f"[bot_router.admin_update_bot] Invalid bot_name: {e}")
                return ApiResponse(
                    success=False,
                    message=str(e),
                    error_code=400,
                    data=None,
                )

        result = bot_service.admin_update_bot(
            bot_id=bot_id,
            owner_id=owner_id.strip(),
            bot_name=data.get("bot_name"),
            bot_desc=data.get("bot_desc"),
            template_config=template_config,
        )

        return ApiResponse(
            success=True,
            data=result,
        )

    except BotNotFoundError:
        return ApiResponse(
            success=False,
            message=f"Bot不存在: {bot_id}",
            error_code=404,
            data=None,
        )
    except BotNameExistsError as e:
        return ApiResponse(
            success=False,
            message=f"{str(e)}",
            error_code=409,
            data=None,
        )
    except BotServiceError as e:
        error_msg = str(e)
        error_code = 400 if "校验失败" in error_msg else 500
        return ApiResponse(
            success=False,
            message=f"管理员更新Bot失败: {error_msg}",
            error_code=error_code,
            data=None,
        )
    except Exception as e:
        logger.error("[bot_router.admin_update_bot] Unexpected error: %s", e)
        return ApiResponse(
            success=False,
            message=f"管理员更新Bot失败: {str(e)}",
            error_code=500,
            data=None,
        )


@router.put("/{bot_id}", response_model=ApiResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$bot_id",
    owner_id="$owner_id",
))
async def update_bot(
    bot_id: str,
    request: Request,
    owner_id: Optional[str] = Query(None, description="Bot owner ID"),
    ctx: RequestContext = Depends(get_request_context),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    passport_plugin: PassportPlugin = Injected(PassportPlugin),
) -> ApiResponse:
    """
    Update bot information.

    PUT /api/bots/{bot_id}
    Body: {
        "bot_name": "New Name",                            // optional
        "bot_desc": "New Desc",                            // optional
        "share_policy": {...},                             // optional
        "avatar_url": "https://example.com/avatar.png"     // optional, bot avatar URL, stored in ext field
    }
    """
    try:
        data = await request.json()

        # Get operator from request context; resource lookup uses resolved owner_id
        operator_id = ctx.user_id

        if not operator_id or operator_id == "anonymous":
            return ApiResponse(
                success=False,
                message="无法获取用户信息",
                error_code=400,
                data=None,
            )

        # Build ext field. Keep backward compatibility for top-level avatar_url,
        # and also support generic ext updates such as ext.read_only_rules.
        # Note: ext={} is a valid update payload and should be passed through;
        # ext.read_only_rules=[] must clear user custom read-only rules.
        ext = data.get("ext")
        if ext is not None and not isinstance(ext, dict):
            return ApiResponse(
                success=False,
                message="ext 必须是 JSON 对象",
                error_code=400,
                data=None,
            )

        avatar_url = data.get("avatar_url")
        if avatar_url:
            ext = {**(ext or {}), "avatar_url": avatar_url}

        template_config = data.get("template_config")
        logger.info(
            "[bot_router.update_bot] bot_id=%s, template_config=%s",
            bot_id,
            "provided" if template_config else "None",
        )

        resolved_owner_id = owner_id or operator_id

        # Get cookie for potential memoryos API call (when yuque_kb_repos changes)
        cookie = request.headers.get("cookie", "")

        # 兼容历史请求字段 name，统一走 bot_name 的校验和更新链路，避免旧客户端
        # 传入空名称时被静默忽略并返回成功。
        if "bot_name" not in data and "name" in data:
            data["bot_name"] = data["name"]

        # bot_name 早校验（与 create_bot 对齐）：允许 None（本次不改名），非 None 时
        # 严格校验非法字符/长度，避免脏名落库及污染下游 passport / 同步链路。
        raw_bot_name = data.get("bot_name")
        if raw_bot_name is not None:
            try:
                data["bot_name"] = validate_bot_name(raw_bot_name)
            except BotNameInvalidError as e:
                logger.warning(f"[bot_router.update_bot] Invalid bot_name: {e}")
                return ApiResponse(
                    success=False,
                    message=str(e),
                    error_code=400,
                    data=None,
                )

        result = bot_service.update_bot(
            bot_id=bot_id,
            user_id=resolved_owner_id,
            bot_name=data.get("bot_name"),
            bot_desc=data.get("bot_desc"),
            share_policy=data.get("share_policy"),
            ext=ext,
            template_config=template_config,
            request_headers=dict(request.headers),
            cookie=cookie,
        )

        # 改名/描述只更新 passport 元信息，不携带 MCP/CLI 资源范围。
        bot_name = data.get("bot_name")
        bot_desc = data.get("bot_desc")
        if bot_name or bot_desc:
            try:
                # Passport 更新需要 engine_type；优先 body，再回退到 bot.active_engine，最后 DEFAULT_ENGINE_TYPE
                passport_engine_type = (
                    data.get("engine_type")
                    or result.get("active_engine")
                    or DEFAULT_ENGINE_TYPE
                )
                passport_plugin.update_passport(
                    bot_id=bot_id,
                    user_id=resolved_owner_id,
                    bot_name=bot_name,
                    bot_desc=bot_desc,
                    engine_type=passport_engine_type,
                )
                logger.info(f"[bot_router.update_bot] Updated passport for bot {bot_id}, engine_type={passport_engine_type}")
            except Exception as e:
                logger.warning(f"[bot_router.update_bot] Failed to update passport: {e}")
                # 不阻断主流程，只记录警告

        return ApiResponse(
            success=True,
            data=result,
        )

    except BotNotFoundError:
        return ApiResponse(
            success=False,
            message=f"Bot不存在: {bot_id}",
            error_code=404,
            data=None,
        )
    except BotNameExistsError as e:
        logger.warning(f"[bot_router.update_bot] Bot name exists: {e}")
        return ApiResponse(
            success=False,
            message=f"{str(e)}",
            error_code=409,
            data=None,
        )
    except BotServiceError as e:
        logger.error(f"[bot_router.update_bot] Bot service error: {e}")
        return ApiResponse(
            success=False,
            message=f"更新Bot失败: {str(e)}",
            error_code=500,
            data=None,
        )
    except Exception as e:
        logger.error(f"[bot_router.update_bot] Error: {e}")
        return ApiResponse(
            success=False,
            message=f"更新Bot失败: {str(e)}",
            error_code=500,
            data=None,
        )


@router.delete("/{bot_id}", response_model=ApiResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    required_level=PermissionLevel.OWNER,  # 仅 bot owner 可执行
    bot_id="$bot_id",
    owner_id="$owner_id",
))
async def delete_bot(
    bot_id: str,
    owner_id: Optional[str] = Query(None, description="Bot owner ID"),
    ctx: RequestContext = Depends(get_request_context),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> ApiResponse:
    """
    Delete a bot (soft delete).

    DELETE /api/bots/{bot_id}
    """
    try:
        # Get operator from request context; resource lookup uses resolved owner_id
        operator_id = ctx.user_id

        if not operator_id or operator_id == "anonymous":
            return ApiResponse(
                success=False,
                message="无法获取用户信息",
                error_code=400,
                data=None,
            )

        resolved_owner_id = owner_id or operator_id

        bot_service.delete_bot(bot_id=bot_id, user_id=resolved_owner_id)

        return ApiResponse(
            success=True,
            message=f"Bot {bot_id} 已删除",
        )

    except BotNotFoundError:
        return ApiResponse(
            success=False,
            message=f"Bot不存在: {bot_id}",
            error_code=404,
            data=None,
        )
    except PassportError as e:
        logger.error(f"[bot_router.delete_bot] TCAuth error: {e}")
        return ApiResponse(
            success=False,
            message=f"销毁授权凭证失败: {str(e)}",
            error_code=500,
            data=None,
        )
    except BotServiceError as e:
        logger.error(f"[bot_router.delete_bot] Bot service error: {e}")
        return ApiResponse(
            success=False,
            message=f"删除Bot失败: {str(e)}",
            error_code=500,
            data=None,
        )
    except Exception as e:
        logger.error(f"[bot_router.delete_bot] Error: {e}")
        return ApiResponse(
            success=False,
            message=f"删除Bot失败: {str(e)}",
            error_code=500,
            data=None,
        )


@router.get("/{bot_id}/status", response_model=ApiResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$bot_id",
    owner_id="$owner_id",
    persist_audit_log=False,  # 只读操作，不需要审计和锁检查
))
async def get_bot_status(
    bot_id: str,
    owner_id: Optional[str] = Query(None, description="Bot owner ID"),
    ctx: RequestContext = Depends(get_request_context),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> ApiResponse:
    """
    Get bot device allocation status.

    GET /api/bots/{bot_id}/status

    Used for polling device allocation status after bot creation.
    Status flow: PENDING -> ACTIVE/FAILED

    Response includes:
    - bot_status: PENDING, ACTIVE, FAILED
    - binding_status: PENDING, ACTIVE, FAILED, RELEASED
    - device_id: allocated device ID (if ACTIVE)
    - error_message: error details (if FAILED)

    Example response when pending:
    {
        "success": true,
        "data": {
            "bot_id": 2026031601234567,
            "bot_status": "PENDING",
            "binding_status": "PENDING",
            "device_id": null,
            "error_message": null,
            "is_ready": false
        }
    }

    Example response when active:
    {
        "success": true,
        "data": {
            "bot_id": 2026031601234567,
            "bot_status": "ACTIVE",
            "binding_status": "ACTIVE",
            "device_id": "device_xxx",
            "device_provider": "provider_xxx",
            "error_message": null,
            "is_ready": true
        }
    }
    """
    try:
        # Get operator from request context; resource lookup uses resolved owner_id
        operator_id = ctx.user_id

        if not operator_id or operator_id == "anonymous":
            return ApiResponse(
                success=False,
                message="无法获取用户信息",
                error_code=400,
                data=None,
            )

        resolved_owner_id = owner_id or operator_id

        bot = bot_service.get_bot(bot_id, resolved_owner_id)

        # Extract status info
        bot_status = bot.get("status", "UNKNOWN")
        binding_info = bot.get("device_binding", {})
        ext = bot.get("ext") or {}
        binding_status = binding_info.get("status", "UNKNOWN") if binding_info else "UNKNOWN"
        # 与 is_ready 共用同一份判定（core/bot_management/readiness.py）。
        stale_baas_failure = has_stale_baas_publish_failure(bot)

        # 失败信息来源（按优先级）：
        # 1. binding.error_message — 设备层直接上报（DaaS 等）
        # 2. ext.start_message — service-start 失败时 _mark_service_start_failed 写入；
        #    aicoding 应用 bot 的 .repos/ clone 失败也会落到这一档：
        #    finalize.sh Step 5.4 workspace_warmup 失败 → STARTING_MARKER_FILE=FAILED
        #    → starting_watchdog 上报 status=FAILED + tail 1 行 log
        # binding 层没有 error_message 字段时（如 DeviceBindingRecord）退到 ext
        error_message = binding_info.get("error_message") if binding_info else None
        if not error_message and ext.get("start_status") == "FAILED" and not stale_baas_failure:
            error_message = ext.get("start_message")
        response_ext = _sanitize_baas_status_ext_for_response(
            bot_status=bot_status,
            binding_status=binding_status,
            device_provider=(
                binding_info.get("device_provider") if binding_info else "UNKNOWN"
            ),
            ext=ext,
        )

        # aicoding 应用 bot：等 .repos/ 仓库克隆完成才算 ready。
        # 判定策略见 core/bot_management/readiness.py —— 内部与公共 API 共用同一实现。
        result = {
            "bot_id": bot_id,
            "bot_status": bot_status,
            "binding_status": binding_status,
            "device_id": binding_info.get("device_id") if binding_info else None,
            "device_provider": binding_info.get("device_provider") if binding_info else None,
            "error_message": error_message,
            "is_ready": is_bot_ready(bot),
            "ext": response_ext,  # 添加扩展字段
        }

        return ApiResponse(
            success=True,
            data=result,
        )

    except BotNotFoundError:
        return ApiResponse(
            success=False,
            message=f"Bot不存在: {bot_id}",
            error_code=404,
            data=None,
        )
    except Exception as e:
        logger.error(f"[bot_router.get_bot_status] Error: {e}")
        return ApiResponse(
            success=False,
            message=f"获取Bot状态失败: {str(e)}",
            error_code=500,
            data=None,
        )


@router.get("/{bot_id}/work-dir", response_model=ApiResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$bot_id",
    owner_id="$owner_id",
    persist_audit_log=False,  # 只读操作，不需要审计和锁检查
))
async def get_bot_work_dir(
    bot_id: str,
    owner_id: Optional[str] = Query(None, description="Bot owner ID"),
    engine_type: Optional[str] = Query(None, description="Engine override; defaults to bot's active_engine"),
    ctx: RequestContext = Depends(get_request_context),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    bot_repo: BotRepository = Injected(BotRepository),
) -> ApiResponse:
    """
    Get bot working directory path. Defaults to bot's active_engine.

    GET /api/bots/{bot_id}/work-dir?engine_type=moltis

    Args:
        bot_id: Bot ID
        engine_type: Optional override; if omitted, the bot's active_engine is used

    Returns:
        Single path string for the bot working directory
    """
    try:
        # Get operator from request context; resource lookup uses resolved owner_id
        operator_id = ctx.user_id

        if not operator_id or operator_id == "anonymous":
            return ApiResponse(
                success=False,
                message="无法获取用户信息",
                error_code=400,
                data=None,
            )

        resolved_owner_id = owner_id or operator_id

        effective_engine = resolve_engine_for_bot(
            bot_id=bot_id, owner_id=resolved_owner_id, override=engine_type, bot_repo=bot_repo,
        )

        # Get work directory path using service method with permission check
        work_path = bot_service.get_bot_work_path(
            bot_id=bot_id,
            user_id=resolved_owner_id,
            engine_type=effective_engine,
        )

        return ApiResponse(
            success=True,
            data=str(work_path),
        )

    except BotNotFoundError:
        return ApiResponse(
            success=False,
            message=f"Bot不存在: {bot_id}",
            error_code=404,
            data=None,
        )
    except Exception as e:
        logger.error(f"[bot_router.get_bot_work_dir] Error: {e}")
        return ApiResponse(
            success=False,
            message=f"获取Bot工作目录失败: {str(e)}",
            error_code=500,
            data=None,
        )


@router.get("/{bot_id}/config-dir", response_model=ApiResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$bot_id",
    owner_id="$owner_id",
    persist_audit_log=False,  # 只读操作，不需要审计和锁检查
))
async def get_bot_config_dir(
    bot_id: str,
    owner_id: Optional[str] = Query(None, description="Bot owner ID"),
    engine_type: Optional[str] = Query(None, description="Engine override; defaults to bot's active_engine"),
    ctx: RequestContext = Depends(get_request_context),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    bot_repo: BotRepository = Injected(BotRepository),
) -> ApiResponse:
    """
    Get bot configuration directory path. Defaults to bot's active_engine.

    GET /api/bots/{bot_id}/config-dir?engine_type=moltis

    Args:
        bot_id: Bot ID
        engine_type: Optional override; if omitted, the bot's active_engine is used

    Returns:
        Single path string for the bot configuration directory
    """
    try:
        # Get operator from request context; resource lookup uses resolved owner_id
        operator_id = ctx.user_id

        if not operator_id or operator_id == "anonymous":
            return ApiResponse(
                success=False,
                message="无法获取用户信息",
                error_code=400,
                data=None,
            )

        resolved_owner_id = owner_id or operator_id

        effective_engine = resolve_engine_for_bot(
            bot_id=bot_id, owner_id=resolved_owner_id, override=engine_type, bot_repo=bot_repo,
        )

        # Get config directory path using service method with permission check
        config_path = bot_service.get_bot_config_path(
            bot_id=bot_id,
            user_id=resolved_owner_id,
            engine_type=effective_engine,
        )

        return ApiResponse(
            success=True,
            data=str(config_path),
        )

    except BotNotFoundError:
        return ApiResponse(
            success=False,
            message=f"Bot不存在: {bot_id}",
            error_code=404,
            data=None,
        )
    except Exception as e:
        logger.error(f"[bot_router.get_bot_config_dir] Error: {e}")
        return ApiResponse(
            success=False,
            message=f"获取Bot配置目录失败: {str(e)}",
            error_code=500,
            data=None,
        )


@router.post("/{bot_id}/restart", response_model=ApiResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$bot_id",
    owner_id="$owner_id",
))
async def restart_bot(
    bot_id: str,
    request: Request,
    response: Response,
    owner_id: Optional[str] = Query(None, description="Bot owner ID"),
    ctx: RequestContext = Depends(get_request_context),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> ApiResponse:
    """
    Restart a bot by releasing current device and allocating a new one.

    POST /api/bots/{bot_id}/restart

    This will:
    1. Release the current device bound to the bot
    2. Reset bot status to PENDING
    3. Trigger async device allocation

    Returns updated bot record with PENDING status.
    Use GET /api/bots/{bot_id}/status to poll for device allocation completion.

    User ID is obtained from Buservice authentication (not from request body for security).
    """
    try:
        # Get operator from authentication context; resource lookup uses resolved owner_id
        operator_id = ctx.user_id
        nick_name = ctx.nick_name or operator_id

        if not operator_id or operator_id == "anonymous":
            return ApiResponse(
                success=False,
                message="无法获取认证用户信息",
                error_code=401,
                data=None,
            )

        resolved_owner_id = owner_id or operator_id

        result = bot_service.restart_bot(
            bot_id=bot_id,
            user_id=resolved_owner_id,
            nick_name=nick_name,
        )

        if result.get("restart_in_progress"):
            response.status_code = 202

        return ApiResponse(
            success=True,
            data=result,
        )

    except BotNotFoundError:
        return ApiResponse(
            success=False,
            message=f"Bot不存在: {bot_id}",
            error_code=404,
            data=None,
        )
    except BotInvalidLifecycleStateError as e:
        logger.warning(f"[bot_router.restart_bot] Invalid lifecycle state: {e}")
        response.status_code = 409
        return ApiResponse(
            success=False,
            message=f"当前状态不允许重启Bot: {str(e)}",
            error_code=409,
            data=None,
        )
    except BotServiceError as e:
        logger.error(f"[bot_router.restart_bot] Bot service error: {e}")
        return ApiResponse(
            success=False,
            message=f"重启Bot失败: {str(e)}",
            error_code=500,
            data=None,
        )
    except Exception as e:
        logger.error(f"[bot_router.restart_bot] Unexpected error: {e}")
        return ApiResponse(
            success=False,
            message=f"重启Bot失败: {str(e)}",
            error_code=500,
            data=None,
        )


@router.post("/switch-engine", response_model=ApiResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    required_level=PermissionLevel.OWNER,
    bot_id="$request.bot_id",
    owner_id="$request.owner_id",
))
async def switch_engine(
    request: Request,
    ctx: RequestContext = Depends(get_request_context),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> ApiResponse:
    """
    Switch the active engine for a bot.

    POST /api/bots/switch-engine
    Body: {
        "bot_id": "2026031601234567",
        "engine_type": "openclaw"
    }

    Switches the bot's active_engine to the specified engine_type.
    Only the bot owner can switch the engine.

    User ID is obtained from Buservice authentication.

    Returns:
        Updated bot record with new active_engine

    Example response:
    {
        "success": true,
        "data": {
            "bot_id": "2026031601234567",
            "active_engine": "openclaw",
            ...
        }
    }
    """
    try:
        data = await request.json()

        bot_id = data.get("bot_id")
        engine_type = data.get("engine_type")

        # Get operator from authentication context; resource lookup uses resolved owner_id
        operator_id = ctx.user_id

        # Validate required fields
        if not bot_id:
            return ApiResponse(
                success=False,
                message="缺少必填参数: bot_id",
                error_code=400,
                data=None,
            )
        if not operator_id or operator_id == "anonymous":
            return ApiResponse(
                success=False,
                message="无法获取认证用户信息",
                error_code=401,
                data=None,
            )
        if not engine_type:
            return ApiResponse(
                success=False,
                message="缺少必填参数: engine_type",
                error_code=400,
                data=None,
            )

        resolved_owner_id = data.get("owner_id") or operator_id

        # Call service to switch engine
        result = bot_service.switch_engine(
            bot_id=bot_id,
            user_id=resolved_owner_id,
            engine_type=engine_type,
        )

        return ApiResponse(
            success=True,
            data=result,
        )

    except BotNotFoundError:
        return ApiResponse(
            success=False,
            message="Bot不存在",
            error_code=404,
            data=None,
        )
    except BotServiceError as e:
        logger.error(f"[bot_router.switch_engine] Bot service error: {e}")
        return ApiResponse(
            success=False,
            message=f"切换引擎失败: {str(e)}",
            error_code=400,
            data=None,
        )
    except Exception as e:
        logger.error(f"[bot_router.switch_engine] Unexpected error: {e}")
        return ApiResponse(
            success=False,
            message=f"切换引擎失败: {str(e)}",
            error_code=500,
            data=None,
        )


@router.get("/{bot_id}/engine-config", response_model=ApiResponse)
@with_interceptors(
    CollaboratorPermissionInterceptor(
        bot_id="$bot_id",
        owner_id="$owner_id",
        persist_audit_log=False,  # 只读操作，不需要审计和锁检查
    )
)
async def get_engine_config(
    bot_id: str,
    owner_id: Optional[str] = Query(None, description="Bot owner workno; defaults to current user"),
    engine_type: Optional[str] = Query(None, description="Engine override; defaults to bot's active_engine"),
    ctx: RequestContext = Depends(get_request_context),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    engine_config_service: EngineConfigServiceProtocol = Injected(
        EngineConfigServiceProtocol
    ),
) -> ApiResponse:
    """
    Get engine configuration for a bot. Defaults to bot's active_engine.

    GET /api/bots/{bot_id}/engine-config?engine_type=openclaw

    Args:
        bot_id: Bot ID
        engine_type: Optional override; if omitted, the bot's active_engine is used

    Returns:
        Engine configuration JSON object

    Example response:
    {
        "success": true,
        "data": {
            "key1": "value1",
            "key2": "value2"
        }
    }
    """
    import json

    try:
        operator_id = ctx.user_id
        resolved_owner_id = owner_id or operator_id

        if not operator_id or operator_id == "anonymous":
            return ApiResponse(
                success=False,
                message="无法获取用户信息",
                error_code=400,
                data=None,
            )

        # Get bot info (with permission check)
        bot = bot_service.get_bot(bot_id, resolved_owner_id)
        entity_id = bot.get("entity_id")
        entity_type = bot.get("entity_type", "staff")

        if not entity_id:
            return ApiResponse(
                success=False,
                message="Bot 没有关联的 entity_id",
                error_code=500,
                data=None,
            )

        effective_engine = engine_type or bot.get("active_engine") or DEFAULT_ENGINE_TYPE

        # Provider-blind read through EngineConfigService (resolve_for_bot +
        # dispatch_addressed(config)). data is {} only when the file is missing/empty;
        # malformed JSON / resolve failures surface via the except blocks below.
        data = await engine_config_service.read_bot_config(
            bot_id=bot_id, owner_id=resolved_owner_id,
            entity_id=entity_id, entity_type=entity_type, engine_type=effective_engine,
        )
        return ApiResponse(success=True, data=data)

    except BotNotFoundError:
        return ApiResponse(
            success=False,
            message=f"Bot不存在: {bot_id}",
            error_code=404,
            data=None,
        )
    except json.JSONDecodeError as e:
        logger.error(f"[bot_router.get_engine_config] Invalid JSON in config file: {e}")
        return ApiResponse(
            success=False,
            message=f"配置文件格式错误: {str(e)}",
            error_code=500,
            data=None,
        )
    except Exception as e:
        logger.error(f"[bot_router.get_engine_config] Error: {e}")
        return ApiResponse(
            success=False,
            message=f"获取引擎配置失败: {str(e)}",
            error_code=500,
            data=None,
        )


@router.put("/{bot_id}/engine-config", response_model=ApiResponse)
@with_interceptors(
    CollaboratorPermissionInterceptor(
        bot_id="$bot_id",
        owner_id="$owner_id",
    )
)
async def update_engine_config(
    bot_id: str,
    request: Request,
    owner_id: Optional[str] = Query(None, description="Bot owner workno; defaults to current user"),
    engine_type: Optional[str] = Query(None, description="Engine override; defaults to bot's active_engine"),
    ctx: RequestContext = Depends(get_request_context),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    engine_config_service: EngineConfigServiceProtocol = Injected(
        EngineConfigServiceProtocol
    ),
) -> ApiResponse:
    """
    Update (save) engine configuration for a bot. Defaults to bot's active_engine.

    PUT /api/bots/{bot_id}/engine-config?engine_type=openclaw
    Body: {
        "key1": "value1",
        "key2": "value2"
    }

    Args:
        bot_id: Bot ID
        engine_type: Optional override; if omitted, the bot's active_engine is used

    Returns:
        Updated engine configuration JSON object

    Example response:
    {
        "success": true,
        "data": {
            "key1": "value1",
            "key2": "value2"
        }
    }
    """
    try:
        operator_id = ctx.user_id
        resolved_owner_id = owner_id or operator_id

        if not operator_id or operator_id == "anonymous":
            return ApiResponse(
                success=False,
                message="无法获取用户信息",
                error_code=400,
                data=None,
            )

        # Get bot info (with permission check)
        bot = bot_service.get_bot(bot_id, resolved_owner_id)
        entity_id = bot.get("entity_id")
        entity_type = bot.get("entity_type", "staff")

        if not entity_id:
            return ApiResponse(
                success=False,
                message="Bot 没有关联的 entity_id",
                error_code=500,
                data=None,
            )

        # 引擎配置必须是一个 JSON 对象。显式转换解析/类型错误，避免无效内容
        # 被通用异常处理为 500，或将数组、字符串等顶层值写进配置文件。
        try:
            config_data = await request.json()
        except json.JSONDecodeError:
            return ApiResponse(
                success=False,
                message="请求体不是有效的 JSON",
                error_code=400,
                data=None,
            )
        if not isinstance(config_data, dict):
            return ApiResponse(
                success=False,
                message="引擎配置必须是 JSON 对象",
                error_code=400,
                data=None,
            )

        effective_engine = engine_type or bot.get("active_engine") or DEFAULT_ENGINE_TYPE

        # Provider-blind write through EngineConfigService (resolve_for_bot +
        # dispatch_addressed(config) → device_fs.write_file). One path for
        # arca/baas/teclaw/local — no device_provider branching, no arca special-case.
        await engine_config_service.write_bot_config(
            bot_id=bot_id, owner_id=resolved_owner_id,
            entity_id=entity_id, entity_type=entity_type, engine_type=effective_engine,
            config=config_data,
        )
        logger.info(f"[bot_router.update_engine_config] Saved engine config for bot {bot_id}")
        return ApiResponse(
            success=True,
            data=config_data,
            message="引擎配置保存成功",
        )

    except BotNotFoundError:
        return ApiResponse(
            success=False,
            message=f"Bot不存在: {bot_id}",
            error_code=404,
            data=None,
        )
    except Exception as e:
        logger.error(f"[bot_router.update_engine_config] Error: {e}")
        return ApiResponse(
            success=False,
            message=f"保存引擎配置失败: {str(e)}",
            error_code=500,
            data=None,
        )


@router.patch("/{bot_id}/ext", response_model=ApiResponse)
@with_interceptors(
    CollaboratorPermissionInterceptor(
        required_level=PermissionLevel.OWNER,
        bot_id="$bot_id",
        owner_id="$owner_id",
    )
)
async def update_bot_ext(
    bot_id: str,
    request: Request,
    owner_id: Optional[str] = Query(None, description="Bot owner workno; defaults to current user"),
    ctx: RequestContext = Depends(get_request_context),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    data_init_service: DataInitServiceProtocol = Injected(DataInitServiceProtocol),
) -> ApiResponse:
    """
    Partially update bot ext field.

    PATCH /api/bots/{bot_id}/ext
    Body: { "key1": "value1", "key2": "value2", ... }

    Merges the provided fields into the existing ext JSON.
    Only the bot owner can update ext.
    """
    try:
        operator_id = ctx.user_id
        resolved_owner_id = owner_id or operator_id

        if not operator_id or operator_id == "anonymous":
            return ApiResponse(
                success=False,
                message="无法获取用户信息",
                error_code=400,
                data=None,
            )

        ext_update = await request.json()
        if not isinstance(ext_update, dict):
            return ApiResponse(
                success=False,
                message="请求体必须是 JSON 对象",
                error_code=400,
                data=None,
            )

        bot_service.update_bot_ext(bot_id, resolved_owner_id, ext_update)

        # 如果写入了 authorized_sources，且从未初始化过，自动触发数据初始化（只触发一次）
        if "authorized_sources" in ext_update:
            try:
                import asyncio

                bot = bot_service.get_bot(bot_id, resolved_owner_id)
                ext = bot.get("ext") or {}
                if isinstance(ext, str):
                    import json as _json
                    try:
                        ext = _json.loads(ext)
                    except Exception:
                        ext = {}

                existing_status = ext.get("data_init_status")
                if existing_status in ("completed", "in_progress"):
                    logger.info(
                        f"bot_id={bot_id} update_bot_ext data_init_skipped data_init_status={existing_status}"
                    )
                else:
                    entity_id = bot.get("entity_id") or resolved_owner_id
                    entity_type = bot.get("entity_type") or "staff"

                    # 保存用户 IAM_TOKEN 到 bot.ext，供异步 data_init 使用
                    iam_token = request.cookies.get("IAM_TOKEN") or ""
                    logger.info(
                        f"bot_id={bot_id} update_bot_ext data_init_iam_token "
                        f"has_iam_token={bool(iam_token)}"
                    )
                    if iam_token:
                        bot_service.update_bot_ext(bot_id, resolved_owner_id, {"iam_token": iam_token})

                    asyncio.ensure_future(
                        data_init_service.trigger_init(
                            bot_id=bot_id,
                            owner_id=resolved_owner_id,
                            entity_id=entity_id,
                            entity_type=entity_type,
                        )
                    )
                    logger.info(f"bot_id={bot_id} update_bot_ext data_init_triggered")
            except Exception as init_err:
                logger.warning(f"bot_id={bot_id} update_bot_ext data_init_trigger_failed exc={init_err}")

        return ApiResponse(
            success=True,
            message="ext 更新成功",
        )

    except BotNotFoundError:
        return ApiResponse(
            success=False,
            message=f"Bot不存在: {bot_id}",
            error_code=404,
            data=None,
        )
    except BotPermissionError as e:
        return ApiResponse(
            success=False,
            message=str(e),
            error_code=403,
            data=None,
        )
    except Exception as e:
        logger.error(f"bot_id={bot_id} update_bot_ext error exc={e}")
        return ApiResponse(
            success=False,
            message=f"更新Bot ext失败: {str(e)}",
            error_code=500,
            data=None,
        )


# ==================== Data Init ====================


class DataInitRequest(BaseModel):
    """Request model for triggering bot data initialization."""
    force: bool = False


@router.post("/{bot_id}/data-init", response_model=ApiResponse)
@with_interceptors(
    CollaboratorPermissionInterceptor(
        bot_id="$bot_id",
        owner_id="$owner_id",
    )
)
async def trigger_data_init(
    bot_id: str,
    request: Request,
    body: DataInitRequest = DataInitRequest(),
    owner_id: Optional[str] = Query(None, description="Bot owner workno; defaults to current user"),
    ctx: RequestContext = Depends(get_request_context),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    data_init_service: DataInitServiceProtocol = Injected(DataInitServiceProtocol),
) -> ApiResponse:
    """
    Trigger bot data initialization.

    POST /api/bots/{bot_id}/data-init
    Body: { "force": false }

    - If Bot is ACTIVE: starts async data-init (fire-and-forget), returns {"status": "in_progress"}
    - If Bot is PENDING: marks data_init_status="pending", returns {"status": "pending_init"}
    - force=true: re-runs init even if already completed

    Frontend polls GET /api/bots/by-owner → ext.data_init_status for progress.
    """
    try:
        operator_id = ctx.user_id
        resolved_owner_id = owner_id or operator_id

        if not operator_id or operator_id == "anonymous":
            return ApiResponse(
                success=False,
                message="无法获取认证用户信息",
                error_code=401,
                data=None,
            )

        import asyncio

        force = body.force

        # 获取 entity_id / entity_type
        bot = bot_service.get_bot(bot_id, resolved_owner_id)
        entity_id = bot.get("entity_id") or resolved_owner_id
        entity_type = bot.get("entity_type") or "staff"

        # 保存用户 IAM_TOKEN 到 bot.ext，供异步 data_init 使用
        iam_token = request.cookies.get("IAM_TOKEN") or ""
        logger.info(
            f"bot_id={bot_id} trigger_data_init_api iam_token "
            f"has_iam_token={bool(iam_token)}"
        )
        if iam_token:
            bot_service.update_bot_ext(bot_id, resolved_owner_id, {"iam_token": iam_token})

        # 即发即弃：trigger_init 内部 await 执行，但 ensure_future 让 HTTP 请求立即返回
        asyncio.ensure_future(
            data_init_service.trigger_init(
                bot_id=bot_id,
                owner_id=resolved_owner_id,
                entity_id=entity_id,
                entity_type=entity_type,
                force=force,
            )
        )
        logger.info(
            f"bot_id={bot_id} trigger_data_init_api dispatched owner_id={resolved_owner_id} force={force} mode=async"
        )

        # 即发即弃模式，返回 in_progress 表示已提交
        result = {"status": "in_progress", "message": "数据初始化已提交"}

        return ApiResponse(
            success=True,
            data=result,
        )

    except Exception as e:
        logger.error(f"bot_id={bot_id} trigger_data_init_api error exc={e}", exc_info=True)
        return ApiResponse(
            success=False,
            message=f"触发数据初始化失败: {str(e)}",
            error_code=500,
            data=None,
        )
