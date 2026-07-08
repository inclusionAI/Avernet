"""Quality Task Management Router.

API endpoints for quality task management (evaluation, stress testing, etc.).
"""
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from agentclaw.community.adapters.http.auth.dependencies import (
    get_current_user as _get_current_user,
)
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.core.access.admin_scopes import super_admin
from agentclaw.community.adapters.http.quality.schemas import (
    ApiResponse,
    CreateQualityTaskRequest,
    CreateQualityTaskResponse,
    ListQualityTasksResponse,
    ProcessTaskResponse,
    QualityTaskResponse,
)
from agentclaw.community.api.quality_service import QualityTaskServiceProtocol
from agentclaw.community.api.task_processor_service import TaskProcessorProtocol
from agentclaw.community.core.bot_collaborator.interceptor import (
    CollaboratorPermissionInterceptor,
    PermissionParams,
    with_interceptors,
)
from agentclaw.community.core.quality.repositories import QualityTaskRecord
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger

# Export get_current_user for testing
get_current_user = _get_current_user

logger = get_logger()

router = APIRouter(prefix="/api/quality", tags=["quality"])


# ============================================================================
# 协作者权限提取函数
# ============================================================================
async def extract_from_task_id(id: str, ctx) -> PermissionParams:
    """从 task id 查询 bot_id 和 owner_id.

    Args:
        id: 任务 ID（由拦截器通过表达式注入）
        ctx: 拦截器上下文；``ctx.injector`` 由 ``with_interceptors``
            从 ``request.app.state.injector`` 注入。
    """
    if not id:
        return PermissionParams()

    if ctx.injector is None:
        return PermissionParams()

    try:
        service = ctx.injector.get(QualityTaskServiceProtocol)
    except Exception:
        return PermissionParams()

    try:
        task_id = int(id)
        record = service.get_task_by_id(task_id)
        if not record:
            return PermissionParams()

        return PermissionParams(
            bot_id=record.bot_id,
            owner_id=record.owner_id,
        )
    except (ValueError, Exception):
        return PermissionParams()


def _record_to_response(record: QualityTaskRecord) -> QualityTaskResponse:
    """Convert QualityTaskRecord to response model."""
    return QualityTaskResponse(
        id=record.id,
        uuid=record.uuid,
        task_type=record.task_type,
        biz_type=record.biz_type,
        status=record.status,
        bot_id=record.bot_id,
        owner_id=record.owner_id,
        ext=record.ext,
        operator_id=record.operator_id,
        env=record.env,
        gmt_create=record.gmt_create,
        gmt_modified=record.gmt_modified,
    )


# ============== API Endpoints ==============

@router.get("/tasks", response_model=ListQualityTasksResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$bot_id",
    owner_id="$owner_id",
    persist_audit_log=False,  # 只读操作，不需要审计和锁检查
))
async def list_quality_tasks(
    task_type: str = Query(default="eval", description="任务类型，默认 eval"),
    biz_type: str = Query(
        default="service_bot_single", description="业务类型，默认 service_bot_single"
    ),
    bot_id: str | None = Query(None, description="Bot ID"),
    owner_id: str | None = Query(None, description="Owner ID"),
    page: int = Query(default=1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(default=20, ge=1, le=100, description="每页数量"),
    service: QualityTaskServiceProtocol = Injected(QualityTaskServiceProtocol),
    user: AuthenticatedUser = Depends(get_current_user),
) -> ListQualityTasksResponse:
    """
    查询评测任务列表

    - 根据 task_type、biz_type、bot_id、owner_id 分页查询
    - task_type 和 biz_type 有默认值
    """
    tasks, total = service.list_tasks(
        task_type=task_type,
        biz_type=biz_type,
        bot_id=bot_id,
        owner_id=owner_id,
        page=page,
        page_size=page_size,
    )
    return ListQualityTasksResponse(
        items=[_record_to_response(t) for t in tasks],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/tasks/{id}", response_model=QualityTaskResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    params_extractor=extract_from_task_id,
    extractor_params={"id": "$id"},
    persist_audit_log=False,  # 只读操作，不需要审计和锁检查
))
async def get_quality_task(
    id: int,
    service: QualityTaskServiceProtocol = Injected(QualityTaskServiceProtocol),
    user: AuthenticatedUser = Depends(get_current_user),
) -> QualityTaskResponse:
    """
    根据 ID 查询评测任务详情
    """
    task = service.get_task_by_id(id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return _record_to_response(task)


@router.post("/tasks/create", response_model=CreateQualityTaskResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$request.bot_id",
    owner_id="$request.owner_id",
))
async def create_task(
    request: CreateQualityTaskRequest,
    service: QualityTaskServiceProtocol = Injected(QualityTaskServiceProtocol),
    user: AuthenticatedUser = Depends(get_current_user),
) -> CreateQualityTaskResponse:
    """
    创建单Bot评测任务
    """
    task = service.create_task(
        task_type=request.task_type,
        biz_type=request.biz_type,
        bot_id=request.bot_id,
        owner_id=request.owner_id,
        ext=request.ext,
        operator_id=user.staffId,
    )
    return CreateQualityTaskResponse(
        success=True,
        data=_record_to_response(task),
        message="创建成功",
    )


@router.post(
    "/tasks/{id}/status_for_others",
    response_model=ApiResponse,
    summary="更新任务状态(管理员代操作)",
)
async def update_task_status_for_others(
    id: int,
    status: str = Query(..., description="任务状态: init/env_preparing/env_ready/running/success/failed"),
    ext: Optional[dict[str, Any]] = Body(default=None, description="可选的扩展数据，会合并到现有ext中"),
    service: QualityTaskServiceProtocol = Injected(QualityTaskServiceProtocol),
    user: AuthenticatedUser = Depends(get_current_user),
) -> ApiResponse:
    """
    更新任务状态(管理员代操作)

    仅限 SUPER_ADMIN 中的用户使用，使用任务 owner_id 进行权限校验。
    """
    try:
        user_id = user.staffId
        if not user_id or user_id == "anonymous":
            return ApiResponse(success=False, message="无法获取用户信息", error_code=400, data=None)

        # 权限校验：只有 SUPER_ADMIN 中的用户才能操作
        if user_id not in super_admin():
            logger.warning(f"[update_task_status_for_others] Permission denied: user_id={user_id}")
            return ApiResponse(success=False, message="无权限执行此操作", error_code=403, data=None)

        # 当函数被直接调用（非 FastAPI 路由）时，ext 可能是 Body 对象
        # 需要将其转换为 None
        ext_dict = ext if isinstance(ext, dict) else None

        logger.info(
            f"[update_task_status_for_others] Updating status: task_id={id}, status={status}, operator={user_id}, ext={ext_dict}"
        )

        # 获取任务记录
        record = service.get_task_by_id(id)
        if not record:
            raise HTTPException(status_code=404, detail="Task not found")

        owner_id = record.owner_id
        logger.info(
            f"[update_task_status_for_others] Using owner_id context: task_id={id}, owner_id={owner_id}"
        )

        # 更新状态
        task = service.update_task_status(id, status, ext=ext_dict)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        return ApiResponse(
            success=True,
            data=_record_to_response(task).model_dump(),
            message="状态更新成功",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[update_task_status_for_others] Unexpected error: {e}")
        return ApiResponse(success=False, message=f"更新任务状态失败: {str(e)}", error_code=500, data=None)


@router.post("/tasks/{id}/process", response_model=ProcessTaskResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    params_extractor=extract_from_task_id,
    extractor_params={"id": "$id"},
))
async def process_task(
    id: int,
    processor: TaskProcessorProtocol = Injected(TaskProcessorProtocol),
    user: AuthenticatedUser = Depends(get_current_user),
) -> ProcessTaskResponse:
    """
    推进任务状态到下一阶段

    状态流程: init → env_preparing → env_ready → running → success/failed
    """
    task = await processor.process(id)
    return ProcessTaskResponse(
        success=True,
        data=_record_to_response(task),
        message="状态推进成功",
    )


@router.post(
    "/tasks/{id}/process_for_others",
    response_model=ApiResponse,
    summary="推进任务状态(管理员代操作)",
)
async def process_task_for_others(
    id: int,
    processor: TaskProcessorProtocol = Injected(TaskProcessorProtocol),
    service: QualityTaskServiceProtocol = Injected(QualityTaskServiceProtocol),
    user: AuthenticatedUser = Depends(get_current_user),
) -> ApiResponse:
    """
    推进任务状态到下一阶段(管理员代操作)

    仅限 SUPER_ADMIN 中的用户使用，使用任务 owner_id 进行权限校验。
    状态流程: init → env_preparing → env_ready → running → success/failed
    """
    try:
        user_id = user.staffId
        if not user_id or user_id == "anonymous":
            return ApiResponse(success=False, message="无法获取用户信息", error_code=400, data=None)

        # 权限校验：只有 SUPER_ADMIN 中的用户才能操作
        if user_id not in super_admin():
            logger.warning(f"[process_task_for_others] Permission denied: user_id={user_id}")
            return ApiResponse(success=False, message="无权限执行此操作", error_code=403, data=None)

        logger.info(
            f"[process_task_for_others] Processing task: task_id={id}, operator={user_id}"
        )

        # 获取任务记录
        record = service.get_task_by_id(id)
        if not record:
            raise HTTPException(status_code=404, detail="Task not found")

        owner_id = record.owner_id
        logger.info(
            f"[process_task_for_others] Using owner_id context: task_id={id}, owner_id={owner_id}"
        )

        # 推进任务状态
        task = await processor.process(id)

        return ApiResponse(
            success=True,
            data=_record_to_response(task).model_dump(),
            message="状态推进成功",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[process_task_for_others] Unexpected error: {e}")
        return ApiResponse(success=False, message=f"推进任务状态失败: {str(e)}", error_code=500, data=None)
