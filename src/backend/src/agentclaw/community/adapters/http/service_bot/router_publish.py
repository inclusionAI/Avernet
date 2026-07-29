"""Bot 发布流程 API 接口。

根据 README.md 分层规范：
- router 只处理 HTTP 协议关注点：路径参数、Query、Body 解析，HTTP 错误码映射
- 调用 core/service_bot/services/ 中的业务逻辑
- Request/Response 类型来自同级 schemas.py
"""
import json

from fastapi import APIRouter, Depends, Request

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.core.access.admin_scopes import super_admin
from agentclaw.community.adapters.http.service_bot.schemas import ApiResponse
from agentclaw.community.adapters.http.service_bot.schemas_publish import (
    CheckApprovalRequest,
    CreateFirstPublishRequest,
    PublishFlowRequest,
    UpdateBotTypeForOthersRequest,
    UpdateBotTypeRequest,
    UpdatePublishStatusRequest,
    UpdateServiceBotConfigRequest,
    UpgradeBotTypeForOthersRequest,
    UpgradeBotTypeRequest,
    UpgradePublishRequest,
)
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.api.bot_publish_service import BotPublishServiceProtocol
from agentclaw.community.api.publish_approval import (
    ApprovalResult,
    PublishApprovalServiceProtocol,
)
from agentclaw.community.api.publish_flow_service import PublishFlowServiceProtocol
from agentclaw.community.core.bot_collaborator.interceptor import (
    CollaboratorPermissionInterceptor,
    PermissionParams,
    with_interceptors,
)
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError as BotManagementNotFoundError,
)
from agentclaw.community.core.bot_management.services.engine_resolver import resolve_engine_for_bot
from agentclaw.community.api.engine_config_service import EngineConfigServiceProtocol
from agentclaw.community.core.service_bot.repository.bot_publish_repository import BotPublishRepositoryProtocol
from agentclaw.community.core.service_bot.repository.models import PublishStatus
from agentclaw.community.core.service_bot.services.bot_publish_service import (
    BotAlreadyServiceTypeError,
    BotNotFoundError,
    BotNotServiceTypeError,
    BotPublishServiceError,
    BotTypeNotSupportedError,
    PublishAlreadyExistsError,
    PublishNotFoundError,
    PublishStatusInvalidError,
)
from agentclaw.community.core.service_bot.services.publish_flow_service import (
    PublishFlowServiceError,
)
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger


logger = get_logger()

router = APIRouter(prefix="/api/service-bot/publish", tags=["service-bot-publish"])


# ============================================================================
# 协作者权限提取函数
# ============================================================================
async def extract_from_publish_id(publish_id: str, ctx) -> PermissionParams:
    """从 publish_id 查询 bot_id 和 owner_id.

    Args:
        publish_id: 发布单 ID（由拦截器通过表达式注入）
        ctx: 拦截器上下文。``ctx.injector`` 由 ``with_interceptors``
            从 ``request.app.state.injector`` 注入；解析服务以避免
            依赖全局 ``get_app_injector()``。
    """
    if not publish_id:
        return PermissionParams()

    if ctx.injector is None:
        return PermissionParams()

    try:
        publish_service = ctx.injector.get(BotPublishServiceProtocol)
    except Exception:
        return PermissionParams()

    try:
        publish_id_int = int(publish_id)
        record = publish_service.get_publish_by_id(publish_id_int)
        if not record:
            return PermissionParams()

        return PermissionParams(
            bot_id=record.source_bot_id,
            owner_id=record.owner_id,
        )
    except (ValueError, Exception):
        return PermissionParams()


@router.post(
    "/process",
    response_model=ApiResponse,
    summary="推进发布流程",
)
@with_interceptors(CollaboratorPermissionInterceptor(
    params_extractor=extract_from_publish_id,
    extractor_params={"publish_id": "$request.publish_id"},
))
async def process_publish(
    request: PublishFlowRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    flow_service: PublishFlowServiceProtocol = Injected(PublishFlowServiceProtocol),
) -> ApiResponse:
    """推进发布流程.

    POST /api/service-bot/publish/process
    Body: {
        "publish_id": 123,
        "operator": "xxx"
    }

    根据发布单当前状态执行对应阶段：
    - DRAFT -> 执行构建阶段（构建 + 发布到验证环境）
    - BUILT -> 执行发布阶段（发布到正式环境）

    Returns:
        ApiResponse: 包含 PublishFlowResult
    """
    try:
        user_id = user.staffId
        publish_id = request.publish_id

        if not user_id or user_id == "anonymous":
            return ApiResponse(success=False, message="无法获取用户信息", error_code=400, data=None)

        logger.info(f"[process_publish] Processing: publish_id={publish_id}, user_id={user_id}")

        result = await flow_service.process(
            publish_id=publish_id,
            operator=user_id,
        )

        return ApiResponse(
            success=result.status != PublishStatus.FAILED,
            data=result.model_dump() if hasattr(result, 'model_dump') else result.dict(),
            message=result.message,
        )

    except PublishNotFoundError as e:
        logger.error(f"[process_publish] Order not found: {e}")
        return ApiResponse(success=False, message=str(e), error_code=404, data=None)

    except PublishStatusInvalidError as e:
        logger.error(f"[process_publish] Invalid status: {e}")
        return ApiResponse(success=False, message=str(e), error_code=400, data=None)

    except PublishFlowServiceError as e:
        logger.error(f"[process_publish] Flow error: {e}")
        return ApiResponse(success=False, message=str(e), error_code=500, data=None)

    except Exception as e:
        logger.error(f"[process_publish] Unexpected error: {e}")
        return ApiResponse(success=False, message=f"发布流程失败: {str(e)}", error_code=500, data=None)


@router.get(
    "/{bot_id}/binding",
    response_model=ApiResponse,
    summary="查询 Bot 指定阶段绑定信息",
)
async def get_bot_stage_binding_info(
    bot_id: str,
    owner_id: str,
    stage: str,
    publish_service: BotPublishServiceProtocol = Injected(BotPublishServiceProtocol),
) -> ApiResponse:
    """查询 bot 在指定阶段对应的 binding / BaaS 信息.

    GET /api/service-bot/publish/{bot_id}/binding?owner_id=u1&stage=online
    """
    try:
        logger.info(
            f"[get_bot_stage_binding_info] Query: bot_id={bot_id}, stage={stage}, owner_id={owner_id}"
        )

        result = publish_service.get_bot_stage_binding_info(
            bot_id=bot_id,
            owner_id=owner_id,
            stage=stage,
        )

        return ApiResponse(
            success=True,
            data=result,
            message="查询成功",
        )

    except BotNotFoundError as e:
        logger.warning(f"[get_bot_stage_binding_info] Bot not found: {e}")
        return ApiResponse(success=False, message=str(e), error_code=404, data=None)

    except BotPublishServiceError as e:
        logger.error(f"[get_bot_stage_binding_info] Service error: {e}")
        return ApiResponse(success=False, message=str(e), error_code=500, data=None)

    except Exception as e:
        logger.error(f"[get_bot_stage_binding_info] Unexpected error: {e}")
        return ApiResponse(success=False, message=f"查询绑定信息失败: {str(e)}", error_code=500, data=None)


@router.get(
    "/{publish_id}",
    response_model=ApiResponse,
    summary="查询发布记录",
)
async def get_publish_record(
    publish_id: int,
    user: AuthenticatedUser = Depends(get_current_user),
    publish_service: BotPublishServiceProtocol = Injected(BotPublishServiceProtocol),
) -> ApiResponse:
    """查询发布记录详情.

    GET /api/service-bot/publish/{publish_id}
    """
    try:
        user_id = user.staffId

        if not user_id or user_id == "anonymous":
            return ApiResponse(success=False, message="无法获取用户信息", error_code=400, data=None)

        logger.info(f"[get_publish_record] Query: publish_id={publish_id}, user_id={user_id}")

        # 查询发布记录
        record = publish_service.get_publish_by_id(publish_id)
        if not record:
            return ApiResponse(
                success=False,
                message=f"发布记录不存在: {publish_id}",
                error_code=404,
                data=None,
            )

        return ApiResponse(
            success=True,
            data=record.to_dict(),
            message="查询成功",
        )

    except Exception as e:
        logger.error(f"[get_publish_record] Error: {e}")
        return ApiResponse(success=False, message=f"查询失败: {str(e)}", error_code=500, data=None)


@router.get(
    "/{publish_id}/engine-config",
    response_model=ApiResponse,
    summary="根据发布单获取引擎配置",
)
@with_interceptors(CollaboratorPermissionInterceptor(
    params_extractor=extract_from_publish_id,
    extractor_params={"publish_id": "$publish_id"},
    persist_audit_log=False,  # 只读操作，不需要审计和锁检查
))
async def get_publish_engine_config(
    publish_id: int,
    user: AuthenticatedUser = Depends(get_current_user),
    publish_repo: BotPublishRepositoryProtocol = Injected(BotPublishRepositoryProtocol),
    bot_repo: BotRepository = Injected(BotRepository),
    engine_config_service: EngineConfigServiceProtocol = Injected(
        EngineConfigServiceProtocol
    ),
) -> ApiResponse:
    """根据服务 Bot 发布单读取发布阶段引擎配置.

    GET /api/service-bot/publish/{publish_id}/engine-config

    从发布单 ext.binding 获取发布阶段（online 优先，其次 verify）的 binding，
    通过 EngineConfigService 以 provider-blind 方式读取所在设备（arca/baas/teclaw）
    上的引擎配置文件。

    配置文件按 bot 的引擎类型解析（openclaw / claude_code），与 bot 级
    engine-config 读取保持一致。
    """
    try:
        logger.info(
            f"[get_publish_engine_config] Query: publish_id={publish_id}, user={user.staffId}"
        )

        # 1. 获取发布记录
        record = publish_repo.get_by_id(publish_id)
        if not record:
            return ApiResponse(
                success=False,
                message=f"发布记录不存在: {publish_id}",
                error_code=404,
                data=None,
            )

        # 2. 解析引擎类型（路由层负责，传给 provider-blind 的服务）
        engine_type = resolve_engine_for_bot(
            bot_id=record.source_bot_id,
            owner_id=record.owner_id,
            bot_repo=bot_repo,
        )

        # 3. 读取发布阶段引擎配置（按 binding 所在设备分流）。
        #    data 仅在容器内配置文件缺失/为空时为 {}；无法解析/读取设备等真实
        #    失败会上抛（见下方 except），不会被伪装成空配置。
        data = await engine_config_service.read_publish_config(record, engine_type)
        return ApiResponse(success=True, data=data, message="查询成功")

    except json.JSONDecodeError as e:
        logger.error(
            f"[get_publish_engine_config] Invalid JSON: publish_id={publish_id}, error={e}"
        )
        return ApiResponse(
            success=False,
            message=f"配置文件格式错误: {e}",
            error_code=500,
            data=None,
        )
    except Exception as e:
        logger.error(f"[get_publish_engine_config] Unexpected error: {e}")
        return ApiResponse(success=False, message=f"查询失败: {str(e)}", error_code=500, data=None)


@router.post(
    "/create_first_publish",
    response_model=ApiResponse,
    summary="创建首个发布单",
)
async def create_first_publish(
    request: CreateFirstPublishRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    publish_service: BotPublishServiceProtocol = Injected(BotPublishServiceProtocol),
) -> ApiResponse:
    """为服务型 Bot 创建首个发布单.

    POST /api/service-bot/publish/create
    Body: {
        "bot_id": "xxx",
        "name": "发布单名称",
        "permission_owner": "owner",  // 可选，默认 "owner"
        "description": "描述"         // 可选
    }

    前置条件：
    - Bot 必须存在
    - Bot 类型必须是 service
    - 该 Bot 尚未创建过发布单

    Returns:
        ApiResponse: 包含创建的发布单信息
    """
    try:
        user_id = user.staffId
        user_name = getattr(user, 'name', None) or user_id

        if not user_id or user_id == "anonymous":
            return ApiResponse(success=False, message="无法获取用户信息", error_code=400, data=None)

        logger.info(
            f"[create_first_publish] Creating: bot_id={request.bot_id}, "
            f"name={request.name}, user_id={user_id}"
        )

        # 调用 service 创建发布单
        record = publish_service.create_first_publish_for_bot(
            bot_id=request.bot_id,
            owner_id=user_id,
            name=request.name,
            permission_owner=request.permission_owner,
            description=request.description,
            owner_name=user_name,
        )

        return ApiResponse(
            success=True,
            data=record.to_dict(),
            message="发布单创建成功",
        )

    except BotNotFoundError as e:
        logger.warning(f"[create_first_publish] Bot not found: {e}")
        return ApiResponse(success=False, message=str(e), error_code=404, data=None)

    except BotNotServiceTypeError as e:
        logger.warning(f"[create_first_publish] Bot type error: {e}")
        return ApiResponse(success=False, message=str(e), error_code=400, data=None)

    except PublishAlreadyExistsError as e:
        logger.warning(f"[create_first_publish] Publish already exists: {e}")
        return ApiResponse(success=False, message=str(e), error_code=409, data=None)

    except BotPublishServiceError as e:
        logger.error(f"[create_first_publish] Service error: {e}")
        return ApiResponse(success=False, message=str(e), error_code=500, data=None)

    except Exception as e:
        logger.error(f"[create_first_publish] Unexpected error: {e}")
        return ApiResponse(success=False, message=f"创建发布单失败: {str(e)}", error_code=500, data=None)


@router.post(
    "/upgrade",
    response_model=ApiResponse,
    summary="升级发布单",
)
@with_interceptors(CollaboratorPermissionInterceptor(
    params_extractor=extract_from_publish_id,
    extractor_params={"publish_id": "$request.publish_id"},
))
async def upgrade_publish(
    request: UpgradePublishRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    publish_service: BotPublishServiceProtocol = Injected(BotPublishServiceProtocol),
) -> ApiResponse:
    """升级发布单，基于已成功的版本创建新草稿.

    POST /api/service-bot/publish/upgrade
    Body: {
        "publish_id": 123
    }

    前置条件：
    - 原发布单必须存在
    - 原发布单状态必须为 success
    - 操作者必须是发布单的 owner 或协作者（ADMIN 及以上权限）

    幂等性：
    - 如果已升级过，返回已创建的新发布单

    Returns:
        ApiResponse: 包含新创建的发布单信息
    """
    try:
        user_id = user.staffId

        if not user_id or user_id == "anonymous":
            return ApiResponse(success=False, message="无法获取用户信息", error_code=400, data=None)

        logger.info(
            f"[upgrade_publish] Upgrading: publish_id={request.publish_id}, user_id={user_id}"
        )

        # 调用 service 升级发布单
        record = publish_service.upgrade_publish(
            publish_id=request.publish_id,
            owner_id=user_id,
        )

        return ApiResponse(
            success=True,
            data=record.to_dict(),
            message="发布单升级成功",
        )

    except PublishNotFoundError as e:
        logger.warning(f"[upgrade_publish] Publish not found: {e}")
        return ApiResponse(success=False, message=str(e), error_code=404, data=None)

    except PublishStatusInvalidError as e:
        logger.warning(f"[upgrade_publish] Invalid status: {e}")
        return ApiResponse(success=False, message=str(e), error_code=400, data=None)

    except BotPublishServiceError as e:
        logger.error(f"[upgrade_publish] Service error: {e}")
        return ApiResponse(success=False, message=str(e), error_code=403, data=None)

    except Exception as e:
        logger.error(f"[upgrade_publish] Unexpected error: {e}")
        return ApiResponse(success=False, message=f"升级发布单失败: {str(e)}", error_code=500, data=None)


@router.post(
    "/{publish_id}/sync",
    response_model=ApiResponse,
    summary="Get publish status (read-only)",
)
async def describe_publish(
    publish_id: int,
    user: AuthenticatedUser = Depends(get_current_user),
    flow_service: PublishFlowServiceProtocol = Injected(PublishFlowServiceProtocol),
) -> ApiResponse:
    """Report the publish record's current status. Read-only.

    POST /api/service-bot/publish/{publish_id}/sync

    Historically this endpoint drove the BaaS-progress sync itself; the durable
    task pipeline now owns all status advancement (the progress-poll task drives
    ``advance_publish_progress``), so this query just reads the record and
    describes its status — it never mutates. The route path stays ``/sync`` for
    API compatibility.

    Returns:
        ApiResponse: the current status and a human-readable message
        (``success=false`` when the publish is FAILED).
    """
    try:
        user_id = user.staffId

        if not user_id or user_id == "anonymous":
            return ApiResponse(success=False, message="无法获取用户信息", error_code=400, data=None)

        logger.info(
            f"[describe_publish] Reporting status: publish_id={publish_id}, user_id={user_id}"
        )

        result = flow_service.describe_publish(
            publish_id=publish_id,
        )

        return ApiResponse(
            success=result.status != PublishStatus.FAILED,
            data=result.model_dump() if hasattr(result, 'model_dump') else result.dict(),
            message=result.message,
        )

    except PublishNotFoundError as e:
        logger.error(f"[describe_publish] Order not found: {e}")
        return ApiResponse(success=False, message=str(e), error_code=404, data=None)

    except PublishStatusInvalidError as e:
        logger.error(f"[describe_publish] Invalid status: {e}")
        return ApiResponse(success=False, message=str(e), error_code=400, data=None)

    except PublishFlowServiceError as e:
        logger.error(f"[describe_publish] Flow error: {e}")
        return ApiResponse(success=False, message=str(e), error_code=500, data=None)

    except Exception as e:
        logger.error(f"[describe_publish] Unexpected error: {e}")
        return ApiResponse(success=False, message=f"同步发布进度失败: {str(e)}", error_code=500, data=None)


@router.post(
    "/{publish_id}/scale/status",
    response_model=ApiResponse,
    summary="查询扩容发布单状态",
)
async def sync_scale_progress(
    publish_id: int,
    user: AuthenticatedUser = Depends(get_current_user),
    flow_service: PublishFlowServiceProtocol = Injected(PublishFlowServiceProtocol),
) -> ApiResponse:
    """查询 BaaS 层扩容发布进度。

    POST /api/service-bot/publish/{publish_id}/scale/status
    """
    try:
        user_id = user.staffId

        if not user_id or user_id == "anonymous":
            return ApiResponse(success=False, message="无法获取用户信息", error_code=400, data=None)

        logger.info(
            f"[sync_scale_progress] Syncing scale progress: publish_id={publish_id}, user_id={user_id}"
        )

        result = flow_service.sync_scale_progress(
            publish_id=publish_id,
        )

        return ApiResponse(
            success=result.status != PublishStatus.FAILED,
            data=result.model_dump() if hasattr(result, 'model_dump') else result.dict(),
            message=result.message,
        )

    except PublishNotFoundError as e:
        logger.error(f"[sync_scale_progress] Order not found: {e}")
        return ApiResponse(success=False, message=str(e), error_code=404, data=None)

    except PublishStatusInvalidError as e:
        logger.error(f"[sync_scale_progress] Invalid status: {e}")
        return ApiResponse(success=False, message=str(e), error_code=400, data=None)

    except PublishFlowServiceError as e:
        logger.error(f"[sync_scale_progress] Flow error: {e}")
        return ApiResponse(success=False, message=str(e), error_code=500, data=None)

    except Exception as e:
        logger.error(f"[sync_scale_progress] Unexpected error: {e}")
        return ApiResponse(success=False, message=f"同步扩容进度失败: {str(e)}", error_code=500, data=None)


@router.post(
    "/{publish_id}/status",
    response_model=ApiResponse,
    summary="更新发布单状态",
)
async def update_publish_status(
    publish_id: int,
    request: UpdatePublishStatusRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    publish_service: BotPublishServiceProtocol = Injected(BotPublishServiceProtocol),
) -> ApiResponse:
    """更新发布单状态.

    POST /api/service-bot/publish/{publish_id}/status
    Body: {
        "source_status": "draft",
        "target_status": "built"
    }

    Returns:
        ApiResponse: 包含更新后的发布单信息
    """
    try:
        user_id = user.staffId

        if not user_id or user_id == "anonymous":
            return ApiResponse(success=False, message="无法获取用户信息", error_code=400, data=None)

        logger.info(
            f"[update_publish_status] Updating: publish_id={publish_id}, "
            f"target_status={request.target_status}, source_status={request.source_status}, user_id={user_id}"
        )

        publish_record = publish_service.get_publish_by_id(publish_id)

        ext = publish_record.ext or {}
        ext["source_status"] = request.failed_status

        record = publish_service.update_publish_status_with_ext(
            publish_id=publish_id,
            target_status=request.target_status,
            ext=ext,
            source_status=request.source_status,
        )

        return ApiResponse(
            success=True,
            data=record.to_dict(),
            message="状态更新成功",
        )

    except PublishNotFoundError as e:
        logger.warning(f"[update_publish_status] Publish not found: {e}")
        return ApiResponse(success=False, message=str(e), error_code=404, data=None)

    except Exception as e:
        logger.error(f"[update_publish_status] Unexpected error: {e}")
        return ApiResponse(success=False, message=f"状态更新失败: {str(e)}", error_code=500, data=None)


@router.post(
    "/{publish_id}/offline",
    response_model=ApiResponse,
    summary="下线发布单",
)
@with_interceptors(CollaboratorPermissionInterceptor(
    required_level=PermissionLevel.ADMIN,
    params_extractor=extract_from_publish_id,
    extractor_params={"publish_id": "$publish_id"},
))
async def offline_publish(
    publish_id: int,
    user: AuthenticatedUser = Depends(get_current_user),
    publish_service: BotPublishServiceProtocol = Injected(BotPublishServiceProtocol),
) -> ApiResponse:
    """下线发布单.

    POST /api/service-bot/publish/{publish_id}/offline

    根据发布单状态自动判断下线流程：
    - SUCCESS（已发布成功）：先删除 Bot，再销毁线上 bot
    - VALIDATING（验证中）：销毁验证环境 bot

    Returns:
        ApiResponse: 包含下线结果
    """
    try:
        user_id = user.staffId

        if not user_id or user_id == "anonymous":
            return ApiResponse(success=False, message="无法获取用户信息", error_code=400, data=None)

        logger.info(
            f"[offline_publish] Offlining: publish_id={publish_id}, user_id={user_id}"
        )

        # 确保 PublishFlowService 已初始化（会自动注入依赖）

        # 调用 service 下线
        result = await publish_service.offline_publish(
            publish_id=publish_id,
        )

        return ApiResponse(
            success=result.get("success", True),
            data=result,
            message=result.get("message", "下线成功"),
        )

    except PublishNotFoundError as e:
        logger.warning(f"[offline_publish] Publish not found: {e}")
        return ApiResponse(success=False, message=str(e), error_code=404, data=None)

    except BotPublishServiceError as e:
        logger.error(f"[offline_publish] Service error: {e}")
        return ApiResponse(success=False, message=str(e), error_code=400, data=None)

    except Exception as e:
        logger.error(f"[offline_publish] Unexpected error: {e}")
        return ApiResponse(success=False, message=f"下线失败: {str(e)}", error_code=500, data=None)


@router.post(
    "/{publish_id}/scale",
    response_model=ApiResponse,
    summary="服务 Bot 扩缩容",
)
async def scale_publish_bot(
    publish_id: int,
    user: AuthenticatedUser = Depends(get_current_user),
    flow_service: PublishFlowServiceProtocol = Injected(PublishFlowServiceProtocol),
) -> ApiResponse:
    """对服务 Bot 发起扩缩容。

    POST /api/service-bot/publish/{publish_id}/scale
    """
    try:
        user_id = user.staffId

        if not user_id or user_id == "anonymous":
            return ApiResponse(success=False, message="无法获取用户信息", error_code=400, data=None)

        logger.info(
            f"[scale_publish_bot] Scaling: publish_id={publish_id}, user_id={user_id}, "
        )

        result = await flow_service.scale_bot(
            publish_id=publish_id,
            operator=user_id,
        )

        return ApiResponse(
            success=result.get("success", False),
            data=result,
            message=result.get("message", "扩容任务已提交"),
        )

    except PublishNotFoundError as e:
        logger.warning(f"[scale_publish_bot] Publish not found: {e}")
        return ApiResponse(success=False, message=str(e), error_code=404, data=None)

    except PublishStatusInvalidError as e:
        logger.warning(f"[scale_publish_bot] Invalid status: {e}")
        return ApiResponse(success=False, message=str(e), error_code=400, data=None)

    except PublishFlowServiceError as e:
        logger.error(f"[scale_publish_bot] Flow error: {e}")
        return ApiResponse(success=False, message=str(e), error_code=500, data=None)

    except Exception as e:
        logger.error(f"[scale_publish_bot] Unexpected error: {e}")
        return ApiResponse(success=False, message=f"扩容发布单失败: {str(e)}", error_code=500, data=None)


@router.post(
    "/{publish_id}/restart",
    response_model=ApiResponse,
    summary="重启发布单",
)
@with_interceptors(CollaboratorPermissionInterceptor(
    params_extractor=extract_from_publish_id,
    extractor_params={"publish_id": "$publish_id"},
))
async def restart_publish(
    publish_id: int,
    user: AuthenticatedUser = Depends(get_current_user),
    flow_service: PublishFlowServiceProtocol = Injected(PublishFlowServiceProtocol),
) -> ApiResponse:
    """重启发布单.

    POST /api/service-bot/publish/{publish_id}/restart

    根据发布单状态确定当前阶段，从 binding 信息获取 bot_uuid，调用 BaaS 层重启接口。
    支持重启的状态：
    - VALIDATING: 重启验证环境的 bot
    - SUCCESS: 重启线上的 bot

    Returns:
        ApiResponse: 包含重启结果
    """
    try:
        user_id = user.staffId

        if not user_id or user_id == "anonymous":
            return ApiResponse(success=False, message="无法获取用户信息", error_code=400, data=None)

        logger.info(
            f"[restart_publish] Restarting: publish_id={publish_id}, user_id={user_id}"
        )

        # 调用 service 重启
        result = flow_service.restart_bot(
            publish_id=publish_id,
            operator=user_id,
        )

        return ApiResponse(
            success=result.get("success", False),
            data=result,
            message=result.get("message", "重启任务已提交"),
        )

    except PublishNotFoundError as e:
        logger.warning(f"[restart_publish] Publish not found: {e}")
        return ApiResponse(success=False, message=str(e), error_code=404, data=None)

    except PublishStatusInvalidError as e:
        logger.warning(f"[restart_publish] Invalid status: {e}")
        return ApiResponse(success=False, message=str(e), error_code=400, data=None)

    except PublishFlowServiceError as e:
        logger.error(f"[restart_publish] Flow error: {e}")
        return ApiResponse(success=False, message=str(e), error_code=500, data=None)

    except Exception as e:
        logger.error(f"[restart_publish] Unexpected error: {e}")
        return ApiResponse(success=False, message=f"重启发布单失败: {str(e)}", error_code=500, data=None)


@router.get(
    "/{publish_id}/restart_for_others",
    response_model=ApiResponse,
    summary="重启发布单(使用owner_id)",
)
async def restart_publish_for_others(
    publish_id: int,
    user: AuthenticatedUser = Depends(get_current_user),
    publish_service: BotPublishServiceProtocol = Injected(BotPublishServiceProtocol),
    flow_service: PublishFlowServiceProtocol = Injected(PublishFlowServiceProtocol),
) -> ApiResponse:
    """重启发布单(使用发布单的owner_id作为operator).

    GET /api/service-bot/publish/{publish_id}/restart_for_others

    与 restart_publish 的区别：operator 使用发布单的 owner_id，而非当前登录用户。
    用于管理员或系统代为执行重启操作的场景。

    根据发布单状态确定当前阶段，从 binding 信息获取 bot_uuid，调用 BaaS 层重启接口。
    支持重启的状态：
    - VALIDATING: 重启验证环境的 bot
    - SUCCESS: 重启线上的 bot

    Returns:
        ApiResponse: 包含重启结果
    """
    try:
        user_id = user.staffId

        if not user_id or user_id == "anonymous":
            return ApiResponse(success=False, message="无法获取用户信息", error_code=400, data=None)

        # 权限校验：只有 SUPER_ADMIN 中的用户才能操作
        if user_id not in super_admin():
            logger.warning(f"[restart_publish_for_others] Permission denied: user_id={user_id}")
            return ApiResponse(success=False, message="无权限执行此操作", error_code=403, data=None)

        logger.info(
            f"[restart_publish_for_others] Restarting: publish_id={publish_id}, operator={user_id}"
        )

        # 获取发布单记录，从中提取 owner_id
        record = publish_service.get_publish_by_id(publish_id)
        if not record:
            raise PublishNotFoundError(f"发布记录不存在: {publish_id}")

        owner_id = record.owner_id
        logger.info(
            f"[restart_publish_for_others] Using owner_id as operator: publish_id={publish_id}, owner_id={owner_id}"
        )

        # 调用 service 重启，使用 owner_id 作为 operator
        result = flow_service.restart_bot(
            publish_id=publish_id,
            operator=owner_id,
        )

        return ApiResponse(
            success=result.get("success", False),
            data=result,
            message=result.get("message", "重启任务已提交"),
        )

    except PublishNotFoundError as e:
        logger.warning(f"[restart_publish_for_others] Publish not found: {e}")
        return ApiResponse(success=False, message=str(e), error_code=404, data=None)

    except PublishStatusInvalidError as e:
        logger.warning(f"[restart_publish_for_others] Invalid status: {e}")
        return ApiResponse(success=False, message=str(e), error_code=400, data=None)

    except PublishFlowServiceError as e:
        logger.error(f"[restart_publish_for_others] Flow error: {e}")
        return ApiResponse(success=False, message=str(e), error_code=500, data=None)

    except Exception as e:
        logger.error(f"[restart_publish_for_others] Unexpected error: {e}")
        return ApiResponse(success=False, message=f"重启发布单失败: {str(e)}", error_code=500, data=None)


@router.post(
    "/{publish_id}/restart_status",
    response_model=ApiResponse,
    summary="查询重启发布状态",
)
async def restart_status(
    publish_id: int,
    user: AuthenticatedUser = Depends(get_current_user),
    flow_service: PublishFlowServiceProtocol = Injected(PublishFlowServiceProtocol),
) -> ApiResponse:
    """查询重启发布单状态.

    POST /api/service-bot/publish/{publish_id}/restart_status

    根据发布单状态确定当前阶段，从 ext 中获取 BaaS 层重启发布单 ID，
    调用 BaaS 层获取发布进度并返回。
    支持查询的状态：
    - VALIDATING: 查询验证环境重启进度
    - SUCCESS: 查询线上环境重启进度

    Returns:
        ApiResponse: 包含重启发布进度
    """
    try:
        user_id = user.staffId

        if not user_id or user_id == "anonymous":
            return ApiResponse(success=False, message="无法获取用户信息", error_code=400, data=None)

        logger.info(
            f"[restart_status] Querying restart status: publish_id={publish_id}, user_id={user_id}"
        )

        result = flow_service.sync_restart_progress(
            publish_id=publish_id,
        )

        return ApiResponse(
            success=result.status != PublishStatus.FAILED,
            data=result.model_dump() if hasattr(result, 'model_dump') else result.dict(),
            message=result.message,
        )

    except PublishNotFoundError as e:
        logger.error(f"[restart_status] Order not found: {e}")
        return ApiResponse(success=False, message=str(e), error_code=404, data=None)

    except PublishFlowServiceError as e:
        logger.error(f"[restart_status] Flow error: {e}")
        return ApiResponse(success=False, message=str(e), error_code=500, data=None)

    except Exception as e:
        logger.error(f"[restart_status] Unexpected error: {e}")
        return ApiResponse(success=False, message=f"查询重启状态失败: {str(e)}", error_code=500, data=None)


@router.post(
    "/{publish_id}/retry",
    response_model=ApiResponse,
    summary="重试失败的发布流程",
)
@with_interceptors(CollaboratorPermissionInterceptor(
    params_extractor=extract_from_publish_id,
    extractor_params={"publish_id": "$publish_id"},
))
async def retry_publish(
    publish_id: int,
    user: AuthenticatedUser = Depends(get_current_user),
    flow_service: PublishFlowServiceProtocol = Injected(PublishFlowServiceProtocol),
) -> ApiResponse:
    """重试失败的发布流程.

    POST /api/service-bot/publish/{publish_id}/retry

    根据失败前状态自动选择重试策略：
    - building failed → roll back to BUILDING, rebuild + verify release
    - built 失败 → 回退到 BUILT，重新验证发布
    - validate_pub 失败 → 回退到 VALIDATE_PUB，调用 BaaS 重启重试
    - online_pub 失败 → 回退到 ONLINE_PUB，调用 BaaS 重启重试

    Returns:
        ApiResponse: 包含重试结果
    """
    try:
        user_id = user.staffId

        if not user_id or user_id == "anonymous":
            return ApiResponse(success=False, message="无法获取用户信息", error_code=400, data=None)

        logger.info(f"[retry_publish] Retrying: publish_id={publish_id}, user_id={user_id}")

        result = await flow_service.retry(
            publish_id=publish_id,
            operator=user_id,
        )

        return ApiResponse(
            success=result.status != PublishStatus.FAILED,
            data=result.model_dump() if hasattr(result, 'model_dump') else result.dict(),
            message=result.message,
        )

    except PublishNotFoundError as e:
        logger.warning(f"[retry_publish] Publish not found: {e}")
        return ApiResponse(success=False, message=str(e), error_code=404, data=None)

    except PublishFlowServiceError as e:
        logger.error(f"[retry_publish] Flow error: {e}")
        return ApiResponse(success=False, message=str(e), error_code=400, data=None)

    except Exception as e:
        logger.error(f"[retry_publish] Unexpected error: {e}")
        return ApiResponse(success=False, message=f"重试发布失败: {str(e)}", error_code=500, data=None)


@router.post(
    "/{publish_id}/retry_for_others",
    response_model=ApiResponse,
    summary="重试失败的发布流程(使用owner_id)",
)
async def retry_publish_for_others(
    publish_id: int,
    user: AuthenticatedUser = Depends(get_current_user),
    publish_service: BotPublishServiceProtocol = Injected(BotPublishServiceProtocol),
    flow_service: PublishFlowServiceProtocol = Injected(PublishFlowServiceProtocol),
) -> ApiResponse:
    """重试失败的发布流程(使用发布单的owner_id作为operator).

    POST /api/service-bot/publish/{publish_id}/retry_for_others

    与 retry_publish 的区别：operator 使用发布单的 owner_id，而非当前登录用户。
    用于管理员或系统代为执行重试操作的场景。

    根据失败前状态自动选择重试策略：
    - building failed → roll back to BUILDING, rebuild + verify release
    - built 失败 → 回退到 BUILT，重新验证发布
    - validate_pub 失败 → 回退到 VALIDATE_PUB，调用 BaaS 重启重试
    - online_pub 失败 → 回退到 ONLINE_PUB，调用 BaaS 重启重试

    Returns:
        ApiResponse: 包含重试结果
    """
    try:
        user_id = user.staffId

        if not user_id or user_id == "anonymous":
            return ApiResponse(success=False, message="无法获取用户信息", error_code=400, data=None)

        # 权限校验：只有 SUPER_ADMIN 中的用户才能操作
        if user_id not in super_admin():
            logger.warning(f"[retry_publish_for_others] Permission denied: user_id={user_id}")
            return ApiResponse(success=False, message="无权限执行此操作", error_code=403, data=None)

        logger.info(
            f"[retry_publish_for_others] Retrying: publish_id={publish_id}, operator={user_id}"
        )

        # 获取发布单记录，从中提取 owner_id
        record = publish_service.get_publish_by_id(publish_id)
        if not record:
            raise PublishNotFoundError(f"发布记录不存在: {publish_id}")

        owner_id = record.owner_id
        logger.info(
            f"[retry_publish_for_others] Using owner_id as operator: publish_id={publish_id}, owner_id={owner_id}"
        )

        # 调用 service 重试，使用 owner_id 作为 operator
        result = await flow_service.retry(
            publish_id=publish_id,
            operator=owner_id,
        )

        return ApiResponse(
            success=result.status != PublishStatus.FAILED,
            data=result.model_dump() if hasattr(result, 'model_dump') else result.dict(),
            message=result.message,
        )

    except PublishNotFoundError as e:
        logger.warning(f"[retry_publish_for_others] Publish not found: {e}")
        return ApiResponse(success=False, message=str(e), error_code=404, data=None)

    except PublishFlowServiceError as e:
        logger.error(f"[retry_publish_for_others] Flow error: {e}")
        return ApiResponse(success=False, message=str(e), error_code=400, data=None)

    except Exception as e:
        logger.error(f"[retry_publish_for_others] Unexpected error: {e}")
        return ApiResponse(success=False, message=f"重试发布失败: {str(e)}", error_code=500, data=None)


@router.post(
    "/upgrade_bot_type",
    response_model=ApiResponse,
    summary="升级 Bot 为服务型",
)
async def upgrade_bot_type(
    request: UpgradeBotTypeRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    publish_service: BotPublishServiceProtocol = Injected(BotPublishServiceProtocol),
) -> ApiResponse:
    """将个人 Bot 升级为服务 Bot。

    POST /api/service-bot/publish/upgrade_bot_type
    Body: {
        "bot_id": "xxx"
    }

    前置条件：
    - Bot 必须存在且属于当前用户
    - Bot 类型必须是 personal
    - Bot 不能是 aicoding 类型
    - 如已有发布记录，只更新 bot_type，不创建新发布单

    Returns:
        ApiResponse: 包含更新后的 Bot 和新创建的发布单
    """
    try:
        user_id = user.staffId

        if not user_id or user_id == "anonymous":
            return ApiResponse(success=False, message="无法获取用户信息", error_code=400, data=None)

        logger.info(
            f"[upgrade_bot_type] Upgrading: bot_id={request.bot_id}, user_id={user_id}"
        )

        # 调用 service 升级
        result = publish_service.upgrade_bot_to_service(
            bot_id=request.bot_id,
            owner_id=user_id,
        )

        return ApiResponse(
            success=True,
            data={
                "bot": result["bot"],
                "publish_record": result["publish_record"].to_dict() if result["publish_record"] else None,
            },
            message="Bot 升级为服务型成功",
        )

    except BotNotFoundError as e:
        logger.warning(f"[upgrade_bot_type] Bot not found: {e}")
        return ApiResponse(success=False, message=str(e), error_code=404, data=None)

    except BotAlreadyServiceTypeError as e:
        logger.warning(f"[upgrade_bot_type] Bot already service: {e}")
        return ApiResponse(success=False, message=str(e), error_code=400, data=None)

    except BotTypeNotSupportedError as e:
        logger.warning(f"[upgrade_bot_type] Bot type not supported: {e}")
        return ApiResponse(success=False, message=str(e), error_code=400, data=None)

    except BotPublishServiceError as e:
        logger.error(f"[upgrade_bot_type] Service error: {e}")
        return ApiResponse(success=False, message=str(e), error_code=500, data=None)

    except Exception as e:
        logger.error(f"[upgrade_bot_type] Unexpected error: {e}")
        return ApiResponse(success=False, message=f"升级失败: {str(e)}", error_code=500, data=None)


@router.post(
    "/upgrade_bot_type_for_others",
    response_model=ApiResponse,
    summary="升级 Bot 为服务型（使用指定 owner_id）",
)
async def upgrade_bot_type_for_others(
    request: UpgradeBotTypeForOthersRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    publish_service: BotPublishServiceProtocol = Injected(BotPublishServiceProtocol),
) -> ApiResponse:
    """将个人 Bot 升级为服务 Bot（使用指定 owner_id）。

    POST /api/service-bot/publish/upgrade_bot_type_for_others
    Body: {
        "bot_id": "xxx",
        "owner_id": "xxx"
    }

    与 upgrade_bot_type 的区别：
    - upgrade_bot_type: 使用当前登录用户作为 owner_id
    - upgrade_bot_type_for_others: 使用请求体指定的 owner_id

    用于管理员或系统代为执行升级的场景。

    Returns:
        ApiResponse: 包含更新后的 Bot 和新创建的发布单
    """
    try:
        user_id = user.staffId

        if not user_id or user_id == "anonymous":
            return ApiResponse(success=False, message="无法获取用户信息", error_code=400, data=None)

        # 权限校验：只有 SUPER_ADMIN 中的用户才能操作
        if user_id not in super_admin():
            logger.warning(f"[upgrade_bot_type_for_others] Permission denied: user_id={user_id}")
            return ApiResponse(success=False, message="无权限执行此操作", error_code=403, data=None)

        logger.info(
            f"[upgrade_bot_type_for_others] Upgrading: bot_id={request.bot_id}, "
            f"owner_id={request.owner_id}, user_id={user_id}"
        )

        # 调用 service 升级
        result = publish_service.upgrade_bot_to_service(
            bot_id=request.bot_id,
            owner_id=request.owner_id,
        )

        return ApiResponse(
            success=True,
            data={
                "bot": result["bot"],
                "publish_record": result["publish_record"].to_dict() if result["publish_record"] else None,
            },
            message="Bot 升级为服务型成功",
        )

    except BotNotFoundError as e:
        logger.warning(f"[upgrade_bot_type_for_others] Bot not found: {e}")
        return ApiResponse(success=False, message=str(e), error_code=404, data=None)

    except BotAlreadyServiceTypeError as e:
        logger.warning(f"[upgrade_bot_type_for_others] Bot already service: {e}")
        return ApiResponse(success=False, message=str(e), error_code=400, data=None)

    except BotTypeNotSupportedError as e:
        logger.warning(f"[upgrade_bot_type_for_others] Bot type not supported: {e}")
        return ApiResponse(success=False, message=str(e), error_code=400, data=None)

    except BotPublishServiceError as e:
        logger.error(f"[upgrade_bot_type_for_others] Service error: {e}")
        return ApiResponse(success=False, message=str(e), error_code=500, data=None)

    except Exception as e:
        logger.error(f"[upgrade_bot_type_for_others] Unexpected error: {e}")
        return ApiResponse(success=False, message=f"升级失败: {str(e)}", error_code=500, data=None)


@router.post(
    "/update_bot_type",
    response_model=ApiResponse,
    summary="更新 Bot 类型",
)
async def update_bot_type(
    request: UpdateBotTypeRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    publish_service: BotPublishServiceProtocol = Injected(BotPublishServiceProtocol),
) -> ApiResponse:
    """直接更新 Bot 类型（不做业务校验）。

    POST /api/service-bot/publish/update_bot_type
    Body: {
        "bot_id": "xxx",
        "bot_type": "personal" | "service"
    }

    与 upgrade_bot_type 的区别：
    - upgrade_bot_type: 个人 Bot 升级为服务型，会创建发布记录
    - update_bot_type: 直接更新 bot_type 字段，不做额外业务处理

    Returns:
        ApiResponse: 包含更新后的 Bot 信息
    """
    try:
        user_id = user.staffId

        if not user_id or user_id == "anonymous":
            return ApiResponse(success=False, message="无法获取用户信息", error_code=400, data=None)

        logger.info(
            f"[update_bot_type] Updating: bot_id={request.bot_id}, "
            f"bot_type={request.bot_type}, user_id={user_id}"
        )

        # 调用 BotPublishService 更新
        result = publish_service.update_bot_type(
            bot_id=request.bot_id,
            owner_id=user_id,
            bot_type=request.bot_type,
        )

        return ApiResponse(
            success=True,
            data=result["bot"],
            message="Bot 类型更新成功",
        )

    except BotNotFoundError as e:
        logger.warning(f"[update_bot_type] Bot not found: {e}")
        return ApiResponse(success=False, message=str(e), error_code=404, data=None)

    except BotPublishServiceError as e:
        logger.error(f"[update_bot_type] Service error: {e}")
        return ApiResponse(success=False, message=str(e), error_code=400, data=None)

    except Exception as e:
        logger.error(f"[update_bot_type] Unexpected error: {e}")
        return ApiResponse(success=False, message=f"更新失败: {str(e)}", error_code=500, data=None)


@router.post(
    "/update_bot_type_for_others",
    response_model=ApiResponse,
    summary="更新 Bot 类型（使用指定 owner_id）",
)
async def update_bot_type_for_others(
    request: UpdateBotTypeForOthersRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    publish_service: BotPublishServiceProtocol = Injected(BotPublishServiceProtocol),
) -> ApiResponse:
    """直接更新 Bot 类型（使用指定 owner_id）。

    POST /api/service-bot/publish/update_bot_type_for_others
    Body: {
        "bot_id": "xxx",
        "bot_type": "personal" | "service",
        "owner_id": "xxx"
    }

    与 update_bot_type 的区别：
    - update_bot_type: 使用当前登录用户作为 owner_id
    - update_bot_type_for_others: 使用请求体指定的 owner_id

    用于管理员或系统代为执行更新的场景。

    Returns:
        ApiResponse: 包含更新后的 Bot 信息
    """
    try:
        user_id = user.staffId

        if not user_id or user_id == "anonymous":
            return ApiResponse(success=False, message="无法获取用户信息", error_code=400, data=None)

        # 权限校验：只有 SUPER_ADMIN 中的用户才能操作
        if user_id not in super_admin():
            logger.warning(f"[update_bot_type_for_others] Permission denied: user_id={user_id}")
            return ApiResponse(success=False, message="无权限执行此操作", error_code=403, data=None)

        logger.info(
            f"[update_bot_type_for_others] Updating: bot_id={request.bot_id}, "
            f"bot_type={request.bot_type}, owner_id={request.owner_id}, user_id={user_id}"
        )

        # 调用 BotPublishService 更新
        result = publish_service.update_bot_type(
            bot_id=request.bot_id,
            owner_id=request.owner_id,
            bot_type=request.bot_type,
        )

        return ApiResponse(
            success=True,
            data=result["bot"],
            message="Bot 类型更新成功",
        )

    except BotNotFoundError as e:
        logger.warning(f"[update_bot_type_for_others] Bot not found: {e}")
        return ApiResponse(success=False, message=str(e), error_code=404, data=None)

    except BotPublishServiceError as e:
        logger.error(f"[update_bot_type_for_others] Service error: {e}")
        return ApiResponse(success=False, message=str(e), error_code=400, data=None)

    except Exception as e:
        logger.error(f"[update_bot_type_for_others] Unexpected error: {e}")
        return ApiResponse(success=False, message=f"更新失败: {str(e)}", error_code=500, data=None)


@router.post(
    "/{publish_id}/delete",
    response_model=ApiResponse,
    summary="删除服务 Bot",
)
@with_interceptors(CollaboratorPermissionInterceptor(
    required_level=PermissionLevel.OWNER,
    params_extractor=extract_from_publish_id,
    extractor_params={"publish_id": "$publish_id"},
))
async def delete_service_bot(
    publish_id: int,
    user: AuthenticatedUser = Depends(get_current_user),
    publish_service: BotPublishServiceProtocol = Injected(BotPublishServiceProtocol),
) -> ApiResponse:
    """删除服务 Bot.

    POST /api/service-bot/publish/{publish_id}/delete

    删除条件：
    1. 发布单状态必须是草稿（draft）
    2. 该 Bot 没有其他发布成功的发布单

    删除流程：
    1. 检查删除条件
    2. 销毁验证阶段的发布历史
    3. 删除 Bot

    Returns:
        ApiResponse: 包含删除结果
    """
    try:
        user_id = user.staffId

        if not user_id or user_id == "anonymous":
            return ApiResponse(success=False, message="无法获取用户信息", error_code=400, data=None)

        logger.info(
            f"[delete_service_bot] Deleting: publish_id={publish_id}, user_id={user_id}"
        )

        # 调用 service 删除（BotPublishService.delete_service_bot 内部
        # 通过 self._publish_flow_service_provider() 惰性解析
        # PublishFlowService，无需在此显式注入）。
        result = publish_service.delete_service_bot(publish_id=publish_id)

        return ApiResponse(
            success=True,
            data={"deleted": result},
            message="服务 Bot 删除成功",
        )

    except PublishNotFoundError as e:
        logger.warning(f"[delete_service_bot] Publish not found: {e}")
        return ApiResponse(success=False, message=str(e), error_code=404, data=None)

    except BotPublishServiceError as e:
        logger.error(f"[delete_service_bot] Service error: {e}")
        return ApiResponse(success=False, message=str(e), error_code=400, data=None)

    except Exception as e:
        logger.error(f"[delete_service_bot] Unexpected error: {e}")
        return ApiResponse(success=False, message=f"删除失败: {str(e)}", error_code=500, data=None)


@router.get(
    "/{publish_id}/can-rollback",
    response_model=ApiResponse,
    summary="检查是否可以回滚",
)
@with_interceptors(CollaboratorPermissionInterceptor(
    params_extractor=extract_from_publish_id,
    extractor_params={"publish_id": "$publish_id"},
    persist_audit_log=False,  # 只读操作
))
async def can_rollback_publish(
    publish_id: int,
    user: AuthenticatedUser = Depends(get_current_user),
    publish_service: BotPublishServiceProtocol = Injected(BotPublishServiceProtocol),
    publish_repo: BotPublishRepositoryProtocol = Injected(BotPublishRepositoryProtocol),
) -> ApiResponse:
    """检查发布单是否可以回滚.

    GET /api/service-bot/publish/{publish_id}/can-rollback

    回滚条件：
    1. 发布单状态必须是 SUCCESS
    2. 有上一个版本（last_pub_id > 0）
    3. 当前版本无 rollback_restored_from 标记
    4. 无新版本基于当前版本（版本链未延伸）
    5. 目标版本状态为 UPGRADED
    6. 目标版本有构建产物

    Returns:
        ApiResponse: 包含可回滚状态和原因
    """
    try:
        user_id = user.staffId

        if not user_id or user_id == "anonymous":
            return ApiResponse(success=False, message="无法获取用户信息", error_code=400, data=None)

        logger.info(
            f"[can_rollback_publish] Checking: publish_id={publish_id}, user_id={user_id}"
        )

        # 查询发布记录
        record = publish_repo.get_by_id(publish_id)
        if not record:
            return ApiResponse(
                success=False,
                can_rollback=False,
                reason=f"发布单不存在: publish_id={publish_id}",
                error_code=404,
                data=None,
            )

        # 调用 service 检查是否可回滚
        can_rollback, reason = publish_service.can_rollback(publish_id)

        # 构建响应
        result = {
            "can_rollback": can_rollback,
            "reason": reason if not can_rollback else None,
        }

        # 如果可回滚，添加目标版本信息
        if can_rollback and record.last_pub_id:
            target = publish_repo.get_by_id(record.last_pub_id)
            if target:
                result["target_publish_id"] = target.id
                result["target_version"] = target.version
                result["target_status"] = target.status

        # 检查 rollback_restored_from 标记
        current_ext = record.ext or {}
        if current_ext.get("rollback_restored_from"):
            result["rollback_restored_from"] = current_ext.get("rollback_restored_from")

        # 检查版本链延伸
        next_record = publish_repo.get_by_last_pub_id(publish_id)
        if next_record:
            result["next_publish_id"] = next_record.id
            result["next_version"] = next_record.version
            result["next_status"] = next_record.status

        return ApiResponse(
            success=True,
            data=result,
            message="查询成功",
        )

    except Exception as e:
        logger.error(f"[can_rollback_publish] Unexpected error: {e}")
        return ApiResponse(success=False, message=f"查询失败: {str(e)}", error_code=500, data=None)


@router.post(
    "/{publish_id}/rollback",
    response_model=ApiResponse,
    summary="回滚发布单",
)
@with_interceptors(CollaboratorPermissionInterceptor(
    required_level=PermissionLevel.ADMIN,
    params_extractor=extract_from_publish_id,
    extractor_params={"publish_id": "$publish_id"},
))
async def rollback_publish(
    publish_id: int,
    user: AuthenticatedUser = Depends(get_current_user),
    publish_service: BotPublishServiceProtocol = Injected(BotPublishServiceProtocol),
) -> ApiResponse:
    """回滚发布单到上一个稳定版本.

    POST /api/service-bot/publish/{publish_id}/rollback

    操作流程：
    1. 校验是否可以回滚
    2. 将当前版本状态改为 DRAFT，记录 ext.rollback
    3. 将上一个版本状态恢复为 SUCCESS，标记 ext.rollback_restored_from
    4. 使用上一个版本的配置重新部署

    Returns:
        ApiResponse: 包含回滚结果
    """
    try:
        user_id = user.staffId

        if not user_id or user_id == "anonymous":
            return ApiResponse(success=False, message="无法获取用户信息", error_code=400, data=None)

        logger.info(
            f"[rollback_publish] Rolling back: publish_id={publish_id}, user_id={user_id}"
        )

        # 调用 service 执行回滚（状态更新 + 部署）
        result = await publish_service.rollback_publish(
            publish_id=publish_id,
            operator=user_id,
            reason=None,  # 可从请求体获取
        )

        return ApiResponse(
            success=True,
            data=result,
            message="回滚成功",
        )

    except PublishNotFoundError as e:
        logger.warning(f"[rollback_publish] Publish not found: {e}")
        return ApiResponse(success=False, message=str(e), error_code=404, data=None)

    except PublishStatusInvalidError as e:
        logger.warning(f"[rollback_publish] Invalid status: {e}")
        return ApiResponse(success=False, message=str(e), error_code=400, data=None)

    except BotPublishServiceError as e:
        logger.error(f"[rollback_publish] Service error: {e}")
        return ApiResponse(success=False, message=str(e), error_code=400, data=None)

    except PublishFlowServiceError as e:
        logger.error(f"[rollback_publish] Flow error: {e}")
        return ApiResponse(success=False, message=str(e), error_code=500, data=None)

    except Exception as e:
        logger.error(f"[rollback_publish] Unexpected error: {e}")
        return ApiResponse(success=False, message=f"回滚失败: {str(e)}", error_code=500, data=None)


# ============================================================================
# 发布审批相关端点
# ============================================================================

@router.post("/approval_callback", summary="发布审批回调接口")
async def publish_approval_callback(
    request: Request,
    service: PublishApprovalServiceProtocol = Injected(PublishApprovalServiceProtocol),
):
    """接收 antprocess 审批回调（表单格式）.

    Args:
        request: FastAPI request object (form data from antprocess)
        service: Injected PublishApprovalService

    Returns:
        Dict with success status and message
    """
    form_data = await request.form()
    data = dict(form_data)

    publish_id_str = data.get("publish_id", "0")
    try:
        publish_id = int(publish_id_str)
    except (ValueError, TypeError):
        logger.warning(
            "[publish_approval_callback] invalid publish_id: %s",
            publish_id_str,
        )
        return {"success": False, "message": f"Invalid publish_id: {publish_id_str}"}

    action = str(data.get("action", ""))
    applicant = str(data.get("applicant", ""))
    puid = str(data.get("globalUniqueId", ""))
    last_operate = str(data.get("lastOperate", "")).upper()

    logger.info(
        "[publish_approval_callback] received: publish_id=%s, action=%s, applicant=%s, puid=%s, last_operate=%s",
        publish_id,
        action,
        applicant,
        puid,
        last_operate,
    )

    result = await service.handle_approval_callback(
        publish_id=publish_id,
        action=action,
        applicant=applicant,
        puid=puid,
        last_operate=last_operate,
    )

    return result


@router.post(
    "/{publish_id}/check_approval",
    response_model=ApiResponse,
    summary="检查并处理审批状态",
)
@with_interceptors(CollaboratorPermissionInterceptor(
    params_extractor=extract_from_publish_id,
    extractor_params={"publish_id": "$publish_id"},
))
async def check_and_process_approval(
    publish_id: int,
    request: CheckApprovalRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    approval_service: PublishApprovalServiceProtocol = Injected(PublishApprovalServiceProtocol),
    publish_service: BotPublishServiceProtocol = Injected(BotPublishServiceProtocol),
) -> ApiResponse:
    """检查并处理审批状态.

    POST /api/service-bot/publish/{publish_id}/check_approval
    Body: {"action": "online" | "offline"}

    根据 action 调用对应的审批检查方法：
    - online: check_and_process_should_approval
    - offline: check_and_process_offline_approval

    Returns:
        ApiResponse with approval status info
    """
    try:
        user_id = user.staffId
        if not user_id or user_id == "anonymous":
            return ApiResponse(success=False, message="无法获取用户信息", error_code=400, data=None)

        # 获取发布记录
        publish_record = publish_service.get_publish_by_id(publish_id)
        if not publish_record:
            return ApiResponse(
                success=False,
                message=f"发布单不存在: {publish_id}",
                error_code=404,
                data=None,
            )

        # 校验发布单状态
        if request.action == "online":
            if publish_record.status != PublishStatus.VALIDATING:
                return ApiResponse(
                    success=False,
                    message=f"上线操作要求发布单状态为 validating，当前状态为 {publish_record.status}",
                    error_code=400,
                    data=None,
                )
        else:  # offline
            if publish_record.status != PublishStatus.SUCCESS:
                return ApiResponse(
                    success=False,
                    message=f"下线操作要求发布单状态为 success，当前状态为 {publish_record.status}",
                    error_code=400,
                    data=None,
                )

        # 根据 action 调用对应的审批检查方法
        if request.action == "online":
            result: ApprovalResult = await approval_service.check_and_process_should_approval(
                publish_record=publish_record,
                operator=user_id,
            )
        else:  # offline
            result = await approval_service.check_and_process_offline_approval(
                publish_record=publish_record,
                operator=user_id,
            )

        logger.info(
            "[check_and_process_approval] publish_id=%s, action=%s, status=%s, should_approval=%s",
            publish_id,
            request.action,
            result.status,
            result.should_approval,
        )

        return ApiResponse(
            success=True,
            message=result.message,
            data={
                "should_approval": result.should_approval,
                "status": result.status,
                "approval": result.approval,
                "message": result.message,
            },
        )

    except PublishNotFoundError as e:
        logger.warning("[check_and_process_approval] PublishNotFoundError: %s", e)
        return ApiResponse(success=False, message=str(e), error_code=404, data=None)
    except Exception as e:
        logger.exception("[check_and_process_approval] unexpected error: %s", e)
        return ApiResponse(
            success=False,
            message=f"检查审批状态失败: {str(e)}",
            error_code=500,
            data=None,
        )


@router.post(
    "/update-service-bot-config",
    response_model=ApiResponse,
    summary="更新服务 Bot 配置",
)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$request.bot_id",
    owner_id="$request.owner_id",
))
async def update_service_bot_config(
    request: UpdateServiceBotConfigRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> ApiResponse:
    """更新服务 Bot 配置.

    POST /api/service-bot/publish/update-service-bot-config
    Body: {
        "bot_id": "default",
        "owner_id": "100000",
        "config_update": {
            "should_approval": true,
            "device_count": 2,
            "cpu": 4,
            "memory": 8192
        }
    }

    增量更新 Bot.ext.service_bot_config 字段，会与现有配置合并。

    权限说明：
    - 需要 ADMIN 及以上权限
    - 协作者可通过此接口更新服务 Bot 配置

    Returns:
        ApiResponse: 包含更新结果
    """
    try:
        # Get current user from authentication context
        caller_user_id = user.staffId
        caller_nick_name = getattr(user, 'name', None) or caller_user_id

        logger.info(
            f"[update_service_bot_config] Caller: {caller_user_id}({caller_nick_name}), "
            f"Owner: {request.owner_id}, Bot: {request.bot_id}"
        )

        # Validate required fields
        if not request.owner_id or not request.owner_id.strip():
            return ApiResponse(
                success=False,
                message="owner_id 不能为空",
                error_code=400,
                data=None,
            )
        if not request.bot_id or not request.bot_id.strip():
            return ApiResponse(
                success=False,
                message="bot_id 不能为空",
                error_code=400,
                data=None,
            )
        if not request.config_update or not isinstance(request.config_update, dict):
            return ApiResponse(
                success=False,
                message="config_update 必须是非空 JSON 对象",
                error_code=400,
                data=None,
            )

        owner_id = request.owner_id.strip()
        bot_id = request.bot_id.strip()

        # Get current bot ext to merge service_bot_config
        bot = bot_service.get_bot(bot_id, owner_id)
        current_ext = bot.get("ext") or {}
        if isinstance(current_ext, str):
            try:
                import json as _json
                current_ext = _json.loads(current_ext)
            except Exception:
                current_ext = {}

        # Merge service_bot_config (incremental update)
        current_service_bot_config = current_ext.get("service_bot_config") or {}
        if not isinstance(current_service_bot_config, dict):
            current_service_bot_config = {}
        merged_service_bot_config = {**current_service_bot_config, **request.config_update}

        # Build update payload for service_bot_config
        ext_update = {"service_bot_config": merged_service_bot_config}

        # Call service to update bot ext
        bot_service.update_bot_ext(bot_id, owner_id, ext_update)

        logger.info(
            f"[update_service_bot_config] Successfully updated service_bot_config for bot {bot_id} "
            f"of user {owner_id}"
        )

        return ApiResponse(
            success=True,
            message=f"成功更新服务 Bot 配置: {bot_id}",
            data={
                "owner_id": owner_id,
                "bot_id": bot_id,
                "service_bot_config": merged_service_bot_config,
            },
        )

    except BotManagementNotFoundError:
        return ApiResponse(
            success=False,
            message=f"Bot不存在: {request.bot_id}",
            error_code=404,
            data=None,
        )
    except BotPublishServiceError as e:
        return ApiResponse(
            success=False,
            message=str(e),
            error_code=403,
            data=None,
        )
    except Exception as e:
        logger.error(f"[update_service_bot_config] Unexpected error: {e}")
        return ApiResponse(
            success=False,
            message=f"更新服务 Bot 配置失败: {str(e)}",
            error_code=500,
            data=None,
        )
