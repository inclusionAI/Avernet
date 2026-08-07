"""Bot 协作者管理 API 路由。

根据 README.md 分层规范：
- router 只处理 HTTP 协议关注点：路径参数、Query、Body 解析，HTTP 错误码映射
- 调用 core/bot_collaborator/services/ 中的业务逻辑
- Request/Response 类型来自同级 schemas.py
"""
from fastapi import APIRouter, Depends

from agentclaw.community.adapters.http.bot_collaborator.schemas import (
    ApiResponse,
    AddCollaboratorRequest,
    AddCollaboratorResponse,
    CollaboratorInfo,
    ListCollaboratorsResponse,
    UpdateCollaboratorRequest,
    UpdateCollaboratorResponse,
    RemoveCollaboratorRequest,
    RemoveCollaboratorResponse,
    LeaveCollaborationRequest,
    LeaveCollaborationResponse,
    CheckPermissionRequest,
    CheckPermissionResponse,
    AcquireLockRequest,
    AcquireLockResponse,
    LockInfo,
    ReleaseLockRequest,
    ReleaseLockResponse,
    GetLockInfoResponse,
    StealLockRequest,
    StealLockResponse,
    SessionLockRequest,
    SessionReleaseLockRequest,
    SessionLockResponse,
    SessionLockInfoResponse,
)
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.core.access.admin_scopes import collaborator_admin
from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_collaborator.interceptor import (
    CollaboratorPermissionInterceptor,
    with_interceptors,
)
from agentclaw.community.core.bot_collaborator.services.collaborator_service import (
    CollaboratorServiceError,
    PermissionDeniedError,
    CollaboratorNotFoundError,
    CollaboratorAlreadyExistsError,
    CannotRemoveSelfError,
    BotNotFoundError,
    BotNotServiceTypeError,
)
from agentclaw.community.core.bot_collaborator.services.collaborator_lock_service import (
    LockNotHeldError,
    LockReleaseDeniedError,
)
from agentclaw.community.api.collaborator_service import CollaboratorServiceProtocol
from agentclaw.community.api.collaborator_lock_service import CollaboratorLockServiceProtocol
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger

logger = get_logger()

router = APIRouter(prefix="/api/bot/collaborator", tags=["bot-collaborator"])


@router.post(
    "/add",
    response_model=ApiResponse,
    summary="添加协作者",
)
async def add_collaborator(
    request: AddCollaboratorRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: CollaboratorServiceProtocol = Injected(CollaboratorServiceProtocol),
    lock_service: CollaboratorLockServiceProtocol = Injected(CollaboratorLockServiceProtocol),
) -> ApiResponse:
    """添加协作者.

    POST /api/bot/collaborator/add
    Body: {
        "bot_id": "xxx",
        "owner_id": "xxx",
        "user_id": "xxx",
        "user_name": "张三",  // 可选
        "role": "admin"  // 可选，"admin" 或 "member"，默认 "admin"
    }

    权限要求：ADMIN 或 OWNER

    锁机制：尝试获取编辑锁，被他人持有时返回 423

    Returns:
        ApiResponse: 包含添加的协作者信息
    """
    try:
        operator_id = user.staffId

        if not operator_id or operator_id == "anonymous":
            return ApiResponse(success=False, message="无法获取用户信息", error_code=400, data=None)

        logger.info(
            "[add_collaborator] Adding: bot_id=%s, user_id=%s, role=%s, operator=%s",
            request.bot_id, request.user_id, request.role, operator_id
        )

        # 尝试获取锁
        lock = lock_service.acquire_lock(request.bot_id, request.owner_id, operator_id)
        if lock:
            logger.info(
                "[add_collaborator] Acquired lock: bot_id=%s, operator=%s",
                request.bot_id, operator_id
            )
        else:
            # 获取失败，查询锁持有者
            lock_info = lock_service.get_lock_info(request.bot_id, request.owner_id, operator_id)
            if lock_info.lock and lock_info.lock.holder_user_id != operator_id:
                logger.warning(
                    "[add_collaborator] Lock held by another user: bot_id=%s, holder=%s, requester=%s",
                    request.bot_id, lock_info.lock.holder_user_id, operator_id
                )
                return ApiResponse(
                    success=False,
                    message="Bot 正在被其他用户编辑",
                    error_code=423,
                    data={
                        "locked": True,
                        "lock": {
                            "holder_user_id": lock_info.lock.holder_user_id,
                            "holder_name": lock_info.holder_name,
                        },
                    },
                )
            # 并发冲突或其他情况
            return ApiResponse(
                success=False,
                message="获取编辑锁失败，请稍后重试",
                error_code=423,
                data={"locked": False, "lock": None},
            )

        record = service.add_collaborator(
            bot_id=request.bot_id,
            owner_id=request.owner_id,
            user_id=request.user_id,
            operator_id=operator_id,
            user_name=request.user_name,
            role=request.role,
        )

        return ApiResponse(
            success=True,
            data=AddCollaboratorResponse(
                id=record.id,
                bot_pk=record.bot_pk,
                bot_id=record.bot_id,
                owner_id=record.owner_id,
                user_id=record.user_id,
                user_name=record.user_name,
                role=record.role,
                operator_id=record.operator_id,
                gmt_create=record.gmt_create,
            ).model_dump(),
            message="协作者添加成功",
        )

    except BotNotFoundError as e:
        logger.warning(f"[add_collaborator] Bot not found: {e}")
        return ApiResponse(success=False, message=str(e), error_code=404, data=None)

    except BotNotServiceTypeError as e:
        logger.warning(f"[add_collaborator] Bot not service type: {e}")
        return ApiResponse(success=False, message=str(e), error_code=400, data=None)

    except PermissionDeniedError as e:
        logger.warning(f"[add_collaborator] Permission denied: {e}")
        return ApiResponse(success=False, message=str(e), error_code=403, data=None)

    except CollaboratorAlreadyExistsError as e:
        logger.warning(f"[add_collaborator] Already exists: {e}")
        return ApiResponse(success=False, message=str(e), error_code=409, data=None)

    except CollaboratorServiceError as e:
        logger.error(f"[add_collaborator] Service error: {e}")
        return ApiResponse(success=False, message=str(e), error_code=500, data=None)

    except Exception as e:
        logger.error(f"[add_collaborator] Unexpected error: {e}")
        return ApiResponse(success=False, message=f"添加协作者失败: {str(e)}", error_code=500, data=None)


@router.post(
    "/add_for_others",
    response_model=ApiResponse,
    summary="代他人添加协作者",
)
async def add_collaborator_for_others(
    request: AddCollaboratorRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: CollaboratorServiceProtocol = Injected(CollaboratorServiceProtocol),
) -> ApiResponse:
    """代他人添加协作者（operator_id 使用 owner_id）.

    POST /api/bot/collaborator/add_for_others
    Body: {
        "bot_id": "xxx",
        "owner_id": "xxx",
        "user_id": "xxx",
        "user_name": "张三",  // 可选
        "role": "admin"  // 可选，"admin" 或 "member"，默认 "admin"
    }

    与 add_collaborator 的区别：operator_id 使用 request.owner_id 而非当前用户

    Returns:
        ApiResponse: 包含添加的协作者信息
    """
    try:
        user_id = user.staffId

        if not user_id or user_id == "anonymous":
            return ApiResponse(success=False, message="无法获取用户信息", error_code=400, data=None)

        if user_id not in collaborator_admin():
            logger.warning(
                "[add_collaborator_for_others] Permission denied: user_id=%s not in whitelist",
                user_id
            )
            return ApiResponse(success=False, message="无权限调用此接口", error_code=403, data=None)

        logger.info(
            "[add_collaborator_for_others] Adding: bot_id=%s, user_id=%s, role=%s, operator=%s (current_user=%s)",
            request.bot_id, request.user_id, request.role, request.owner_id, user_id
        )

        record = service.add_collaborator(
            bot_id=request.bot_id,
            owner_id=request.owner_id,
            user_id=request.user_id,
            operator_id=request.owner_id,  # 使用 owner_id 作为 operator_id
            user_name=request.user_name,
            role=request.role,
        )

        return ApiResponse(
            success=True,
            data=AddCollaboratorResponse(
                id=record.id,
                bot_pk=record.bot_pk,
                bot_id=record.bot_id,
                owner_id=record.owner_id,
                user_id=record.user_id,
                user_name=record.user_name,
                role=record.role,
                operator_id=record.operator_id,
                gmt_create=record.gmt_create,
            ).model_dump(),
            message="协作者添加成功",
        )

    except BotNotFoundError as e:
        logger.warning(f"[add_collaborator_for_others] Bot not found: {e}")
        return ApiResponse(success=False, message=str(e), error_code=404, data=None)

    except BotNotServiceTypeError as e:
        logger.warning(f"[add_collaborator_for_others] Bot not service type: {e}")
        return ApiResponse(success=False, message=str(e), error_code=400, data=None)

    except PermissionDeniedError as e:
        logger.warning(f"[add_collaborator_for_others] Permission denied: {e}")
        return ApiResponse(success=False, message=str(e), error_code=403, data=None)

    except CollaboratorAlreadyExistsError as e:
        logger.warning(f"[add_collaborator_for_others] Already exists: {e}")
        return ApiResponse(success=False, message=str(e), error_code=409, data=None)

    except CollaboratorServiceError as e:
        logger.error(f"[add_collaborator_for_others] Service error: {e}")
        return ApiResponse(success=False, message=str(e), error_code=500, data=None)

    except Exception as e:
        logger.error(f"[add_collaborator_for_others] Unexpected error: {e}")
        return ApiResponse(success=False, message=f"添加协作者失败: {str(e)}", error_code=500, data=None)


@router.get(
    "/list",
    response_model=ApiResponse,
    summary="获取协作者列表",
)
async def list_collaborators(
    bot_id: str,
    owner_id: str,
    role: str = None,
    user: AuthenticatedUser = Depends(get_current_user),
    service: CollaboratorServiceProtocol = Injected(CollaboratorServiceProtocol),
) -> ApiResponse:
    """获取 Bot 的协作者列表.

    GET /api/bot/collaborator/list?bot_id=xxx&owner_id=xxx&role=admin

    权限要求：MEMBER 或更高

    Returns:
        ApiResponse: 包含协作者列表
    """
    try:
        user_id = user.staffId

        if not user_id or user_id == "anonymous":
            return ApiResponse(success=False, message="无法获取用户信息", error_code=400, data=None)

        logger.info(
            "[list_collaborators] Listing: bot_id=%s, owner_id=%s, user=%s",
            bot_id, owner_id, user_id
        )

        records = service.list_collaborators(
            bot_id=bot_id,
            owner_id=owner_id,
            user_id=user_id,
            role=role,
        )

        collaborators = [
            CollaboratorInfo(
                id=r.id,
                bot_pk=r.bot_pk,
                bot_id=r.bot_id,
                owner_id=r.owner_id,
                user_id=r.user_id,
                user_name=r.user_name,
                role=r.role,
                operator_id=r.operator_id,
                gmt_create=r.gmt_create,
                gmt_modified=r.gmt_modified,
            )
            for r in records
        ]

        return ApiResponse(
            success=True,
            data=ListCollaboratorsResponse(collaborators=collaborators).model_dump(),
            message="查询成功",
        )

    except BotNotFoundError as e:
        logger.warning(f"[list_collaborators] Bot not found: {e}")
        return ApiResponse(success=False, message=str(e), error_code=404, data=None)

    except PermissionDeniedError as e:
        logger.warning(f"[list_collaborators] Permission denied: {e}")
        return ApiResponse(success=False, message=str(e), error_code=403, data=None)

    except CollaboratorServiceError as e:
        logger.error(f"[list_collaborators] Service error: {e}")
        return ApiResponse(success=False, message=str(e), error_code=500, data=None)

    except Exception as e:
        logger.error(f"[list_collaborators] Unexpected error: {e}")
        return ApiResponse(success=False, message=f"查询协作者失败: {str(e)}", error_code=500, data=None)


@router.post(
    "/update",
    response_model=ApiResponse,
    summary="更新协作者",
)
async def update_collaborator(
    request: UpdateCollaboratorRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: CollaboratorServiceProtocol = Injected(CollaboratorServiceProtocol),
) -> ApiResponse:
    """更新协作者信息.

    POST /api/bot/collaborator/update
    Body: {
        "id": 1,
        "user_id": "xxx",  // 可选
        "user_name": "张三",  // 可选
        "role": "admin"  // 可选
    }

    权限要求：ADMIN 或 OWNER

    Returns:
        ApiResponse: 包含更新后的协作者信息
    """
    try:
        operator_id = user.staffId

        if not operator_id or operator_id == "anonymous":
            return ApiResponse(success=False, message="无法获取用户信息", error_code=400, data=None)

        logger.info(
            "[update_collaborator] Updating: id=%s, operator=%s",
            request.id, operator_id
        )

        record = service.update_collaborator(
            collaborator_id=request.id,
            operator_id=operator_id,
            user_id=request.user_id,
            user_name=request.user_name,
            role=request.role,
        )

        return ApiResponse(
            success=True,
            data=UpdateCollaboratorResponse(
                id=record.id,
                bot_pk=record.bot_pk,
                bot_id=record.bot_id,
                owner_id=record.owner_id,
                user_id=record.user_id,
                user_name=record.user_name,
                role=record.role,
                operator_id=record.operator_id,
                gmt_create=record.gmt_create,
                gmt_modified=record.gmt_modified,
            ).model_dump(),
            message="协作者更新成功",
        )

    except CollaboratorNotFoundError as e:
        logger.warning(f"[update_collaborator] Collaborator not found: {e}")
        return ApiResponse(success=False, message=str(e), error_code=404, data=None)

    except PermissionDeniedError as e:
        logger.warning(f"[update_collaborator] Permission denied: {e}")
        return ApiResponse(success=False, message=str(e), error_code=403, data=None)

    except CollaboratorAlreadyExistsError as e:
        logger.warning(f"[update_collaborator] Already exists: {e}")
        return ApiResponse(success=False, message=str(e), error_code=409, data=None)

    except CollaboratorServiceError as e:
        logger.error(f"[update_collaborator] Service error: {e}")
        return ApiResponse(success=False, message=str(e), error_code=500, data=None)

    except Exception as e:
        logger.error(f"[update_collaborator] Unexpected error: {e}")
        return ApiResponse(success=False, message=f"更新协作者失败: {str(e)}", error_code=500, data=None)


@router.post(
    "/remove",
    response_model=ApiResponse,
    summary="移除协作者",
)
async def remove_collaborator(
    request: RemoveCollaboratorRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: CollaboratorServiceProtocol = Injected(CollaboratorServiceProtocol),
) -> ApiResponse:
    """移除协作者.

    POST /api/bot/collaborator/remove
    Body: {
        "id": 1
    }

    权限要求：ADMIN 或 OWNER
    注意：不能移除自己，除非是 owner

    Returns:
        ApiResponse: 包含删除结果
    """
    try:
        operator_id = user.staffId

        if not operator_id or operator_id == "anonymous":
            return ApiResponse(success=False, message="无法获取用户信息", error_code=400, data=None)

        logger.info(
            "[remove_collaborator] Removing: id=%s, operator=%s",
            request.id, operator_id
        )

        success = service.remove_collaborator(
            collaborator_id=request.id,
            operator_id=operator_id,
        )

        return ApiResponse(
            success=True,
            data=RemoveCollaboratorResponse(deleted=success).model_dump(),
            message="协作者移除成功",
        )

    except CollaboratorNotFoundError as e:
        logger.warning(f"[remove_collaborator] Collaborator not found: {e}")
        return ApiResponse(success=False, message=str(e), error_code=404, data=None)

    except PermissionDeniedError as e:
        logger.warning(f"[remove_collaborator] Permission denied: {e}")
        return ApiResponse(success=False, message=str(e), error_code=403, data=None)

    except CannotRemoveSelfError as e:
        logger.warning(f"[remove_collaborator] Cannot remove self: {e}")
        return ApiResponse(success=False, message=str(e), error_code=400, data=None)

    except CollaboratorServiceError as e:
        logger.error(f"[remove_collaborator] Service error: {e}")
        return ApiResponse(success=False, message=str(e), error_code=500, data=None)

    except Exception as e:
        logger.error(f"[remove_collaborator] Unexpected error: {e}")
        return ApiResponse(success=False, message=f"移除协作者失败: {str(e)}", error_code=500, data=None)


@router.post(
    "/leave",
    response_model=ApiResponse,
    summary="退出协作",
)
async def leave_collaboration(
    request: LeaveCollaborationRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: CollaboratorServiceProtocol = Injected(CollaboratorServiceProtocol),
) -> ApiResponse:
    """当前登录成员主动退出 Bot 协作。

    POST /api/bot/collaborator/leave
    Body: {
        "bot_id": "xxx",
        "owner_id": "xxx"
    }

    权限要求：当前登录用户必须是该 Bot 的协作者。
    注意：该接口只允许成员删除自己的协作者记录，不支持 owner 退出自己的 Bot，
    也不支持代其他成员退出。

    Returns:
        ApiResponse: 包含退出结果
    """
    try:
        user_id = user.staffId

        if not user_id or user_id == "anonymous":
            return ApiResponse(success=False, message="无法获取用户信息", error_code=400, data=None)

        logger.info(
            "[leave_collaboration] Leaving: bot_id=%s, owner_id=%s, user=%s",
            request.bot_id, request.owner_id, user_id
        )

        success = service.leave_collaboration(
            bot_id=request.bot_id,
            owner_id=request.owner_id,
            user_id=user_id,
        )

        return ApiResponse(
            success=True,
            data=LeaveCollaborationResponse(deleted=success).model_dump(),
            message="已退出协作",
        )

    except BotNotFoundError as e:
        logger.warning(f"[leave_collaboration] Bot not found: {e}")
        return ApiResponse(success=False, message=str(e), error_code=404, data=None)

    except CollaboratorNotFoundError as e:
        logger.warning(f"[leave_collaboration] Collaborator not found: {e}")
        return ApiResponse(success=False, message=str(e), error_code=404, data=None)

    except CollaboratorServiceError as e:
        logger.error(f"[leave_collaboration] Service error: {e}")
        return ApiResponse(success=False, message=str(e), error_code=500, data=None)

    except Exception as e:
        logger.error(f"[leave_collaboration] Unexpected error: {e}")
        return ApiResponse(success=False, message=f"退出协作失败: {str(e)}", error_code=500, data=None)


@router.post(
    "/check_permission",
    response_model=ApiResponse,
    summary="检查用户权限",
)
async def check_collaborator_permission(
    request: CheckPermissionRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: CollaboratorServiceProtocol = Injected(CollaboratorServiceProtocol),
) -> ApiResponse:
    """检查用户在 Bot 中的协作权限。

    POST /api/bot/collaborator/check_permission
    Body: {
        "bot_id": "xxx",
        "owner_id": "xxx",
        "user_id": "xxx",  // 可选，不传则使用当前登录用户
        "required_level": "ADMIN"  // NONE/MEMBER/ADMIN/OWNER，默认 ADMIN
    }

    Returns:
        ApiResponse: 包含权限检查结果
    """
    try:
        operator_id = user.staffId

        if not operator_id or operator_id == "anonymous":
            return ApiResponse(success=False, message="无法获取用户信息", error_code=400, data=None)

        # 如果未传 user_id，使用当前登录用户
        check_user_id = request.user_id if request.user_id else operator_id

        # 解析权限级别
        try:
            required_level = PermissionLevel[request.required_level.upper()]
        except KeyError:
            return ApiResponse(
                success=False,
                message=f"无效的权限级别: {request.required_level}",
                error_code=400,
                data=None,
            )

        logger.info(
            "[check_collaborator_permission] Checking: bot_id=%s, user_id=%s, required=%s",
            request.bot_id, check_user_id, request.required_level
        )

        result = service.check_collaborator_permission(
            bot_id=request.bot_id,
            owner_id=request.owner_id,
            user_id=check_user_id,
            required_level=required_level,
        )

        return ApiResponse(
            success=True,
            data=CheckPermissionResponse(
                has_permission=result["has_permission"],
                level=result["level"],
                level_value=result["level_value"],
            ).model_dump(),
            message="权限检查完成",
        )

    except BotNotFoundError as e:
        logger.warning(f"[check_permission] Bot not found: {e}")
        return ApiResponse(success=False, message=str(e), error_code=404, data=None)

    except CollaboratorServiceError as e:
        logger.error(f"[check_permission] Service error: {e}")
        return ApiResponse(success=False, message=str(e), error_code=500, data=None)

    except Exception as e:
        logger.error(f"[check_permission] Unexpected error: {e}")
        return ApiResponse(success=False, message=f"权限检查失败: {str(e)}", error_code=500, data=None)


# ============================================================================
# 协作锁 API
# ============================================================================

def _get_user_id(user: AuthenticatedUser) -> str | None:
    """获取用户ID，匿名用户返回None。"""
    user_id = user.staffId
    return user_id if user_id and user_id != "anonymous" else None


def _lock_info(lock, holder_name: str | None = None) -> LockInfo:
    """构建LockInfo对象。"""
    return LockInfo(
        id=lock.id,
        lock_key=lock.lock_key,
        holder_user_id=lock.holder_user_id,
        holder_name=holder_name,
        gmt_create=lock.gmt_create,
    )


@router.post("/lock/acquire", response_model=ApiResponse, summary="获取协作锁")
async def acquire_lock(
    request: AcquireLockRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: CollaboratorLockServiceProtocol = Injected(CollaboratorLockServiceProtocol),
) -> ApiResponse:
    """获取协作锁，支持重入。"""
    user_id = _get_user_id(user)
    if not user_id:
        return ApiResponse(success=False, message="无法获取用户信息", error_code=400, data=None)

    try:
        lock = service.acquire_lock(request.bot_id, request.owner_id, user_id)
        if lock:
            return ApiResponse(
                success=True,
                data=AcquireLockResponse(acquired=True, lock=_lock_info(lock)).model_dump(),
                message="获取锁成功",
            )
        return ApiResponse(
            success=True,
            data=AcquireLockResponse(acquired=False, lock=None).model_dump(),
            message="锁被其他用户持有",
        )
    except Exception as e:
        logger.error(f"[acquire_lock] error: {e}")
        return ApiResponse(success=False, message=f"获取锁失败: {e}", error_code=500, data=None)


@router.post("/lock/release", response_model=ApiResponse, summary="释放协作锁")
async def release_lock(
    request: ReleaseLockRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: CollaboratorLockServiceProtocol = Injected(CollaboratorLockServiceProtocol),
) -> ApiResponse:
    """释放协作锁，force=true可强制释放。"""
    user_id = _get_user_id(user)
    if not user_id:
        return ApiResponse(success=False, message="无法获取用户信息", error_code=400, data=None)

    try:
        success = service.release_lock(request.bot_id, request.owner_id, user_id, request.force)
        return ApiResponse(
            success=True,
            data=ReleaseLockResponse(released=success).model_dump(),
            message="释放锁成功" if success else "释放锁失败",
        )
    except LockNotHeldError as e:
        return ApiResponse(success=False, message=str(e), error_code=404, data=None)
    except LockReleaseDeniedError as e:
        return ApiResponse(success=False, message=str(e), error_code=403, data=None)
    except Exception as e:
        logger.error(f"[release_lock] error: {e}")
        return ApiResponse(success=False, message=f"释放锁失败: {e}", error_code=500, data=None)


@router.post("/lock/steal", response_model=ApiResponse, summary="抢夺协作锁")
@with_interceptors(CollaboratorPermissionInterceptor(
    required_level=PermissionLevel.ADMIN,
    bot_id="$request.bot_id",
    owner_id="$request.owner_id",
    skip_lock_check=True,  # 跳过锁检查，因为抢锁就是要获取他人持有的锁
))
async def steal_lock(
    request: StealLockRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: CollaboratorLockServiceProtocol = Injected(CollaboratorLockServiceProtocol),
) -> ApiResponse:
    """抢夺协作锁。

    强制获取锁，无论当前是否被其他用户持有。
    需要 ADMIN 权限或 Owner 身份。

    权限校验由 CollaboratorPermissionInterceptor 完成：
    - Owner 无条件抢锁
    - ADMIN 协作者可抢锁
    - MEMBER 协作者返回 403
    - 非协作者返回 403
    """
    user_id = _get_user_id(user)
    if not user_id:
        return ApiResponse(success=False, message="无法获取用户信息", error_code=400, data=None)

    try:
        lock = service.steal_lock(request.bot_id, request.owner_id, user_id)
        return ApiResponse(
            success=True,
            data=StealLockResponse(
                stolen=True,
                lock=_lock_info(lock),
            ).model_dump(),
            message="抢锁成功",
        )
    except Exception as e:
        logger.error("[steal_lock] Failed to steal lock: %s", e)
        return ApiResponse(success=False, message=f"抢锁失败: {e}", error_code=500, data=None)


@router.get("/lock/info", response_model=ApiResponse, summary="获取锁信息")
async def get_lock_info(
    bot_id: str,
    owner_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    lock_service: CollaboratorLockServiceProtocol = Injected(CollaboratorLockServiceProtocol),
) -> ApiResponse:
    """获取Bot锁信息。"""
    user_id = _get_user_id(user)
    if not user_id:
        return ApiResponse(success=False, message="无法获取用户信息", error_code=400, data=None)

    try:
        result = lock_service.get_lock_info(bot_id, owner_id, user_id)
        # need_lock: 只有 owner 且没有协作者时才不需要锁
        need_lock = not (user_id == owner_id and not result.has_collaborators)
        if result.lock:
            return ApiResponse(
                success=True,
                data=GetLockInfoResponse(
                    locked=True,
                    lock=_lock_info(result.lock, result.holder_name),
                    has_collaborators=result.has_collaborators,
                    is_owner=result.is_owner,
                    need_lock=need_lock,
                ).model_dump(),
                message="查询成功",
            )
        return ApiResponse(
            success=True,
            data=GetLockInfoResponse(
                locked=False,
                lock=None,
                has_collaborators=result.has_collaborators,
                is_owner=result.is_owner,
                need_lock=need_lock,
            ).model_dump(),
            message="Bot 未被锁定",
        )
    except Exception as e:
        logger.error(f"[get_lock_info] error: {e}")
        return ApiResponse(success=False, message=f"查询锁信息失败: {e}", error_code=500, data=None)


# ============================================================================
# 会话级锁（coding 应用）— key: session:{bot_id}:{owner_id}:{session_id}
# 与上面的 bot 级协作锁同表（ac_bot_collab_lock）共存，互不影响。
# 权限：调用方需为应用成员（owner 或协作者）。本期不在 OCB 强制（前端控制 +
# 后续 engine chat.send 强制），故此处不挂 CollaboratorPermissionInterceptor。
# ============================================================================
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
    except Exception as e:
        logger.error(f"[get_session_lock_info] error: {e}")
        return ApiResponse(success=False, message=f"查询会话锁失败: {e}", error_code=500, data=None)
