"""
Channel Management Router
渠道配置管理 API
"""
from fastapi import APIRouter, HTTPException, Query, Depends

from agentclaw.community.adapters.http.channel.schemas import (
    CreateChannelRequest,
    CreateChannelResponse,
    ChannelListResponse,
    ChannelResponse,
    UpdateStatusResponse,
    UpdateChannelRequest,
    OpenClawConfigsResponse,
)
from agentclaw.community.core.bot_collaborator.interceptor import (
    CollaboratorPermissionInterceptor,
    PermissionParams,
    with_interceptors,
)
from agentclaw.community.api.channel_service import ChannelServiceProtocol
from agentclaw.community.core.channel.models import ChannelRecord
from agentclaw.community.di import Injected
from agentclaw.community.di import config as cfg
from agentclaw.community.core.bot_management.services.bot_service import BotNotFoundError
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.auth.dependencies import get_current_user as _get_current_user
from agentclaw.community.log import get_logger

logger = get_logger()

# 导出 get_current_user 以便测试可以覆盖
get_current_user = _get_current_user

router = APIRouter(prefix="/api/channels", tags=["channels"])


# ============================================================================
# 协作者权限提取函数
# ============================================================================
async def extract_from_channel_id(channel_id: str, ctx) -> PermissionParams:
    """从 channel_id 查询 bot_id 和 owner_id.

    Args:
        channel_id: 渠道配置 ID（由拦截器通过表达式注入）
        ctx: 拦截器上下文；``ctx.injector`` 由 ``with_interceptors``
            从 ``request.app.state.injector`` 注入。
    """
    if not channel_id:
        return PermissionParams()

    if ctx.injector is None:
        return PermissionParams()

    try:
        channel_service = ctx.injector.get(ChannelServiceProtocol)
    except Exception:
        return PermissionParams()

    try:
        channel_id_int = int(channel_id)
        record = channel_service.get_channel_by_id(channel_id_int)
        if not record:
            return PermissionParams()

        return PermissionParams(
            bot_id=record.bind_bot_id,
            owner_id=record.identity_id,
        )
    except (ValueError, Exception):
        return PermissionParams()


def record_to_response(record: ChannelRecord) -> ChannelResponse:
    """将 ChannelRecord 转换为响应模型"""
    return ChannelResponse(
        id=record.id,
        type=record.type,
        description=record.description,
        identity_id=record.identity_id,
        bind_bot_id=record.bind_bot_id,
        config=record.config,
        status=record.status,
        gmt_create=record.gmt_create.isoformat() if record.gmt_create else None,
        gmt_modified=record.gmt_modified.isoformat() if record.gmt_modified else None,
        stage=record.stage,
    )


# ============== API Endpoints ==============

@router.get("", response_model=ChannelListResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$bind_bot_id",
    owner_id="$identity_id",
    persist_audit_log=False,  # 只读操作，不需要审计和锁检查
))
async def list_channels(
    type: str = Query(..., description="渠道类型: dingding"),
    identity_id: str = Query(..., description="用户ID"),
    bind_bot_id: str = Query(..., description="绑定bot ID"),
    service: ChannelServiceProtocol = Injected(ChannelServiceProtocol),
    user: AuthenticatedUser = Depends(get_current_user),
) -> ChannelListResponse:
    """
    查询渠道列表
    - 根据 type + identity_id + aideskdingding(默认配置) + bind_bot_id 查询
    """
    records = service.list_channels(
        type=type,
        identity_id=identity_id,
        bind_bot_id=bind_bot_id,
    )
    return ChannelListResponse(
        success=True,
        data=[record_to_response(r) for r in records],
        message="查询成功",
    )


@router.post("", response_model=CreateChannelResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$request.bind_bot_id",
    owner_id="$request.identity_id",
))
async def create_channel(
    request: CreateChannelRequest,
    service: ChannelServiceProtocol = Injected(ChannelServiceProtocol),
    aix_config: cfg.AixConfig = Injected(cfg.AixConfig),
    user: AuthenticatedUser = Depends(get_current_user),
) -> CreateChannelResponse:
    """
    创建渠道配置
    """
    if not request.config.aix_preview_url:
        request.config.aix_preview_url = aix_config.preview_url
    channel_id = service.create_channel(
        type=request.type,
        description=request.description,
        identity_id=request.identity_id,
        bind_bot_id=request.bind_bot_id,
        config=request.config.model_dump(),
        status="0",  # 默认未生效
        stage=request.stage,
    )
    return CreateChannelResponse(
        success=True,
        data={"id": channel_id},
        message="创建成功",
    )


@router.post("/{channel_id}/delete", response_model=UpdateStatusResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    params_extractor=extract_from_channel_id,
    extractor_params={"channel_id": "$channel_id"},
))
async def delete(
    channel_id: int,
    service: ChannelServiceProtocol = Injected(ChannelServiceProtocol),
    user: AuthenticatedUser = Depends(get_current_user),
) -> UpdateStatusResponse:
    # 与公开 API 的 delete 同路：remove_channel 会 best-effort 清理 bcn_gateway
    # 行的 BCS 绑定，裸 delete 只删行、会孤儿化绑定。
    await service.remove_channel(channel_id)
    return UpdateStatusResponse(success=True, message='成功')


@router.post("/{channel_id}/updateStatus", response_model=UpdateStatusResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    params_extractor=extract_from_channel_id,
    extractor_params={"channel_id": "$channel_id"},
))
async def update_status(
    channel_id: int,
    status: str = Query(..., description="状态: 1=生效, 0=失效"),
    service: ChannelServiceProtocol = Injected(ChannelServiceProtocol),
    user: AuthenticatedUser = Depends(get_current_user),
) -> UpdateStatusResponse:
    """
    更新渠道状态
    - status=1: 生效
    - status=0: 失效
    """
    # 先检查渠道是否存在
    record = service.get_channel_by_id(channel_id)
    if not record:
        raise HTTPException(status_code=404, detail="渠道配置不存在")

    # 持久化状态 + 同步到运行容器。容器类型决定顺序：
    # - teclaw: 先落库再投递（重组读库，best-effort，不抛错）
    # - openclaw: 先写 openclaw.json（可能抛错，fail-closed）再落库
    try:
        await service.set_channel_status(channel_id, status)
    except BotNotFoundError as e:
        logger.exception(f"[update_status] BotNotFoundError: channel_id={channel_id}, status={status}, error={e}")
        raise HTTPException(status_code=400, detail=f"同步失败：绑定的Bot不存在 - {e}") from e
    except FileNotFoundError as e:
        logger.exception(f"[update_status] FileNotFoundError: channel_id={channel_id}, status={status}, error={e}")
        raise HTTPException(status_code=500, detail=f"同步失败：openclaw.json不存在 - {e}") from e
    except ValueError as e:
        logger.exception(f"[update_status] ValueError: channel_id={channel_id}, status={status}, error={e}")
        raise HTTPException(status_code=400, detail=f"同步失败：{e}") from e
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[update_status] Unexpected error: channel_id={channel_id}, status={status}, error={e}")
        raise HTTPException(status_code=500, detail=f"同步失败：{e}") from e

    message = "配置已生效" if status == "1" else "配置已失效"
    return UpdateStatusResponse(success=True, message=message)


@router.post("/{channel_id}/update", response_model=CreateChannelResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$request.bind_bot_id",
    owner_id="$request.identity_id",
))
async def update_channel(
    channel_id: int,
    request: UpdateChannelRequest,
    service: ChannelServiceProtocol = Injected(ChannelServiceProtocol),
    aix_config: cfg.AixConfig = Injected(cfg.AixConfig),
    user: AuthenticatedUser = Depends(get_current_user),
) -> CreateChannelResponse:
    """
    根据 id 更新渠道配置（POST 方式）
    """
    # 先检查渠道是否存在
    record = service.get_channel_by_id(channel_id)
    if not record:
        raise HTTPException(status_code=404, detail="渠道配置不存在")
    # 保留已存储的 client_secret（前端更新时不回传密文）。必须在存在性检查之后，
    # 否则渠道不存在时会 NoneType 解引用崩成 500 而非返回 404。
    request.config.client_secret = record.config['client_secret']
    if not request.config.aix_preview_url:
        request.config.aix_preview_url = aix_config.preview_url

    # 更新数据库
    service.update_channel(
        channel_id=channel_id,
        type=request.type,
        description=request.description,
        identity_id=request.identity_id,
        bind_bot_id=request.bind_bot_id,
        config=request.config.model_dump(),
        status=request.status,
        stage=request.stage,
    )

    # 如果渠道已生效，同步到运行容器（config 已先落库，teclaw 重组读到新配置）
    if request.status == "1":
        try:
            await service.sync_active_channel(channel_id)
        except BotNotFoundError as e:
            logger.exception(f"[update_channel] BotNotFoundError: channel_id={channel_id}, error={e}")
            raise HTTPException(status_code=400, detail=f"同步失败：绑定的Bot不存在 - {e}") from e
        except FileNotFoundError as e:
            logger.exception(f"[update_channel] FileNotFoundError: channel_id={channel_id}, error={e}")
            raise HTTPException(status_code=500, detail=f"同步失败：openclaw.json不存在 - {e}") from e
        except ValueError as e:
            logger.exception(f"[update_channel] ValueError: channel_id={channel_id}, error={e}")
            raise HTTPException(status_code=400, detail=f"同步失败：{e}") from e
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"[update_channel] Unexpected error: channel_id={channel_id}, error={e}")
            raise HTTPException(status_code=500, detail=f"同步失败：{e}") from e

    return CreateChannelResponse(
        success=True,
        data={"id": channel_id},
        message="更新成功",
    )


@router.get("/openclaw-configs", response_model=OpenClawConfigsResponse)
async def get_openclaw_configs(
    bot_id: str = Query(..., description="Bot ID"),
    owner_id: str = Query(..., description="用户身份 ID"),
    service: ChannelServiceProtocol = Injected(ChannelServiceProtocol),
    user: AuthenticatedUser = Depends(get_current_user),
) -> OpenClawConfigsResponse:
    """获取多环境 OpenClaw 配置

    根据 bot_id 和 owner_id 查询钉钉配置，生成 verify 和 online 两个环境的配置文件。
    仅允许 owner 本人访问。
    """
    # 仅允许 owner 本人访问
    if user.staffId != owner_id:
        raise HTTPException(status_code=403, detail="无权限访问此资源")
    try:
        configs = await service.generate_openclaw_configs(
            bot_id=bot_id,
            owner_id=owner_id,
        )
        return OpenClawConfigsResponse(
            verify=configs.verify,
            online=configs.online,
            eval=configs.eval,
        )
    except FileNotFoundError as e:
        logger.exception(f"[get_openclaw_configs] FileNotFoundError: bot_id={bot_id}, owner_id={owner_id}, error={e}")
        raise HTTPException(status_code=500, detail=f"生成配置失败：openclaw.json不存在 - {e}") from e
    except BotNotFoundError as e:
        logger.exception(f"[get_openclaw_configs] BotNotFoundError: bot_id={bot_id}, owner_id={owner_id}, error={e}")
        raise HTTPException(status_code=400, detail=f"生成配置失败：Bot不存在 - {e}") from e
    except Exception as e:
        logger.exception(f"[get_openclaw_configs] Unexpected error: bot_id={bot_id}, owner_id={owner_id}, error={e}")
        raise HTTPException(status_code=500, detail=f"生成配置失败：{e}") from e


@router.get("/{channel_id}", response_model=ChannelListResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    params_extractor=extract_from_channel_id,
    extractor_params={"channel_id": "$channel_id"},
    persist_audit_log=False,  # 只读操作，不需要审计和锁检查
))
async def get_channel(
    channel_id: int,
    service: ChannelServiceProtocol = Injected(ChannelServiceProtocol),
    user: AuthenticatedUser = Depends(get_current_user),
) -> ChannelListResponse:
    """
    根据 id 查询渠道详情
    """
    record = service.get_channel_by_id(channel_id)
    if not record:
        raise HTTPException(status_code=404, detail="渠道配置不存在")

    return ChannelListResponse(
        success=True,
        data=[record_to_response(record)],
        message="查询成功",
    )
