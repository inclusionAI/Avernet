"""Desktop device & bot management API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.api.baas_service import BaasServiceProtocol
from agentclaw.community.api.desktop_bot_service import DesktopBotServiceProtocol
from agentclaw.community.core.desktop_bot.services.desktop_bot_service import DesktopBotServiceError
from agentclaw.community.core.errors import DomainError, NotFound
from agentclaw.community.core.service_bot.services.baas_service import BaasServiceError
from agentclaw.community.core.workspace.constants import SUPPORTED_ENGINE_TYPES
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.auth_relationship import AuthRelationshipError, AuthRelationshipPlugin
from agentclaw.community.plugin_api.passport import PassportPlugin, PassportError

logger = get_logger()

bot_router = APIRouter(prefix="/api/desktop/bots", tags=["desktop-bots"])
device_router = APIRouter(prefix="/api/desktop/devices", tags=["desktop-devices"])


class CreateDesktopBotRequest(BaseModel):
    """Create desktop bot request body."""
    bot_name: str
    machine_id: str
    bot_desc: str | None = None
    mount_path: str | None = None
    avatar_url: str | None = None
    engine_type: str | None = Field(
        default=None,
        description=f"引擎类型，如 {', '.join(SUPPORTED_ENGINE_TYPES)}；不传则默认 openclaw",
    )


class OpenFolderRequest(BaseModel):
    """Open folder request body."""
    folder_path: str | None = Field(
        default=None,
        max_length=512,
        description="要打开的目录路径。支持绝对路径或相对路径（相对于 workspace 根目录）",
    )

    @field_validator("folder_path")
    @classmethod
    def validate_no_traversal(cls, v: str | None) -> str | None:
        if v is not None and ".." in v:
            raise ValueError("folder_path must not contain directory traversal (..)")
        return v


@device_router.get("/{machine_id}/files", response_model=dict)
def list_device_files(
    machine_id: str,
    dir: str = Query("~/Desktop", description="Directory path to list (relative, e.g. ~/Desktop)"),
    user=Depends(get_current_user),
    service: DesktopBotServiceProtocol = Injected(DesktopBotServiceProtocol),
):
    """查询宿主机目录树，用于选择挂载路径。

    通过 BaaS get_machine_res_dirs 接口向 mng daemon 查询目录树结构，
    返回树形结构 {name, children?}，目录有 children，文件只有 name。
    """
    staff_id = user.staffId
    if not staff_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        result = service.list_directory(
            machine_id=machine_id,
            dir=dir,
        )
    except DesktopBotServiceError as e:
        logger.error("[desktop.list_files] Desktop bot service error: %s", e)
        return {"success": False, "message": f"查询目录失败: {e}", "error_code": 500}
    except Exception as e:
        logger.error("[desktop.list_files] Unexpected error: %s", e)
        return {"success": False, "message": f"查询目录失败: {e}", "error_code": 500}

    return {"success": True, "data": result}


@bot_router.post("", response_model=dict)
def create_desktop_bot(
    body: CreateDesktopBotRequest = Body(...),
    user=Depends(get_current_user),
    service: DesktopBotServiceProtocol = Injected(DesktopBotServiceProtocol),
):
    """创建桌面 Bot。

    两段式创建入口：先调用 apply_passport_before_create 申请 Passport 授权，
    前端完成授权后再调用 auth-status 接口，由 create_after_authorization 完成 BAAS 创建和本地入库。
    """
    staff_id = user.staffId
    if not staff_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    logger.info(
        "[desktop.create] staff_id=%s bot_name=%s", staff_id, body.bot_name,
    )

    try:
        result = service.apply_passport_before_create(
            bot={
                "bot_name": body.bot_name,
                "bot_desc": body.bot_desc,
            },
            user_id=staff_id,
            machine_id=body.machine_id,
            mount_path=body.mount_path,
            avatar_url=body.avatar_url,
            engine_type=body.engine_type,
        )

        # 桌面版两段式：先返回 need_authorization，前端授权后再调 auth-status
        if result.get("need_authorization"):
            return {
                "success": False,
                "message": "需要授权",
                "error_code": 401,
                "data": result,
            }

        return {"success": True, "data": result}

    except PassportError as e:
        logger.error("[desktop.create] TCAuth error: %s", e)
        return {
            "success": False,
            "message": f"授权申请异常: {e}",
            "error_code": 5400,
        }
    except DesktopBotServiceError as e:
        logger.error("[desktop.create] Desktop bot service error: %s", e)
        return {
            "success": False,
            "message": f"创建桌面 Bot 失败: {e}",
            "error_code": 500,
        }
    except DomainError:
        raise
    except Exception as e:
        logger.error("[desktop.create] Unexpected error: %s", e)
        return {
            "success": False,
            "message": f"创建桌面 Bot 失败: {e}",
            "error_code": 500,
        }


@bot_router.post("/auth-status", response_model=dict)
def auth_status_desktop_bot(
    request: dict = Body(...),
    user=Depends(get_current_user),
    service: DesktopBotServiceProtocol = Injected(DesktopBotServiceProtocol),
    passport_plugin: PassportPlugin = Injected(PassportPlugin),
    auth_rel_plugin: AuthRelationshipPlugin = Injected(AuthRelationshipPlugin),
):
    """轮询桌面 Bot 的授权状态（两段式第二步）。

    查询 Passport 状态：PENDING → 继续等待；ISSUED → 调用 create_after_authorization 完成创建。
    """
    staff_id = user.staffId
    if not staff_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    nick_name = user.nickName or staff_id

    bot_id = request.get("bot_id")
    if not bot_id:
        return {"success": False, "message": "bot_id 是必填参数", "error_code": 400}

    logger.info(
        "[desktop.auth_status] staff_id=%s bot_id=%s", staff_id, bot_id,
    )

    try:
        auth_status = passport_plugin.query_auth_status(
            bot_id=bot_id,
            owner_workno=staff_id,
        )
        if not auth_status:
            return {"success": False, "message": "查询授权状态失败", "error_code": 500}

        status = auth_status.get("status")

        if status == "PENDING":
            return {
                "success": True,
                "data": {"status": "PENDING", "message": "授权处理中"},
            }

        if status == "ISSUED":
            result = service.create_after_authorization(
                bot={
                    "bot_id": bot_id,
                    "bot_name": request.get("bot_name"),
                    "bot_desc": request.get("bot_desc"),
                    "avatar_url": request.get("avatar_url"),
                },
                user_id=staff_id,
                machine_id=request.get("machine_id"),
                mount_path=request.get("mount_path"),
                engine_type=request.get("engine_type"),
            )
            auth_rel_plugin.create_relationship(
                work_no=staff_id,
                agent_code=result["agent_code"],
                description="Bot owner default authorization",
                operator_work_no=staff_id,
                operator_name=nick_name,
            )
            return {
                "success": True,
                "data": {"status": "ISSUED", "bot": result},
            }

        return {
            "success": False,
            "message": f"授权状态异常: {status}",
            "error_code": 400,
            "data": {"status": status},
        }

    except PassportError as e:
        logger.error("[desktop.auth_status] TCAuth error: %s", e)
        return {
            "success": False,
            "message": f"授权查询异常: {e}",
            "error_code": 5400,
        }
    except DesktopBotServiceError as e:
        logger.error("[desktop.auth_status] Desktop bot service error: %s", e)
        return {
            "success": False,
            "message": f"创建桌面 Bot 失败: {e}",
            "error_code": 500,
        }
    except AuthRelationshipError as e:
        logger.error("[desktop.auth_status] auth_relationship error: %s", e)
        return {
            "success": False,
            "message": f"授权关系绑定失败: {e}",
            "error_code": 500,
            "data": {"status": "ISSUED", "bot": result},
        }
    except DomainError:
        raise
    except Exception as e:
        logger.error("[desktop.auth_status] Unexpected error: %s", e)
        return {
            "success": False,
            "message": f"创建桌面 Bot 失败: {e}",
            "error_code": 500,
        }


@device_router.get("", response_model=dict)
def list_desktop_devices(
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    status: str | None = Query(None, description="Filter by status (PENDING/ACTIVE/OFFLINE/RELEASED)"),
    user=Depends(get_current_user),
    service: DesktopBotServiceProtocol = Injected(DesktopBotServiceProtocol),
):
    """查询当前用户的桌面注册设备列表.

    通过 BAAS API 查询用户的所有 Bot，前端展示设备列表供用户选择和管理。
    """
    staff_id = user.staffId
    if not staff_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        total, device_items = service.list_devices(
            user_id=staff_id,
            page=page,
            page_size=page_size,
            status=status,
        )
    except DesktopBotServiceError as e:
        logger.error("[desktop.list_devices] Desktop bot service error: %s", e)
        return {"success": False, "message": f"查询设备列表失败: {e}", "error_code": 500}
    except Exception as e:
        logger.error("[desktop.list_devices] Unexpected error: %s", e)
        return {"success": False, "message": f"查询设备列表失败: {e}", "error_code": 500}

    items: list[dict[str, Any]] = []
    for device in device_items:
        items.append({
            "machine_id": device.get("machine_id", ""),
            "machine_name": device.get("machine_name", ""),
            "status": device.get("status", ""),
            "last_alive_at": device.get("last_online_at"),
            "created_at": device.get("created_at"),
        })

    logger.info(
        "[desktop.list] staff_id=%s total=%d page=%d page_size=%d",
        staff_id, total, page, page_size,
    )

    return {
        "total": total,
        "items": items,
    }


@bot_router.post("/{bot_id}/restart", response_model=dict)
def restart_desktop_bot(
    bot_id: str,
    user=Depends(get_current_user),
    service: DesktopBotServiceProtocol = Injected(DesktopBotServiceProtocol),
):
    """重启桌面 Bot。

    调用 BAAS restart API，BAAS 通知容器重启，更新本地 AC 表状态。
    """
    staff_id = user.staffId
    if not staff_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    logger.info(
        "[desktop.restart] staff_id=%s bot_id=%s", staff_id, bot_id,
    )

    try:
        service.verify_ownership(bot_id=bot_id, user_id=staff_id)
    except NotFound:
        return {"success": False, "message": "Bot 不存在或无权操作", "error_code": 404}

    try:
        result = service.restart(
            bot_id=bot_id,
            user_id=staff_id,
        )
    except DesktopBotServiceError as e:
        logger.error("[desktop.restart] Desktop bot service error: %s", e)
        return {"success": False, "message": f"重启桌面 Bot 失败: {e}", "error_code": 500}
    except Exception as e:
        logger.error("[desktop.restart] Unexpected error: %s", e)
        return {"success": False, "message": f"重启桌面 Bot 失败: {e}", "error_code": 500}

    return {"success": True, "data": result}


@bot_router.post("/{bot_id}/open-folder", response_model=dict)
def open_folder(
    bot_id: str,
    body: OpenFolderRequest | None = Body(default=None),
    user=Depends(get_current_user),
    service: DesktopBotServiceProtocol = Injected(DesktopBotServiceProtocol),
):
    """在宿主机上打开 Bot 的工作目录。

    调用 BaaS API 通知 mng daemon 使用系统文件管理器打开 bot workspace 目录。
    支持通过 folder_path 参数指定子目录路径（相对路径相对于 workspace 根目录）。
    """
    staff_id = user.staffId
    if not staff_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    logger.info(
        "[desktop.open_folder] staff_id=%s bot_id=%s folder_path=%s",
        staff_id, bot_id, body.folder_path if body else None,
    )

    try:
        service.verify_ownership(bot_id=bot_id, user_id=staff_id)
    except NotFound:
        return {"success": False, "message": "Bot 不存在或无权操作", "error_code": 404}

    try:
        result = service.open_folder(
            bot_id=bot_id,
            user_id=staff_id,
            folder_path=body.folder_path if body else None,
        )
    except DesktopBotServiceError as e:
        logger.error("[desktop.open_folder] Desktop bot service error: %s", e)
        return {"success": False, "message": f"打开文件夹失败: {e}", "error_code": 500}
    except Exception as e:
        logger.error("[desktop.open_folder] Unexpected error: %s", e)
        return {"success": False, "message": f"打开文件夹失败: {e}", "error_code": 500}

    return {"success": True, "data": result}


@bot_router.get("/{bot_uuid}/start-progress", response_model=dict)
def get_bot_start_progress(
    bot_uuid: str,
    device_affinity: str | None = Query(None, description="Device affinity key for sticky device selection"),
    user=Depends(get_current_user),
    baas_service: BaasServiceProtocol = Injected(BaasServiceProtocol),
):
    """查询桌面 Bot 启动进度。

    透传 BaaS GET /api/v1/bots/{bot_uuid}/start-progress 接口，
    返回容器启动进度信息（progress 等字段）。
    """
    staff_id = user.staffId
    if not staff_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    logger.info(
        "[desktop.start_progress] staff_id=%s bot_uuid=%s",
        staff_id, bot_uuid,
    )

    try:
        result = baas_service.get_bot_start_progress(
            bot_uuid=bot_uuid,
            device_affinity=device_affinity,
        )
    except BaasServiceError as e:
        logger.error("[desktop.start_progress] BaaS service error: %s", e)
        return {"success": False, "message": f"查询启动进度失败: {e}", "error_code": 500}
    except Exception as e:
        logger.error("[desktop.start_progress] Unexpected error: %s", e)
        return {"success": False, "message": f"查询启动进度失败: {e}", "error_code": 500}

    return {"success": True, "data": result}


@bot_router.delete("/{bot_id}", response_model=dict)
def delete_desktop_bot(
    bot_id: str,
    user=Depends(get_current_user),
    service: DesktopBotServiceProtocol = Injected(DesktopBotServiceProtocol),
):
    """删除桌面 Bot。

    调用 BAAS destroy API，BAAS 通知容器销毁，更新本地 AC 表。
    即使 BaaS 或 Passport 调用失败，本地数据仍会清理。
    """
    staff_id = user.staffId
    if not staff_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    logger.info(
        "[desktop.delete] staff_id=%s bot_id=%s", staff_id, bot_id,
    )

    try:
        service.verify_ownership(bot_id=bot_id, user_id=staff_id)
    except NotFound:
        return {"success": False, "message": "Bot 不存在或无权操作", "error_code": 404}

    try:
        result = service.delete(
            bot_id=bot_id,
            user_id=staff_id,
        )
    except DesktopBotServiceError as e:
        logger.error("[desktop.delete] Desktop bot service error: %s", e)
        return {"success": False, "message": f"删除桌面 Bot 失败: {e}", "error_code": 500}
    except Exception as e:
        logger.error("[desktop.delete] Unexpected error: %s", e)
        return {"success": False, "message": f"删除桌面 Bot 失败: {e}", "error_code": 500}

    return {"success": True, "data": result}
