"""Cron Router — 定时任务中继 API

提供定时任务管理接口：
- GET /api/cron - 获取定时任务列表
- GET /api/cron/status - 获取 Cron 服务状态
- GET /api/cron/running - 获取正在执行的任务列表
- GET /api/cron/{task_id} - 获取单个任务详情
- POST /api/cron - 创建定时任务
- PUT /api/cron/{task_id} - 更新定时任务
- DELETE /api/cron/{task_id} - 删除定时任务
- POST /api/cron/{task_id}/run - 立即执行任务
- GET /api/cron/{task_id}/runs - 获取执行历史

依赖规则：
  OK:  import api.cron.schemas          (同层)
  OK:  import core.cron.dependencies    (DI工厂)
  OK:  import core.cron.errors          (业务异常)
  BAD: import plugins.*            (never import impl directly)

协作者注解：
  所有端点支持协作者权限检查，通过 owner_id 参数区分：
  - owner_id 存在：协作者视角，以 owner 身份操作
  - owner_id 不存在：owner 本人视角，以当前用户身份操作
"""

from fastapi import APIRouter, Depends, Query, Request

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.cron.schemas import ApiResponse, CronListApiResponse
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.api.cron_relay_service import CronRelayServiceProtocol
from agentclaw.community.core.bot_collaborator.interceptor import (
    CollaboratorPermissionInterceptor,
    with_interceptors,
)
from agentclaw.community.core.bot_collaborator.interceptor.extractors import PermissionParams
from agentclaw.community.core.cron.errors import CronRelayError
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger


logger = get_logger()

router = APIRouter(prefix="/api/cron", tags=["cron"])


# =============================================================================
# 辅助函数
# =============================================================================

def _resolve_user_identity(
    owner_id: str | None,
    bot_id: str,
    user: AuthenticatedUser,
    bot_service: BotServiceProtocol,
) -> tuple[str, str]:
    """解析有效的用户身份。

    Args:
        owner_id: Bot 拥有者工号（协作者场景）
        bot_id: Bot ID
        user: 当前登录用户
        bot_service: Bot 服务（用于获取 owner 信息）

    Returns:
        (user_id, nick_name) 元组
    """
    # bot_id="all" 或 owner_id 为空时，使用当前用户身份
    if not owner_id or bot_id == "all":
        return user.staffId, user.nickName or user.staffId

    # 协作者场景：从 bot 信息获取 owner 身份
    try:
        bot = bot_service.get_bot(bot_id, owner_id)
        return owner_id, bot.get("owner_name") or owner_id if bot else owner_id
    except Exception as e:
        logger.warning(f"[_resolve_user_identity] Failed to get bot: {e}")
        return owner_id, owner_id


async def extract_cron_body_params(ctx) -> PermissionParams:
    """从 POST body 中提取 bot_id 和 owner_id 用于权限检查。

    用于 create_cron 等使用 Request body 传递参数的端点。
    Starlette 的 request.json() 会缓存 body，可多次调用。
    """
    request = ctx.route_kwargs.get("request")
    if request is None:
        return PermissionParams(bot_id=None, owner_id=None)
    try:
        body = await request.json()
        return PermissionParams(
            bot_id=body.get("bot_id"),
            owner_id=body.get("owner_id"),
        )
    except Exception:
        return PermissionParams(bot_id=None, owner_id=None)


# =============================================================================
# API Endpoints
# =============================================================================

@router.get("", response_model=CronListApiResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$bot_id",
    owner_id="$owner_id",
    persist_audit_log=False,  # 查询操作不记录操作日志
))
async def list_crons(
    bot_id: str | None = Query("all", description="Bot ID，'all' 表示所有 Bots"),
    owner_id: str | None = Query(None, description="Bot 拥有者工号"),
    user: AuthenticatedUser = Depends(get_current_user),
    service: CronRelayServiceProtocol = Injected(CronRelayServiceProtocol),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> CronListApiResponse:
    """获取定时任务列表（平铺展示）"""
    logger.info(f"[list_crons] user={user.staffId}, bot_id={bot_id}, owner_id={owner_id}")
    try:
        effective_user_id, effective_nick_name = _resolve_user_identity(
            owner_id, bot_id, user, bot_service
        )
        result = await service.list_all_crons(
            user_id=effective_user_id,
            nick_name=effective_nick_name,
            bot_id=bot_id
        )
        return CronListApiResponse(
            success=result.get("success", True),
            data=result.get("data", []),
            message="OK",
            failed_targets=result.get("failed_targets", []),
        )
    except Exception as e:
        logger.error(f"[list_crons] Error: {e}")
        return CronListApiResponse(
            success=False,
            data=[],
            message=str(e),
            error_code=500,
            failed_targets=[],
        )


@router.get("/status", response_model=ApiResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$bot_id",
    owner_id="$owner_id",
    persist_audit_log=False,  # 查询操作不记录操作日志
))
async def get_cron_status(
    bot_id: str = Query(..., description="Bot ID"),
    owner_id: str | None = Query(None, description="Bot 拥有者工号"),
    user: AuthenticatedUser = Depends(get_current_user),
    service: CronRelayServiceProtocol = Injected(CronRelayServiceProtocol),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> ApiResponse:
    """获取 Cron 服务状态"""
    try:
        effective_user_id, effective_nick_name = _resolve_user_identity(
            owner_id, bot_id, user, bot_service
        )
        result = await service.forward_request(
            bot_id=bot_id,
            user_id=effective_user_id,
            nick_name=effective_nick_name,
            method="GET",
            path="/api/cron/status"
        )
        return ApiResponse(
            success=result.get("success", True),
            data=result.get("data"),
            message=result.get("message", "OK"),
            failed_targets=result.get("failed_targets", []),
        )
    except Exception as e:
        logger.error(f"[get_cron_status] Error: {e}")
        return ApiResponse(success=False, message=str(e), error_code=500)


@router.get("/running", response_model=CronListApiResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$bot_id",
    owner_id="$owner_id",
    persist_audit_log=False,  # 查询操作不记录操作日志
))
async def get_running_crons(
    bot_id: str | None = Query("all", description="Bot ID，'all' 表示所有 Bots"),
    owner_id: str | None = Query(None, description="Bot 拥有者工号"),
    user: AuthenticatedUser = Depends(get_current_user),
    service: CronRelayServiceProtocol = Injected(CronRelayServiceProtocol),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> CronListApiResponse:
    """获取正在执行的任务列表"""
    try:
        effective_user_id, effective_nick_name = _resolve_user_identity(
            owner_id, bot_id, user, bot_service
        )
        result = await service.list_running_crons(
            user_id=effective_user_id,
            nick_name=effective_nick_name,
            bot_id=bot_id
        )
        return CronListApiResponse(
            success=True,
            data=result.get("data", []),
            failed_targets=result.get("failed_targets", []),
        )
    except Exception as e:
        logger.error(f"[get_running_crons] Error: {e}")
        return CronListApiResponse(
            success=False,
            data=[],
            message=str(e),
            error_code=500,
            failed_targets=[],
        )


@router.get("/{task_id}", response_model=ApiResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$bot_id",
    owner_id="$owner_id",
    persist_audit_log=False,  # 查询操作不记录操作日志
))
async def get_cron(
    task_id: str,
    bot_id: str = Query(..., description="所属 Bot ID"),
    owner_id: str | None = Query(None, description="Bot 拥有者工号"),
    runtime_stage: str = Query("draft", description="运行态：draft/verify/online"),
    user: AuthenticatedUser = Depends(get_current_user),
    service: CronRelayServiceProtocol = Injected(CronRelayServiceProtocol),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> ApiResponse:
    """获取单个定时任务详情"""
    try:
        effective_user_id, effective_nick_name = _resolve_user_identity(
            owner_id, bot_id, user, bot_service
        )
        result = await service.forward_request(
            bot_id=bot_id,
            user_id=effective_user_id,
            nick_name=effective_nick_name,
            method="GET",
            path=f"/api/cron/{task_id}",
            runtime_stage=runtime_stage,
        )
        return ApiResponse(
            success=result.get("success", True),
            data=result.get("data"),
            message=result.get("message", "OK"),
            failed_targets=result.get("failed_targets", []),
        )
    except CronRelayError as e:
        logger.warning(f"[get_cron] Business error: {e}")
        return ApiResponse(success=False, message=str(e), error_code=e.error_code)
    except Exception as e:
        logger.error(f"[get_cron] Error: {e}")
        return ApiResponse(success=False, message=str(e), error_code=500)


@router.post("", response_model=ApiResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    params_extractor=extract_cron_body_params,
    ))
async def create_cron(
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: CronRelayServiceProtocol = Injected(CronRelayServiceProtocol),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> ApiResponse:
    """创建定时任务"""
    try:
        data = await request.json()
        bot_id = data.get("bot_id")
        owner_id = data.get("owner_id")
        cron_name = data.get("name")

        if not bot_id:
            return ApiResponse(success=False, message="缺少 bot_id", error_code=400)

        effective_user_id, effective_nick_name = _resolve_user_identity(
            owner_id, bot_id, user, bot_service
        )

        logger.info(f"[create_cron] user={user.staffId}, bot_id={bot_id}, name={cron_name}, owner_id={owner_id}")

        # 转换请求格式（前端格式 → Adapter 格式）
        adapter_body = {
            "name": data.get("name"),
            "schedule": data.get("schedule"),
            "command": data.get("command"),
            "timezone": data.get("timezone", "Asia/Shanghai"),
            "enabled": data.get("enabled", True),
            "timeout_secs": data.get("timeout_secs", 86400),
        }
        if owner_id:
            adapter_body["owner_id"] = owner_id
        if data.get("kind"):
            adapter_body["kind"] = data["kind"]
        if data.get("model"):
            adapter_body["model"] = data.get("model")
        if data.get("notify"):
            adapter_body["notify"] = data.get("notify")

        result = await service.forward_request(
            bot_id=bot_id,
            user_id=effective_user_id,
            nick_name=effective_nick_name,
            method="POST",
            path="/api/cron",
            body=adapter_body
        )

        return ApiResponse(
            success=result.get("success", True),
            data=result.get("data"),
            message=result.get("message", "OK"),
            failed_targets=result.get("failed_targets", []),
        )
    except Exception as e:
        logger.error(f"[create_cron] Error: {e}")
        return ApiResponse(success=False, message=str(e), error_code=500)


@router.put("/{task_id}", response_model=ApiResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$bot_id",
    owner_id="$owner_id",
    ))
async def update_cron(
    task_id: str,
    request: Request,
    bot_id: str = Query(..., description="所属 Bot ID"),
    owner_id: str | None = Query(None, description="Bot 拥有者工号"),
    runtime_stage: str = Query("draft", description="运行态：draft/verify/online"),
    user: AuthenticatedUser = Depends(get_current_user),
    service: CronRelayServiceProtocol = Injected(CronRelayServiceProtocol),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> ApiResponse:
    """更新定时任务"""
    logger.info(f"[update_cron] user={user.staffId}, bot_id={bot_id}, task_id={task_id}")
    try:
        data = await request.json()
        effective_user_id, effective_nick_name = _resolve_user_identity(
            owner_id, bot_id, user, bot_service
        )

        result = await service.forward_request(
            bot_id=bot_id,
            user_id=effective_user_id,
            nick_name=effective_nick_name,
            method="PUT",
            path=f"/api/cron/{task_id}",
            body=data,
            runtime_stage=runtime_stage,
        )

        return ApiResponse(
            success=result.get("success", True),
            data=result.get("data"),
            message=result.get("message", "OK"),
            failed_targets=result.get("failed_targets", []),
        )
    except CronRelayError as e:
        logger.warning(f"[update_cron] Business error: {e}")
        return ApiResponse(success=False, message=str(e), error_code=e.error_code)
    except Exception as e:
        logger.error(f"[update_cron] Error: {e}")
        return ApiResponse(success=False, message=str(e), error_code=500)


@router.delete("/{task_id}", response_model=ApiResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$bot_id",
    owner_id="$owner_id",
    ))
async def delete_cron(
    task_id: str,
    bot_id: str = Query(..., description="所属 Bot ID"),
    owner_id: str | None = Query(None, description="Bot 拥有者工号"),
    runtime_stage: str = Query("draft", description="运行态：draft/verify/online"),
    user: AuthenticatedUser = Depends(get_current_user),
    service: CronRelayServiceProtocol = Injected(CronRelayServiceProtocol),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> ApiResponse:
    """删除定时任务"""
    logger.info(f"[delete_cron] user={user.staffId}, bot_id={bot_id}, task_id={task_id}")
    try:
        effective_user_id, effective_nick_name = _resolve_user_identity(
            owner_id, bot_id, user, bot_service
        )

        result = await service.forward_request(
            bot_id=bot_id,
            user_id=effective_user_id,
            nick_name=effective_nick_name,
            method="DELETE",
            path=f"/api/cron/{task_id}",
            runtime_stage=runtime_stage,
        )

        return ApiResponse(
            success=result.get("success", True),
            message=result.get("message", "Task deleted"),
            failed_targets=result.get("failed_targets", []),
        )
    except CronRelayError as e:
        logger.warning(f"[delete_cron] Business error: {e}")
        return ApiResponse(success=False, message=str(e), error_code=e.error_code)
    except Exception as e:
        logger.error(f"[delete_cron] Error: {e}")
        return ApiResponse(success=False, message=str(e), error_code=500)


@router.post("/{task_id}/run", response_model=ApiResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$bot_id",
    owner_id="$owner_id",
    ))
async def run_cron(
    task_id: str,
    bot_id: str = Query(..., description="所属 Bot ID"),
    force: bool = Query(False, description="是否强制执行"),
    owner_id: str | None = Query(None, description="Bot 拥有者工号"),
    runtime_stage: str = Query("draft", description="运行态：draft/verify/online"),
    user: AuthenticatedUser = Depends(get_current_user),
    service: CronRelayServiceProtocol = Injected(CronRelayServiceProtocol),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> ApiResponse:
    """立即执行定时任务"""
    logger.info(f"[run_cron] user={user.staffId}, bot_id={bot_id}, task_id={task_id}, force={force}")
    try:
        effective_user_id, effective_nick_name = _resolve_user_identity(
            owner_id, bot_id, user, bot_service
        )

        result = await service.forward_request(
            bot_id=bot_id,
            user_id=effective_user_id,
            nick_name=effective_nick_name,
            method="POST",
            path=f"/api/cron/{task_id}/run",
            params={"force": force},
            runtime_stage=runtime_stage,
        )

        return ApiResponse(
            success=result.get("success", True),
            data=result.get("data"),
            message=result.get("message", "OK"),
            failed_targets=result.get("failed_targets", []),
        )
    except CronRelayError as e:
        logger.warning(f"[run_cron] Business error: {e}")
        return ApiResponse(success=False, message=str(e), error_code=e.error_code)
    except Exception as e:
        logger.error(f"[run_cron] Error: {e}")
        return ApiResponse(success=False, message=str(e), error_code=500)


@router.get("/{task_id}/runs", response_model=ApiResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$bot_id",
    owner_id="$owner_id",
    persist_audit_log=False,  # 查询操作不记录操作日志
))
async def get_cron_runs(
    task_id: str,
    bot_id: str = Query(..., description="所属 Bot ID"),
    limit: int = Query(20, description="返回记录数量上限"),
    owner_id: str | None = Query(None, description="Bot 拥有者工号"),
    runtime_stage: str = Query("draft", description="运行态：draft/verify/online"),
    user: AuthenticatedUser = Depends(get_current_user),
    service: CronRelayServiceProtocol = Injected(CronRelayServiceProtocol),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> ApiResponse:
    """获取任务执行历史"""
    try:
        effective_user_id, effective_nick_name = _resolve_user_identity(
            owner_id, bot_id, user, bot_service
        )

        result = await service.forward_request(
            bot_id=bot_id,
            user_id=effective_user_id,
            nick_name=effective_nick_name,
            method="GET",
            path=f"/api/cron/{task_id}/runs",
            params={"limit": limit},
            runtime_stage=runtime_stage,
        )

        return ApiResponse(
            success=result.get("success", True),
            data=result.get("data"),
            message=result.get("message", "OK"),
            failed_targets=result.get("failed_targets", []),
        )
    except CronRelayError as e:
        logger.warning(f"[get_cron_runs] Business error: {e}")
        return ApiResponse(success=False, message=str(e), error_code=e.error_code)
    except Exception as e:
        logger.error(f"[get_cron_runs] Error: {e}")
        return ApiResponse(success=False, message=str(e), error_code=500)
