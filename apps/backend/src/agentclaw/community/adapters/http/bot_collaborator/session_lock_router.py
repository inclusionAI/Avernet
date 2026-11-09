"""Session-level collaborator lock HTTP endpoints."""
from fastapi import APIRouter, Depends

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.bot_collaborator.converter_lock import _get_user_id, _lock_info
from agentclaw.community.adapters.http.bot_collaborator.schemas import (
    ApiResponse, ReleaseLockResponse, SessionLockInfoResponse, SessionLockRequest,
    SessionLockResponse, SessionReleaseLockRequest,
)
from agentclaw.community.api.collaborator_lock_service import CollaboratorLockServiceProtocol
from agentclaw.community.core.bot_collaborator.services.collaborator_lock_service import LockReleaseDeniedError
from agentclaw.community.core.bot_collaborator.services.collaborator_service import BotNotFoundError
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger

logger = get_logger()
router = APIRouter()


# 会话级锁（coding 应用）— key: session:{bot_id}:{owner_id}:{session_id}
# 与上面的 bot 级协作锁同表（ac_bot_collab_lock）共存，互不影响。
# 权限：调用方需为应用成员（owner 或协作者）。本期不在 OCB 强制（前端控制 +
# 后续 engine chat.send 强制），故此处不挂 CollaboratorPermissionInterceptor。
@router.post("/session-lock/acquire", response_model=ApiResponse, summary="获取会话锁")
async def acquire_session_lock(
    request: SessionLockRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: CollaboratorLockServiceProtocol = Injected(CollaboratorLockServiceProtocol),
) -> ApiResponse:
    """获取会话锁（支持重入）。他人持锁时 acquired=false。"""
    user_id = _get_user_id(user)
    if not user_id:
        return ApiResponse(success=False, message="无法获取用户信息", error_code=400, data=None)
    try:
        lock = service.acquire_session_lock(
            request.bot_id, request.owner_id, request.session_id, user_id
        )
        if lock:
            return ApiResponse(
                success=True,
                data=SessionLockResponse(acquired=True, lock=_lock_info(lock)).model_dump(),
                message="获取会话锁成功",
            )
        return ApiResponse(
            success=True,
            data=SessionLockResponse(acquired=False, lock=None).model_dump(),
            message="会话锁被其他用户持有",
        )
    except BotNotFoundError as e:
        logger.warning("[acquire_session_lock] Bot not found: %s", e)
        return ApiResponse(success=False, message=f"获取会话锁失败: {e}", error_code=404, data=None)
    except Exception as e:
        logger.error(f"[acquire_session_lock] error: {e}")
        return ApiResponse(success=False, message=f"获取会话锁失败: {e}", error_code=500, data=None)


@router.post("/session-lock/release", response_model=ApiResponse, summary="释放会话锁")
async def release_session_lock(
    request: SessionReleaseLockRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: CollaboratorLockServiceProtocol = Injected(CollaboratorLockServiceProtocol),
) -> ApiResponse:
    """释放会话锁。仅持有者可释放（force=true 除外）。"""
    user_id = _get_user_id(user)
    if not user_id:
        return ApiResponse(success=False, message="无法获取用户信息", error_code=400, data=None)
    try:
        success = service.release_session_lock(
            request.bot_id, request.owner_id, request.session_id, user_id, request.force
        )
        return ApiResponse(
            success=True,
            data=ReleaseLockResponse(released=success).model_dump(),
            message="释放会话锁成功" if success else "释放会话锁失败",
        )
    except LockReleaseDeniedError as e:
        return ApiResponse(success=False, message=str(e), error_code=403, data=None)
    except BotNotFoundError as e:
        logger.warning("[release_session_lock] Bot not found: %s", e)
        return ApiResponse(success=False, message=f"释放会话锁失败: {e}", error_code=404, data=None)
    except Exception as e:
        logger.error(f"[release_session_lock] error: {e}")
        return ApiResponse(success=False, message=f"释放会话锁失败: {e}", error_code=500, data=None)


@router.post("/session-lock/steal", response_model=ApiResponse, summary="抢夺会话锁")
async def steal_session_lock(
    request: SessionLockRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: CollaboratorLockServiceProtocol = Injected(CollaboratorLockServiceProtocol),
) -> ApiResponse:
    """抢夺会话锁。强制释放他人锁后获取。"""
    user_id = _get_user_id(user)
    if not user_id:
        return ApiResponse(success=False, message="无法获取用户信息", error_code=400, data=None)
    try:
        lock = service.steal_session_lock(
            request.bot_id, request.owner_id, request.session_id, user_id
        )
        return ApiResponse(
            success=True,
            data=SessionLockResponse(acquired=True, lock=_lock_info(lock)).model_dump(),
            message="抢锁成功",
        )
    except BotNotFoundError as e:
        logger.warning("[steal_session_lock] Bot not found: %s", e)
        return ApiResponse(success=False, message=f"抢会话锁失败: {e}", error_code=404, data=None)
    except Exception as e:
        logger.error(f"[steal_session_lock] error: {e}")
        return ApiResponse(success=False, message=f"抢会话锁失败: {e}", error_code=500, data=None)


@router.get("/session-lock/info", response_model=ApiResponse, summary="获取会话锁状态")
async def get_session_lock_info(
    bot_id: str,
    owner_id: str,
    session_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    service: CollaboratorLockServiceProtocol = Injected(CollaboratorLockServiceProtocol),
) -> ApiResponse:
    """获取会话锁状态（locked / 持有者 / is_mine）。"""
    user_id = _get_user_id(user)
    if not user_id:
        return ApiResponse(success=False, message="无法获取用户信息", error_code=400, data=None)
    try:
        result = service.get_session_lock_info(bot_id, owner_id, session_id, user_id)
        return ApiResponse(
            success=True,
            data=SessionLockInfoResponse(
                locked=result.locked,
                lock=_lock_info(result.lock, result.holder_name) if result.lock else None,
                is_mine=result.is_mine,
            ).model_dump(),
            message="查询成功",
        )
    except BotNotFoundError as e:
        logger.warning("[get_session_lock_info] Bot not found: %s", e)
        return ApiResponse(success=False, message=f"查询会话锁失败: {e}", error_code=404, data=None)
    except Exception as e:
        logger.error(f"[get_session_lock_info] error: {e}")
        return ApiResponse(success=False, message=f"查询会话锁失败: {e}", error_code=500, data=None)
