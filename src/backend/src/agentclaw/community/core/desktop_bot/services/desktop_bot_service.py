"""DesktopBotService — 桌面版 Bot 生命周期管理。

与服务 Bot 不同，桌面 Bot 不经过 publish 状态机，直接调用 BAAS API
并同步写入本地数据库。
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

import httpx

from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.desktop_bot.status_mapping import StatusDecision
from agentclaw.community.core.devices.repository.protocol import DeviceBindingRepository
from agentclaw.community.core.devices.services.device_service import DeviceService
from agentclaw.community.core.mcp.services.passport_scope import filter_passport_mcp_codes
from agentclaw.community.core.service_bot.services.baas_service import (
    BaasService,
    BaasServiceError,
    BotConfig,
    BotDeployConfig,
)
from agentclaw.community.core.skill_center.factories import SkillSetServiceFactory
from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE, SUPPORTED_ENGINE_TYPES
from agentclaw.community.core.errors import NotFound
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.passport import PassportPlugin, PassportError
from agentclaw.community.utils.avernet_tenant import bind_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env

if TYPE_CHECKING:
    # Type-only: runtime ``from agentclaw.community.di import config`` triggers
    # di/__init__ → container → desktop_bot_module → lifecycle →
    # desktop_bot_service, forming a cycle. The injected BaasConfig is
    # resolved by DesktopBotModule's @provider, so this annotation is
    # never accessed at runtime.
    from agentclaw.community.di import config as cfg

logger = get_logger()


def _generate_request_id(
    bot_id: str,
    entity_id: str,
    entity_type: str,
    env: str,
    action: str,
) -> str:
    raw = f"{entity_id}_{entity_type}_{bot_id}_{env}_{action}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _to_device_display_status(status: str) -> str:
    """将 BaaS 机器状态映射为设备展示状态."""
    status_map = {
        "ONLINE": "ACTIVE",
        "OFFLINE": "OFFLINE",
        "DISABLED": "RELEASED",
    }
    return status_map.get(status.upper(), status.upper())


def _format_datetime(value: object) -> str:
    """将 datetime/字符串 安全格式化为 ISO-8601 字符串."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


class DesktopBotServiceError(Exception):
    """Desktop bot service error."""


class DesktopBotOrphanError(DesktopBotServiceError):
    """BaaS confirmed bot not found (orphan)."""


class DesktopBotService:
    """桌面版 Bot 生命周期管理服务。

    负责桌面 Bot 的创建、重启、删除，通过 BAAS API 操作远端容器，
    并同步维护本地数据库记录。
    """

    def __init__(
        self,
        baas_service: BaasService,
        passport_plugin: PassportPlugin,
        device_binding_repo: DeviceBindingRepository,
        bot_repository: BotRepository,
        baas_config: cfg.BaasConfig,
        device_service: "DeviceService",
        skill_set_factory: SkillSetServiceFactory,
    ):
        self._baas = baas_service
        self._passport = passport_plugin
        self._binding_repo = device_binding_repo
        self._bot_repo = bot_repository
        self._device_service = device_service
        self._skill_set_factory = skill_set_factory
        self._baas_api_base = (
            baas_config.api_base_url_pre
            if get_current_env() == "pre"
            else baas_config.api_base_url
        )
        self._tenant = baas_config.tenant
        self._desktop_template_uuid = baas_config.desktop_template_uuid
        self._desktop_ttl_minutes = getattr(baas_config, "desktop_ttl_minutes", 0)

    # ── list & status check ──────────────────────────────────────────────

    _CHECK_STATUSES = ("PENDING", "ACTIVE", "OFFLINE", "RELEASING", "FAILED")

    def list_user_bots(self, user_id: str) -> list[dict[str, Any]]:
        """列出指定用户的桌面 Bot。"""
        all_bots: list[dict[str, Any]] = []
        for status in self._CHECK_STATUSES:
            try:
                _, bots = self._bot_repo.search_bots(
                    bot_type="desktop",
                    owner_id=user_id,
                    bot_status=status,
                    page=1,
                    page_size=100,
                )
                all_bots.extend(bots)
            except Exception as e:
                logger.warning(
                    "[DesktopBotService.list_user_bots] query failed "
                    "user_id=%s status=%s: %s", user_id, status, e,
                )
        return all_bots

    def check_user_bots_status(self, user_id: str) -> None:
        """检查指定用户桌面 Bot 的设备状态并更新本地记录。

        对每个 bot 查询 BaaS device-status，通过 status_mapping 决策逻辑
        判断目标状态，支持孤岛回收（BaaS 404）和 RELEASING 中间态。
        """
        from agentclaw.community.core.desktop_bot.status_mapping import map_baas_to_local

        bots = self.list_user_bots(user_id)
        if not bots:
            return

        for bot in bots:
            bot_id = bot.get("bot_id", "")
            device_id = bot.get("device_id", "")
            current_status = bot.get("status", "")
            owner_id = bot.get("owner_id", "")
            binding_id = bot.get("binding_id")

            if not device_id:
                continue

            try:
                baas_response = self.query_device_status(device_id)
                confirmed_orphan = False
            except DesktopBotOrphanError:
                baas_response = None
                confirmed_orphan = True
            except DesktopBotServiceError as e:
                logger.warning(
                    "[health-check] query failed bot=%s: %s", bot_id, e,
                )
                continue

            decision = map_baas_to_local(
                baas_response=baas_response,
                current_local_status=current_status,
                confirmed_orphan=confirmed_orphan,
            )
            self._apply_decision(bot_id, owner_id, binding_id, current_status, decision)

    # ── decision application ────────────────────────────────────────────

    def _apply_decision(
        self,
        bot_id: str,
        owner_id: str,
        binding_id: int | str | None,
        current_status: str,
        decision: "StatusDecision",
    ) -> None:
        """Apply a StatusDecision: status → ext → soft_delete (strict order)."""

        if decision.log_context:
            logger.info("[health-check] bot=%s decision=%s", bot_id, decision.log_context)
        if decision.target_status and decision.target_status != current_status:
            self._update_local_status(
                binding_id=str(binding_id) if binding_id else "",
                bot_id=bot_id,
                owner_id=owner_id,
                status=decision.target_status,
            )
        if decision.release_reason:
            self._merge_bot_ext(bot_id, owner_id, {
                "release_reason": decision.release_reason,
                "released_at": datetime.now().isoformat(),
            })
        if decision.soft_delete:
            try:
                self._bot_repo.soft_delete_by_owner(bot_id=bot_id, owner_id=owner_id)
            except Exception as e:
                logger.warning("[health-check] soft delete failed bot=%s: %s", bot_id, e)
        # Stamp last_health_check for dedup with periodic scan
        self._merge_bot_ext(bot_id, owner_id, {
            "last_health_check": datetime.now().isoformat(),
        })

    def _merge_bot_ext(self, bot_id: str, owner_id: str, patch: dict) -> None:
        """Read-modify-write bot ext field, merging only specified keys."""
        try:
            bot = self._bot_repo.get_by_id_and_owner(bot_id=bot_id, owner_id=owner_id)
            if not bot:
                return
            ext_raw = bot.get("ext") or {}
            if isinstance(ext_raw, str):
                try:
                    ext_raw = json.loads(ext_raw)
                except json.JSONDecodeError:
                    ext_raw = {}
            ext = dict(ext_raw)
            ext.update(patch)
            self._bot_repo.update_by_owner(
                bot_id=bot_id,
                owner_id=owner_id,
                update_data={"ext": ext},
            )
        except Exception as e:
            logger.warning("[_merge_bot_ext] failed bot=%s: %s", bot_id, e)

    # ── verify_ownership ─────────────────────────────────────────────────

    def verify_ownership(self, *, bot_id: str, user_id: str) -> None:
        """验证当前用户是否为该 Bot 的 owner，否则抛出 403。"""
        logger.info(
            "[DesktopBotService.verify_ownership] bot_id=%s user_id=%s",
            bot_id, user_id,
        )
        bot = self._bot_repo.get_by_id_and_owner(bot_id=bot_id, owner_id=user_id)
        if not bot:
            raise NotFound("Bot not found")
        # ownership already verified by get_by_id_and_owner

    # ── list_directory ──────────────────────────────────────────────────

    def list_directory(
        self,
        *,
        machine_id: str,
        dir: str = "",
    ) -> dict[str, Any]:
        """查询宿主机目录树，用于选择挂载路径。

        通过 BaaS get_machine_res_dirs 接口向 mng daemon 查询目录树结构。
        返回树形结构 {name, children?}，目录有 children 字段，文件只有 name。

        Args:
            machine_id: 目标机器 ID
            dir: 要列出的目录路径（相对路径，如 ~），不能以 / 开头

        Returns:
            目录树字典，包含 name 和可选的 children
        """
        logger.info(
            "[DesktopBotService.list_directory] machine_id=%s dir=%s",
            machine_id, dir,
        )

        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.get(
                    f"{self._baas_api_base}/api/v1/local/machines/{machine_id}/res-dirs",
                    params={"dir": dir},
                )
                response.raise_for_status()

                response_data = response.json()
                logger.info(
                    "[DesktopBotService.list_directory] BaaS raw response: %s",
                    response_data,
                )
                if response_data.get("code") != 0:
                    raise DesktopBotServiceError(
                        f"BaaS API error: {response_data.get('message', 'Unknown error')}"
                    )

                data = response_data.get("data") or {}
                logger.info(
                    "[DesktopBotService.list_directory] machine_id=%s dir=%s ok",
                    machine_id, dir,
                )
                return data

        except httpx.HTTPStatusError as e:
            logger.error(
                "[DesktopBotService.list_directory] HTTP error: %s - %s",
                e.response.status_code, e.response.text,
            )
            raise DesktopBotServiceError(
                f"BaaS API error: {e.response.status_code} - {e.response.text}"
            )
        except DesktopBotServiceError:
            raise
        except Exception as e:
            logger.error(
                "[DesktopBotService.list_directory] Failed: %s", e,
            )
            raise DesktopBotServiceError(f"Failed to list directory: {e}")

    # ── open_folder ────────────────────────────────────────────────────

    def open_folder(
        self,
        *,
        bot_id: str,
        user_id: str,
        folder_path: str | None = None,
    ) -> dict[str, Any]:
        """在宿主机上打开 Bot 工作目录。

        通过 BaaS API 通知 mng daemon 使用系统文件管理器打开
        bot 的 workspace 目录。

        Args:
            bot_id: Bot ID
            user_id: 操作者用户 ID
            folder_path: 要打开的目录路径。支持绝对路径或相对路径
                （相对于 workspace 根目录）。None 时打开默认 workspace 根目录。

        Returns:
            dict: bot_id, workspace_path

        Raises:
            DesktopBotServiceError: bot 不存在或 BaaS 调用失败
        """
        bot = self._bot_repo.get_by_id_and_owner(bot_id=bot_id, owner_id=user_id)
        if not bot:
            raise DesktopBotServiceError(f"Desktop bot not found: bot_id={bot_id}")

        device_id = bot.get("device_id")
        if not device_id:
            raise DesktopBotServiceError(
                f"Desktop bot has no device_id: bot_id={bot_id}"
            )

        logger.info(
            "[DesktopBotService.open_folder] bot_id=%s device_id=%s folder_path=%s",
            bot_id, device_id, folder_path,
        )

        try:
            self._baas.open_folder_bot(bot_uuid=device_id, folder_path=folder_path)
        except BaasServiceError as e:
            raise DesktopBotServiceError(f"Failed to open folder: {e}")

        return {"bot_id": bot_id}

    # ── list_devices ────────────────────────────────────────────────────

    def list_devices(
        self,
        *,
        user_id: str,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
    ) -> tuple[int, list[dict[str, Any]]]:
        """查询用户的本地注册设备列表。

        调用 BaaS API: GET /api/v1/local/users/{user_id}/machines
        """
        logger.info(
            "[DesktopBotService.list_devices] user_id=%s page=%d page_size=%d status=%s",
            user_id, page, page_size, status,
        )

        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.get(
                    f"{self._baas_api_base}/api/v1/local/users/{user_id}/machines",
                )
                response.raise_for_status()

                response_data = response.json()
                if response_data.get("code") != 0:
                    raise DesktopBotServiceError(
                        f"BaaS API error: {response_data.get('message', 'Unknown error')}"
                    )

                machines = response_data.get("data") or []
                logger.info(
                    "[DesktopBotService.list_devices] BaaS raw response: %s",
                    response_data,
                )
                all_devices = []
                for m in machines:
                    info = m.get("machine_info") or {}
                    all_devices.append({
                        "machine_id": m.get("machine_id", ""),
                        "machine_name": info.get("machine_name", m.get("machine_id", "")),
                        "hostname": info.get("machine_name", ""),
                        "status": _to_device_display_status(m.get("status", "")),
                        "ip_address": info.get("ip_address", ""),
                        "os_version": info.get("os_version", ""),
                        "last_online_at": _format_datetime(m.get("last_heartbeat")),
                        "created_at": info.get("created_at", ""),
                    })

            # 按状态过滤
            if status:
                filtered = [d for d in all_devices if d["status"] == status]
            else:
                filtered = list(all_devices)

            # 分页
            total = len(filtered)
            start = (page - 1) * page_size
            end = start + page_size
            items = filtered[start:end]

            logger.info(
                "[DesktopBotService.list_devices] total=%d returned=%d",
                total, len(items),
            )
            return total, items

        except httpx.HTTPStatusError as e:
            logger.error(
                "[DesktopBotService.list_devices] HTTP error: %s - %s",
                e.response.status_code, e.response.text,
            )
            raise DesktopBotServiceError(
                f"BaaS API error: {e.response.status_code} - {e.response.text}"
            )
        except DesktopBotServiceError:
            raise
        except Exception as e:
            logger.error(
                "[DesktopBotService.list_devices] Failed to list devices: %s", e,
            )
            raise DesktopBotServiceError(f"Failed to list devices: {e}")

    # ── create ──────────────────────────────────────────────────────────

    def apply_passport_before_create(
        self,
        bot: dict[str, Any],
        user_id: str,
        machine_id: str,
        mount_path: str | None = None,
        avatar_url: str | None = None,
        engine_type: str | None = None,
    ) -> dict[str, Any]:
        """在创建桌面 Bot 之前申请 Passport，返回前端授权链接。

        桌面版采用两段式创建：
        1. 本方法向 Passport 服务申请许可证，返回 need_authorization=True 及授权链接。
        2. 前端引导用户完成授权后，调用 create_after_authorization 继续创建流程。

        Args:
            bot: Bot 信息字典，包含 bot_name, bot_desc, 可选 bot_id
            user_id: 创建者用户 ID
            machine_id: 目标设备节点 ID
            mount_path: 用户自定义本地磁盘挂载路径（可选）
            avatar_url: Bot 头像 URL（可选）
            engine_type: 引擎类型（可选，默认 openclaw）

        Returns:
            dict: need_authorization, bot_id, iframe_url, redirect_url
        """
        bot_id = bot.get("bot_id", f"desktop_bot_{int(time.time() * 1000)}")

        # 前置校验 mount_path，与 BaaS 侧规则保持一致
        self._validate_mount_path(mount_path)

        logger.info(
            "[DesktopBotService.apply_passport_before_create] bot_name=%s user_id=%s machine_id=%s "
            "mount_path=%s",
            bot.get("bot_name"), user_id, machine_id, mount_path,
        )

        workspace_path = f"~/.teamclaw/boxes/{bot_id}"

        # Step 1: 获取机器信息并申请 Passport
        # 桌面版需要 device_token 用于许可证绑定，阻塞流程
        machine_info = self._fetch_machine_info(machine_id)
        device_token = machine_info.get("device_token")
        if not device_token:
            raise DesktopBotServiceError(
                f"Failed to get device_token for machine {machine_id}"
            )
        passport_result = self._apply_passport(
            bot_id=bot_id,
            user_id=user_id,
            bot_name=bot.get("bot_name"),
            bot_desc=bot.get("bot_desc"),
            workspace_path=workspace_path,
            device_token=device_token,
            engine_type=engine_type or DEFAULT_ENGINE_TYPE,
        )

        # 桌面版永远走两段式：先返回 need_authorization，
        # 由前端引导授权后，通过 auth-status 轮询 ISSUED 状态再调用 create_after_authorization。
        return {
            "need_authorization": True,
            "bot_id": bot_id,
            "iframe_url": passport_result.get("iframe_url") if passport_result else None,
            "redirect_url": passport_result.get("redirect_url") if passport_result else None,
        }

    # ── _execute_creation ──────────────────────────────────────────────

    def _execute_creation(
        self,
        bot: dict[str, Any],
        user_id: str,
        machine_id: str,
        migration_path: str,
        mount_path: str | None,
        agent_code: str,
        engine_type: str | None = None,
    ) -> dict[str, Any]:
        """执行 BAAS 创建 Bot、审批、写数据库的完整流程。

        由 create_after_authorization 在授权完成后调用，不应直接被外部调用。

        Args:
            bot: Bot 信息字典，包含 bot_id, bot_name, entity_id, entity_type, bot_desc, active_engine, avatar_url
            user_id: 创建者用户 ID
            machine_id: 目标设备节点 ID
            migration_path: Bot 实例数据目录路径
            mount_path: 用户自定义本地磁盘挂载路径（可选）
            agent_code: Passport 返回的 agent_code，用于 BaaS 部署配置

        Returns:
            dict: bot_uuid, binding_id, bot_id, agent_code
        """
        bot_id = bot.get("bot_id", "")

        # Step 1: 生成唯一请求 ID，用于 BaaS 接口幂等
        request_id = _generate_request_id(
            bot_id=bot_id,
            entity_id=bot["entity_id"],
            entity_type=bot["entity_type"],
            env=get_current_env(),
            action="create",
        )

        # Step 2: 检查桌面 Bot 模板 UUID 是否已配置
        if not self._desktop_template_uuid:
            raise DesktopBotServiceError(
                "desktop_template_uuid is not configured; "
                "set baas.desktop_template_uuid in config"
            )

        # Step 3: 生成 VM 凭证标识（持久化到 ext，传给 BaaS deploy_config）
        client_id = f"staff_{user_id}_{bot_id}_{uuid.uuid4().hex}"
        callback_token = secrets.token_urlsafe(32)

        # Step 4: 构建 BaaS 创建 Bot 的请求体
        payload = self._build_desktop_bot_payload(
            bot=bot,
            owner_id=user_id,
            request_id=request_id,
            migration_path=migration_path,
            mount_path=mount_path,
            machine_id=machine_id,
            agent_code=agent_code,
            client_id=client_id,
            callback_token=callback_token,
            engine_type=engine_type,
        )

        # Step 5: 调用 BaaS API 创建 Bot
        try:
            baas_result = self._baas.post_bots_api(
                path="/api/v1/bots",
                payload=payload,
                action="desktop_create_bot",
            )
        except Exception as e:
            logger.error(
                "[DesktopBotService._execute_creation] BaaS create_bot failed: bot_id=%s error=%s",
                bot_id, e,
            )
            raise DesktopBotServiceError(
                f"BaaS create_bot failed: bot_id={bot_id} error={e}"
            )

        logger.info(
            "[DesktopBotService._execute_creation] BaaS create_bot response: bot_id=%s "
            "bot_uuid=%s publish_id=%s",
            bot_id, baas_result.get("bot_uuid"), baas_result.get("publish_id"),
        )

        bot_uuid = baas_result.get("bot_uuid", "")
        publish_id = baas_result.get("publish_id")

        # Step 5: 自动审批 publish（若有 publish_id）
        if publish_id:
            try:
                self._baas.approve_publish(
                    publish_id=publish_id,
                    operator=user_id,
                    request_id=request_id,
                    comment="自动审批",
                )
            except Exception as e:
                logger.error(
                    "[DesktopBotService._execute_creation] approve_publish failed: "
                    "publish_id=%s, error=%s", publish_id, e,
                )
                raise DesktopBotServiceError(
                    f"Desktop bot approve publish failed: publish_id={publish_id}, error={e}"
                )

        # Step 6: 写入本地数据库（binding + bot）
        try:
            env = get_current_env()
            binding_id = self._binding_repo.insert_binding(
                entity_id=user_id,
                entity_type="staff",
                device_id=bot_uuid,
                device_provider="baas",
                env=env,
                device_props={},
                status="PENDING",
                apply_reason=f"Create desktop bot: {bot.get('bot_name', '')}",
                applied_by=user_id,
            )

            ext: dict[str, Any] = {
                "passport": {"agent_code": agent_code, "status": "ISSUED"},
                "start_status": "PENDING",
                "start_message": "",
                "machine_id": machine_id,
                "mount_path": (mount_path or "").strip(),
                "migration_path": migration_path,
                "desktop_template_uuid": self._desktop_template_uuid or "",
                "workspace_path": f"~/.teamclaw/boxes/{bot_id}",
                "client_id": client_id,
                "callback_token": callback_token,
                # 进入 PENDING 的时刻,供周期扫描的过渡超时兜底使用(见 restart)。
                "pending_since": datetime.now().isoformat(),
            }
            avatar_url = bot.get("avatar_url")
            if avatar_url:
                ext["avatar_url"] = avatar_url

            self._bot_repo.insert({
                "bot_id": bot_id,
                "bot_name": bot.get("bot_name", ""),
                "bot_desc": bot.get("bot_desc"),
                "entity_id": user_id,
                "entity_type": "staff",
                "creator_id": user_id,
                "owner_id": user_id,
                "owner_name": bot.get("owner_name", user_id),
                "engine_types": list(SUPPORTED_ENGINE_TYPES),
                "active_engine": bot.get("active_engine", DEFAULT_ENGINE_TYPE),
                "status": "PENDING",
                "binding_id": binding_id,
                "device_id": bot_uuid,
                "modifier_id": user_id,
                "is_delete": 0,
                "bot_type": "desktop",
                "ext": ext,
            })

            logger.info(
                "[DesktopBotService._execute_creation] bot_id=%s binding_id=%s bot_uuid=%s",
                bot_id, binding_id, bot_uuid,
            )

            # Step 7: 启动后台轮询 publish 进度
            if publish_id:
                self._start_publish_polling(
                    publish_id=str(publish_id),
                    binding_id=str(binding_id),
                    bot_id=bot_id,
                    owner_id=user_id,
                    device_id=bot_uuid,
                    engine_type=engine_type or DEFAULT_ENGINE_TYPE,
                )

            return {
                "bot_uuid": bot_uuid,
                "binding_id": binding_id,
                "bot_id": bot_id,
                "agent_code": agent_code,
            }

        except Exception as e:
            logger.error(
                "[DesktopBotService._execute_creation] local DB write failed: %s", e,
            )
            raise DesktopBotServiceError(f"Desktop bot local write failed: {e}")

    # ── create_after_authorization ─────────────────────────────────────

    def create_after_authorization(
        self,
        bot: dict[str, Any],
        user_id: str,
        machine_id: str,
        migration_path: str | None = None,
        mount_path: str | None = None,
        engine_type: str | None = None,
    ) -> dict[str, Any]:
        """授权完成后继续创建桌面 Bot（两段式第二步）。

        查询 Passport 获取 agent_code，然后调用 _execute_creation 完成 BAAS 创建和本地入库。

        Args:
            bot: Bot 信息字典，必须包含 bot_id；可选 bot_name, bot_desc, avatar_url
            user_id: 创建者用户 ID
            machine_id: 目标设备节点 ID
            migration_path: Bot 迁移目录路径（可选）
            mount_path: 用户自定义本地磁盘挂载路径（可选）
            engine_type: 引擎类型（可选，默认 openclaw）

        Returns:
            dict: bot_uuid, binding_id, bot_id, agent_code

        Raises:
            DesktopBotServiceError: bot_id 缺失、Passport 不存在或创建流程失败
        """
        bot_id = bot.get("bot_id", "")
        if not bot_id:
            raise DesktopBotServiceError("bot_id is required for create_after_authorization")

        passport_info = self._passport.query_agent_passport(
            bot_id=bot_id,
            owner_workno=user_id,
        )
        if not passport_info:
            raise DesktopBotServiceError(
                f"Passport not found for bot {bot_id}"
            )

        agent_code = passport_info.get("agent_code", "")

        full_bot = {
            "entity_id": f"staff_{user_id}",
            "entity_type": "staff",
            "active_engine": engine_type or DEFAULT_ENGINE_TYPE,
            **bot,
            "bot_id": bot_id,
        }

        if not migration_path:
            migration_path = f"/home/admin/nfs/bot-data/desktop/{bot_id}"

        return self._execute_creation(
            bot=full_bot,
            user_id=user_id,
            machine_id=machine_id,
            migration_path=migration_path,
            mount_path=mount_path,
            agent_code=agent_code,
            engine_type=engine_type,
        )

    # ── restart ─────────────────────────────────────────────────────────

    def restart(
        self,
        bot_id: str,
        user_id: str,
    ) -> dict[str, Any]:
        """重启桌面 Bot。

        1. 查找本地记录
        2. 调用 BAAS restart_bot + approve_publish
        3. 更新本地状态为 PENDING

        Args:
            bot_id: Bot ID
            user_id: 操作者用户 ID
        """
        binding, bot, bot_id, device_id = self._lookup_local(bot_id, user_id)
        env = bot.get("env", get_current_env())

        logger.info(
            "[DesktopBotService.restart] bot_id=%s device_id=%s user_id=%s",
            bot_id, device_id, user_id,
        )

        request_id = _generate_request_id(
            bot_id=bot_id,
            entity_id=binding.entity_id,
            entity_type=binding.entity_type,
            env=env,
            action="restart",
        )

        # BaaS restart — 失败则直接抛出，本地状态不变
        baas_result = self._baas.restart_bot(
            bot_uuid=device_id,
            operator=user_id,
            request_id=request_id,
        )

        logger.info(
            "[DesktopBotService.restart] BaaS restart_bot response: device_id=%s "
            "publish_id=%s",
            device_id, baas_result.get("publish_id"),
        )

        publish_id = baas_result.get("publish_id")
        if publish_id:
            try:
                self._baas.approve_publish(
                    publish_id=publish_id,
                    operator=user_id,
                    request_id=request_id,
                    comment="自动审批重启",
                )
                logger.info(
                    "[DesktopBotService.restart] approved publish_id=%s", publish_id,
                )
            except Exception as e:
                logger.error(
                    "[DesktopBotService.restart] approve_publish failed: "
                    "publish_id=%s, error=%s", publish_id, e,
                )
                raise DesktopBotServiceError(
                    f"重启审批失败: publish_id={publish_id}, error={e}"
                )

        self._update_local_status(binding.id, bot_id, binding.entity_id, "PENDING")

        # 记录本次进入 PENDING 的时刻,供周期扫描的过渡超时兜底使用。
        # 必须用这个独立时间戳而非 gmt_create:老 bot 的 gmt_create 早超
        # 超时窗,会让老 bot 一重启就被误判 OFFLINE。
        self._merge_bot_ext(bot_id, binding.entity_id, {
            "pending_since": datetime.now().isoformat(),
        })

        # 启动后台轮询 publish 进度
        if publish_id:
            self._start_publish_polling(
                publish_id=str(publish_id),
                binding_id=str(binding.id),
                bot_id=bot_id,
                owner_id=binding.entity_id,
                device_id=device_id,
                engine_type=bot.get("active_engine", DEFAULT_ENGINE_TYPE),
            )

        return {
            "device_id": device_id,
            "bot_id": bot_id,
            "status": "PENDING",
        }

    # ── delete ──────────────────────────────────────────────────────────

    def delete(
        self,
        bot_id: str,
        user_id: str,
    ) -> dict[str, Any]:
        """删除桌面 Bot。

        1. 查找本地记录
        2. 调用 BAAS destroy_bot + approve_publish（失败不阻塞本地清理）
        3. 销毁 Passport（失败不阻塞本地清理）
        4. 释放 binding + 软删除 bot（始终执行）

        Args:
            bot_id: Bot ID
            user_id: 操作者用户 ID
        """
        binding, bot, bot_id, device_id = self._lookup_local(bot_id, user_id)
        env = bot.get("env", get_current_env())

        logger.info(
            "[DesktopBotService.delete] bot_id=%s device_id=%s user_id=%s",
            bot_id, device_id, user_id,
        )

        request_id = _generate_request_id(
            bot_id=bot_id,
            entity_id=binding.entity_id,
            entity_type=binding.entity_type,
            env=env,
            action="delete",
        )

        # BaaS destroy — 失败不阻塞，本地数据照删
        baas_destroy_ok = False
        try:
            baas_result = self._baas.destroy_bot(
                bot_uuid=device_id,
                operator=user_id,
                request_id=request_id,
            )
            baas_destroy_ok = True

            logger.info(
                "[DesktopBotService.delete] BaaS destroy_bot response: device_id=%s "
                "publish_id=%s",
                device_id, baas_result.get("publish_id"),
            )

            publish_id = baas_result.get("publish_id")
            if publish_id:
                try:
                    self._baas.approve_publish(
                        publish_id=publish_id,
                        operator=user_id,
                        request_id=request_id,
                        comment="自动审批销毁",
                    )
                    logger.info(
                        "[DesktopBotService.delete] approved publish_id=%s", publish_id,
                    )
                except Exception as e:
                    logger.warning(
                        "[DesktopBotService.delete] approve failed: %s", e,
                    )
        except Exception as e:
            logger.error(
                "[DesktopBotService.delete] BaaS destroy_bot failed, "
                "proceeding with local cleanup: bot_id=%s device_id=%s error=%s",
                bot_id, device_id, e,
            )

        # 销毁 Passport — 失败不阻塞
        try:
            self._passport.destroy_passport(bot_id, user_id)
            logger.info(
                "[DesktopBotService.delete] destroy_passport success: bot_id=%s user_id=%s",
                bot_id, user_id,
            )
        except Exception as e:
            logger.error(
                "[DesktopBotService.delete] destroy_passport failed, "
                "proceeding with local cleanup: bot_id=%s user_id=%s error=%s",
                bot_id, user_id, e,
            )

        # 本地数据清理 — 始终执行
        try:
            self._binding_repo.release_binding(
                binding_id=binding.id,
                release_reason="Desktop bot deleted",
                released_by=user_id,
            )
            self._bot_repo.soft_delete_by_owner(
                bot_id=bot_id,
                owner_id=binding.entity_id,
            )
            logger.info(
                "[DesktopBotService.delete] bot_id=%s binding_id=%s",
                bot_id, binding.id,
            )
        except Exception as e:
            logger.warning(
                "[DesktopBotService.delete] local update failed: %s", e,
            )

        return {
            "device_id": device_id,
            "bot_id": bot_id,
            "status": "DELETED",
            "baas_destroy_ok": baas_destroy_ok,
        }

    # ── helpers ─────────────────────────────────────────────────────────

    # mount_path 校验：与 BaaS local_paas_service._validate_mount_path 保持一致
    _MOUNT_PATH_BLOCKED_PREFIXES = (
        "/etc", "/bin", "/sbin", "/boot", "/dev", "/proc", "/sys", "/root",
    )

    @staticmethod
    def _validate_mount_path(path: str | None) -> None:
        """校验 mount_path 安全性，与 BaaS 侧规则对齐。

        - None 或空字符串：合法（不挂载）
        - 必须是绝对路径（以 / 开头）
        - 原始路径不能包含 ..
        - 规范化后不能是系统目录或其子目录

        Raises:
            DesktopBotServiceError: 路径不合法
        """
        if path is None:
            return
        if not path.strip():
            return
        if not path.startswith("/"):
            logger.warning(
                "[DesktopBotService._validate_mount_path] rejected: not absolute: %s", path,
            )
            raise DesktopBotServiceError(
                f"mount_path must be absolute path (must start with /): {path}"
            )
        # 先检查原始路径中的 .. （normpath 会消除它们，但语义上仍然危险）
        if ".." in path.split("/"):
            logger.warning(
                "[DesktopBotService._validate_mount_path] rejected: traversal: %s", path,
            )
            raise DesktopBotServiceError(
                f"mount_path cannot contain directory traversal sequences: {path}"
            )
        normalized = os.path.normpath(path)
        for prefix in DesktopBotService._MOUNT_PATH_BLOCKED_PREFIXES:
            # 精确匹配：/etc 或 /etc/xxx，但 /etc2 不拦
            if normalized == prefix or normalized.startswith(prefix + "/"):
                logger.warning(
                    "[DesktopBotService._validate_mount_path] rejected: system dir: %s", path,
                )
                raise DesktopBotServiceError(
                    f"mount_path cannot mount system directory: {path}"
                )

    def _build_desktop_bot_payload(
        self,
        bot: dict[str, Any],
        owner_id: str,
        request_id: str,
        migration_path: str,
        mount_path: str | None = None,
        machine_id: str | None = None,
        agent_code: str = "",
        client_id: str = "",
        callback_token: str = "",
        engine_type: str | None = None,
    ) -> dict[str, Any]:
        """构建桌面 Bot 专属的 BaaS 创建请求体。

        与服务 Bot 不同，桌面 Bot 的 payload 由本方法独立构建，
        不依赖 BaasService._build_create_bot_payload，方便按桌面场景定制参数。

        machine_id、agent_code、mount_path 放入 config.deploy_config 下，
        由 BaaS 侧按需读取。

        Args:
            bot: Bot 信息字典，包含 bot_id, bot_name, entity_id, entity_type, bot_desc, active_engine
            owner_id: 创建者 ID
            request_id: 请求 ID
            migration_path: Bot 实例数据目录路径
            mount_path: 用户自定义挂载路径（可选）
            machine_id: 目标设备节点 ID（可选）
            agent_code: Passport 返回的 agent_code
            client_id: VM 凭证 CLIENT_ID（格式 staff_{owner}_{bot}_{hex}）
            callback_token: VM 凭证 TOKEN（engine HTTP 回调凭证）

        Returns:
            完整的 BaaS 创建 Bot 请求体
        """
        bot_id = bot.get("bot_id", "")
        name = bot.get("bot_name", bot_id)
        entity_id = bot.get("entity_id", "")
        entity_type = bot.get("entity_type", "staff")
        description = bot.get("bot_desc")
        engine = bot.get("active_engine", DEFAULT_ENGINE_TYPE)

        # ── 部署配置 ──
        # 桌面 bot 不需要 mount_points（运行在用户本地机器，非 NAS 挂载）
        # ttl_in_minutes: None 表示不限制存活时间（桌面 bot 长驻运行）
        deploy_config = BotDeployConfig(
            after_create_cmd_hook=self._baas._get_start_cmd(
                bot_id=bot_id,
                owner_id=owner_id,
                entity_id=entity_id,
                entity_type=entity_type,
                migration_pat=migration_path,
                bot_type="desktop",
                engine=engine,
            ),
            after_create_hook_wait_seconds=10,
            before_destroy_cmd_hook=self._baas._get_destroy_cmd(),
            before_destroy_hook_wait_seconds=10,
            ttl_in_minutes=self._desktop_ttl_minutes if self._desktop_ttl_minutes > 0 else None,
            user_id=owner_id or None,
            tc_bot_id=bot_id or None,
            engine_type=engine_type or engine or None,
        )

        # ── Bot 配置 ──
        config = BotConfig(
            entity_id=entity_id,
            entity_type=entity_type,
            deploy_config=deploy_config,
        )

        # ── 顶层请求体 ──
        payload: dict[str, Any] = {
            "name": name,
            "template_uuid": self._desktop_template_uuid,
            "device_count": 1,
            "operator": owner_id,
            "request_id": request_id,
            "config": config.to_dict(),
        }

        # 桌面 bot 专属字段：放入 deploy_config
        # 空字符串 mount_path 视为 None，不传给 BaaS（WR-01 对齐）
        effective_mount_path = mount_path.strip() if mount_path else None
        if machine_id:
            payload["config"]["deploy_config"]["machine_id"] = machine_id
        if agent_code:
            payload["config"]["deploy_config"]["agent_code"] = agent_code
        if effective_mount_path:
            payload["config"]["deploy_config"]["mount_path"] = effective_mount_path
        if description:
            payload["description"] = description

        # ── VM 凭证文件 ──
        # mng daemon (AgentBoxManager) 在 W1 窗口将此字段写为 box_dir/.credentials，
        # virtiofs 挂载后 VM 内即为 ~/.credentials。
        # 消费方：
        #   - BCN plugin _loadCredentials(): BOT_ID + OWNER_ID → bot.connect 身份
        #   - BCN plugin hitl.ts: BOT_ID + ENTITY_ID → HITL 卡片
        #   - Engine CredentialsService: TOKEN/CLIENT_ID/OWNER_ID/BOT_ID/AGENT_CODE
        if bot_id and owner_id:
            credentials = {
                "token": callback_token,
                "client_id": client_id,
                "owner_id": owner_id,
                "bot_id": bot_id,
                "entity_id": entity_id,
                "entity_type": entity_type,
                "bot_type": "desktop",
                "agent_code": agent_code or "",
                "stage": "online",
            }
            payload["config"]["deploy_config"]["credentials"] = credentials
            logger.info(
                "[DesktopBotService._build_desktop_bot_payload] credentials: "
                "bot_id=%s owner_id=%s entity_id=%s entity_type=%s "
                "client_id=%s agent_code=%s stage=%s token=%s",
                bot_id, owner_id, entity_id, entity_type,
                client_id, agent_code or "", "online",
                callback_token[:8] + "..." if callback_token else "",
            )

        ttl = self._desktop_ttl_minutes if self._desktop_ttl_minutes > 0 else None
        logger.info(
            "[DesktopBotService._build_desktop_bot_payload] "
            "bot_id=%s machine_id=%s agent_code=%s mount_path=%s user_id=%s ttl=%s",
            bot_id, machine_id, agent_code, mount_path, owner_id, ttl,
        )

        return payload

    def _apply_passport(
        self,
        bot_id: str,
        user_id: str,
        bot_name: str | None,
        bot_desc: str | None,
        workspace_path: str,
        device_token: str = "",
        engine_type: str = DEFAULT_ENGINE_TYPE,
    ) -> dict[str, Any]:
        """申请 Passport，返回完整结果字典（含 redirect_url/iframe_url 等）。

        桌面版没有"首次默认给过"的概念，永远只走 ``apply_agent_passport``
        （非首次申请）。该接口通常不返回 token，而是返回 iframe_url /
        redirect_url 供前端引导用户完成授权；授权完成后在
        ``create_after_authorization`` 中通过 ``query_agent_passport``
        获取正式的 agent_code。

        Args:
            bot_id: Bot ID。
            user_id: 创建者用户 ID。
            bot_name: Bot 名称（可选）。
            bot_desc: Bot 描述（可选）。
            workspace_path: 工作空间路径，用于许可证绑定。
            device_token: 从 BAAS 机器信息接口获取的设备令牌，用于许可证绑定机器。
            engine_type: 引擎类型，用于 MCP 作用域隔离和 Passport 申请。

        Returns:
            passport_result 字典（token 通常为空，表示需要前端授权）。
        """
        logger.info(
            "[DesktopBotService._apply_passport] bot_id=%s user_id=%s device_token=%s",
            bot_id, user_id, "set" if device_token else "empty",
        )

        # Step 1: 获取该 Bot 的 MCP 列表；提交 Passport 服务前会过滤 LOCAL/stdio MCP。
        skill_set_service = self._skill_set_factory.create(
            user_id=user_id,
            entity_id=user_id,
            bot_id=bot_id,
            entity_type="staff",
            engine_type=engine_type,
        )
        mcp_codes = skill_set_service.get_bot_mcp_codes(
            entity_id=user_id,
            bot_id=bot_id,
            user_id=user_id,
            entity_type="staff",
            engine_type=engine_type,
        )
        logger.info(
            "[DesktopBotService._apply_passport] bot_id=%s mcp_codes=%s",
            bot_id, mcp_codes,
        )
        passport_mcp_codes = filter_passport_mcp_codes(mcp_codes)
        if passport_mcp_codes != mcp_codes:
            logger.info(
                "[DesktopBotService._apply_passport] filtered passport mcp_codes=%s",
                passport_mcp_codes,
            )

        # Step 2: 调用 Passport 非首次申请接口
        # 桌面 Bot 没有"首次默认授权"概念，直接走 apply_agent_passport。
        # 该接口通常不返回 token，而是返回 iframe_url/redirect_url 供前端引导用户授权。
        logger.info(
            "[DesktopBotService._apply_passport] calling apply_agent_passport: "
            "bot_id=%s user_id=%s bot_name=%s access_mode=%s device_token_present=%s "
            "workspace_path=%s mcp_count=%d",
            bot_id, user_id, bot_name, "RESTRICTED", bool(device_token),
            workspace_path, len(passport_mcp_codes),
        )

        try:
            passport_result = self._passport.apply_agent_passport(
                bot_id=bot_id,
                owner_workno=user_id,
                mcp_codes=passport_mcp_codes,
                bot_name=bot_name,
                bot_desc=bot_desc,
                engine_type=engine_type,
                access_mode="RESTRICTED",
                device_token=device_token,
                workspace_path=workspace_path,
            )
        except PassportError as e:
            # Passport 服务返回明确错误，直接上抛为业务异常，阻断后续创建流程。
            logger.error(
                "[DesktopBotService._apply_passport] apply_agent_passport "
                "failed: bot_id=%s user_id=%s error=%s",
                bot_id, user_id, e,
            )
            raise DesktopBotServiceError(
                f"Desktop bot passport apply failed: bot_id={bot_id} "
                f"user_id={user_id} error={e}"
            )

        # Step 3: 校验返回结果
        # 空结果属于异常情况（服务端未按约定返回），必须阻断创建。
        if not passport_result:
            raise DesktopBotServiceError(
                f"Passport apply returned empty result: bot_id={bot_id} user_id={user_id}"
            )

        logger.info(
            "[DesktopBotService._apply_passport] result: bot_id=%s "
            "token=%s agent_code=%s iframe_url=%s redirect_url=%s",
            bot_id,
            "present" if passport_result.get("token") else "empty",
            passport_result.get("agent_code"),
            "present" if passport_result.get("iframe_url") else "empty",
            "present" if passport_result.get("redirect_url") else "empty",
        )
        return passport_result

    def _fetch_machine_info(self, machine_id: str) -> dict[str, Any]:
        """通过 BaaS API 查询机器信息，获取 device_token 等字段。

        调用链路：
        1. 向 BaaS /api/v1/local/machines/{machine_id}/info 发 GET 请求。
        2. 先校验 HTTP 状态码，再校验业务响应体中的 code 字段。
        3. 返回 data 里的机器详情字典（含 device_token）。

        Args:
            machine_id: 目标机器 ID

        Returns:
            机器信息字典，包含 device_token, machine_name 等

        Raises:
            DesktopBotServiceError: 任何环节出错均转为业务异常，阻断 bot 创建。
        """
        logger.info(
            "[DesktopBotService._fetch_machine_info] machine_id=%s", machine_id
        )

        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.get(
                    f"{self._baas_api_base}/api/v1/local/machines/{machine_id}/info",
                    params={"tenant": self._tenant},
                )
                # 校验 HTTP 层状态码（4xx/5xx 会抛 HTTPStatusError）
                response.raise_for_status()

                response_data = response.json()
                # BaaS 接口采用标准包裹格式：{code, message, data}
                # code != 0 表示业务失败
                logger.info(
                    "[DesktopBotService._fetch_machine_info] BaaS raw response: %s",
                    response_data,
                )
                if response_data.get("code") != 0:
                    raise DesktopBotServiceError(
                        f"BaaS API error: machine_id={machine_id} "
                        f"message={response_data.get('message', 'Unknown error')}"
                    )

                data = response_data.get("data") or {}
                logger.info(
                    "[DesktopBotService._fetch_machine_info] machine_id=%s "
                    "machine_name=%s",
                    machine_id,
                    data.get("machine_name"),
                )
                return data

        # HTTP 层异常（网络超时、404、500 等）
        except httpx.HTTPStatusError as e:
            logger.error(
                "[DesktopBotService._fetch_machine_info] HTTP error: "
                "machine_id=%s status=%s",
                machine_id,
                e.response.status_code,
            )
            raise DesktopBotServiceError(
                f"BaaS API error: machine_id={machine_id} "
                f"status={e.response.status_code}"
            )
        # 已转换的业务异常直接上抛，避免被下面的通用 Exception 吞掉
        except DesktopBotServiceError:
            raise
        # 兜底：JSON 解析失败、网络不可达等未知异常
        except Exception as e:
            logger.error(
                "[DesktopBotService._fetch_machine_info] Failed: machine_id=%s error=%s",
                machine_id, e,
            )
            raise DesktopBotServiceError(
                f"Failed to fetch machine info: machine_id={machine_id} error={e}"
            )

    def _lookup_local(self, bot_id: str, user_id: str):
        """查找本地 bot 和 binding 记录。

        通过 bot_id + owner_id 查找 bot 记录，再通过 binding_id 查找 binding 记录。
        同时返回 device_id（即 BaaS bot_uuid），供 BaaS API 调用使用。
        """
        logger.info(
            "[DesktopBotService._lookup_local] bot_id=%s user_id=%s", bot_id, user_id,
        )
        bot = self._bot_repo.get_by_id_and_owner(bot_id=bot_id, owner_id=user_id)
        if not bot:
            raise DesktopBotServiceError(
                f"Desktop bot not found: bot_id={bot_id}"
            )

        binding_id = bot.get("binding_id")
        if not binding_id:
            raise DesktopBotServiceError(
                f"Desktop bot has no device binding: bot_id={bot_id}"
            )

        binding = self._binding_repo.get_by_id(binding_id=int(binding_id))
        if not binding:
            raise DesktopBotServiceError(
                f"Desktop device binding not found: binding_id={binding_id}"
            )

        device_id = bot.get("device_id", "")
        return binding, bot, bot_id, device_id

    def _update_local_status(
        self,
        binding_id: str,
        bot_id: str,
        owner_id: str,
        status: str,
    ) -> None:
        """更新本地 bot 和 binding 状态（各自容错）。"""
        try:
            self._bot_repo.update_by_owner(
                bot_id=bot_id,
                owner_id=owner_id,
                update_data={"status": status},
            )
        except Exception as e:
            logger.warning(
                "[DesktopBotService._update_local_status] bot update failed: %s", e,
            )
        if binding_id:
            try:
                self._binding_repo.update_status(
                    binding_id=binding_id,
                    status=status,
                )
            except Exception as e:
                logger.warning(
                    "[DesktopBotService._update_local_status] binding update failed: %s", e,
                )
        logger.info(
            "[DesktopBotService._update_local_status] "
            "bot_id=%s binding_id=%s status=%s",
            bot_id, binding_id, status,
        )

    def _trigger_device_alive(self, device_id: str) -> bool:
        """进程内触发 DeviceActivatedEvent（agentbox 无 callback_token，跳过鉴权）。

        调用 DeviceService.report_device_alive(skip_token_check=True)，完成：
        1. PENDING → ACTIVE 状态变更
        2. 发布 DeviceActivatedEvent → SkillSymlinkListener → 软链同步

        Returns:
            True if report_device_alive succeeded, False otherwise.
        """
        try:
            self._device_service.report_device_alive(
                device_id=device_id,
                token="",
                skip_token_check=True,
            )
            logger.info(
                "[DesktopBotService._trigger_device_alive] "
                "DeviceActivatedEvent triggered for device_id=%s", device_id,
            )
            return True
        except Exception as e:
            logger.warning(
                "[DesktopBotService._trigger_device_alive] failed for device_id=%s: %s",
                device_id, e,
            )
            return False

    # ── publish progress polling ─────────────────────────────────────────

    _POLL_INTERVAL_SECONDS = 5
    _DEFAULT_POLL_TIMEOUT_SECONDS = 180  # 3 minutes (openclaw 等默认引擎)
    _EXTENDED_POLL_TIMEOUT_SECONDS = 600  # 10 minutes (claude_code 等非默认引擎，需下载镜像)

    def _poll_publish_progress(
        self,
        publish_id: str,
        binding_id: str,
        bot_id: str,
        owner_id: str,
        device_id: str,
        engine_type: str = "",
    ) -> None:
        """轮询 publish 进度，根据结果更新本地 bot 状态。

        在后台线程中运行。SUCCESS → _trigger_device_alive（含 ACTIVE 状态变更 + DeviceActivatedEvent）。
        BaaS 明确 FAILED → _update_local_status(FAILED)。
        轮询超时 → 保持 PENDING + ext.start_status=DOWNLOADING，委托周期扫描裁决。

        Args:
            engine_type: 引擎类型，用于选择轮询超时时间。
                非默认引擎（如 claude_code）需要下载镜像，给更长的超时。
        """
        poll_timeout = (
            self._EXTENDED_POLL_TIMEOUT_SECONDS
            if engine_type and engine_type != DEFAULT_ENGINE_TYPE
            else self._DEFAULT_POLL_TIMEOUT_SECONDS
        )
        start_time = time.monotonic()
        final_status = "FAILED"

        logger.info(
            "[DesktopBotService._poll_publish_progress] start polling "
            "publish_id=%s bot_id=%s device_id=%s engine_type=%s timeout=%ds",
            publish_id, bot_id, device_id, engine_type, poll_timeout,
        )

        try:
            while (time.monotonic() - start_time) < poll_timeout:
                time.sleep(self._POLL_INTERVAL_SECONDS)

                try:
                    status = self._query_publish_status(publish_id)
                except Exception as e:
                    logger.warning(
                        "[DesktopBotService._poll_publish_progress] "
                        "query failed (will retry): %s", e,
                    )
                    continue

                if status == "SUCCESS":
                    final_status = "ACTIVE"
                    if not self._trigger_device_alive(device_id):
                        logger.warning(
                            "[DesktopBotService._poll_publish_progress] "
                            "_trigger_device_alive failed, falling back to _update_local_status: "
                            "bot_id=%s device_id=%s", bot_id, device_id,
                        )
                        final_status = "ACTIVE_FALLBACK"
                    break

                if status == "FAILED":
                    final_status = "FAILED"
                    break

                # 其他状态（PENDING/RUNNING 等）继续轮询

            logger.info(
                "[DesktopBotService._poll_publish_progress] done "
                "publish_id=%s final_status=%s elapsed=%.1fs",
                publish_id, final_status, time.monotonic() - start_time,
            )

        except Exception as e:
            logger.error(
                "[DesktopBotService._poll_publish_progress] unexpected error: %s", e,
            )
            final_status = "FAILED"

        # while 循环结束后，判断是轮询超时还是明确的 BaaS FAILED
        elapsed = time.monotonic() - start_time
        timed_out = final_status == "FAILED" and elapsed >= poll_timeout

        if timed_out:
            logger.info(
                "[DesktopBotService._poll_publish_progress] poll timed out — "
                "delegating to periodic scan, publish_id=%s bot_id=%s",
                publish_id, bot_id,
            )
            final_status = "PENDING_DOWNLOADING"

        # 状态更新：PENDING_DOWNLOADING 保持 PENDING，不调 _update_local_status
        if final_status == "PENDING_DOWNLOADING":
            pass  # 保持 PENDING，委托周期扫描裁决
        elif final_status != "ACTIVE":
            effective_status = "ACTIVE" if final_status == "ACTIVE_FALLBACK" else final_status
            self._update_local_status(binding_id, bot_id, owner_id, effective_status)

        # 同步更新 ext 中的 start_status
        if final_status in ("ACTIVE", "ACTIVE_FALLBACK"):
            start_status = "SUCCEEDED"
        elif final_status == "PENDING_DOWNLOADING":
            start_status = "DOWNLOADING"
        else:
            start_status = "FAILED"

        try:
            bot = self._bot_repo.get_by_id_and_owner(bot_id=bot_id, owner_id=owner_id)
            if bot:
                current_ext = bot.get("ext") or {}
                current_ext["start_status"] = start_status
                if start_status == "DOWNLOADING":
                    current_ext["start_message"] = "镜像下载中，请耐心等待..."
                self._bot_repo.update_by_owner(
                    bot_id=bot_id,
                    owner_id=owner_id,
                    update_data={"ext": current_ext},
                )
        except Exception as e:
            logger.warning(
                "[DesktopBotService._poll_publish_progress] "
                "ext update failed: bot_id=%s error=%s", bot_id, e,
            )

    def _query_publish_status(self, publish_id: str) -> str:
        """查询单次 publish 进度，返回 status 字符串。"""
        with httpx.Client(timeout=10.0) as client:
            response = client.get(
                f"{self._baas_api_base}/api/v1/publishes/{publish_id}/progress",
                params={
                    "tenant": self._tenant,
                    "include_devices": "false",
                },
            )
            response.raise_for_status()

            data = response.json()
            logger.info(
                "[DesktopBotService._query_publish_status] BaaS raw response: %s",
                data,
            )
            if data.get("code") != 0:
                raise DesktopBotServiceError(
                    f"Publish progress API error: {data.get('message', 'Unknown')}"
                )

            return data.get("data", {}).get("status", "")

    def _start_publish_polling(
        self,
        publish_id: str,
        binding_id: str,
        bot_id: str,
        owner_id: str,
        device_id: str,
        engine_type: str = "",
    ) -> None:
        """启动后台线程轮询 publish 进度。"""
        thread = threading.Thread(
            target=bind_current_avernet_tenant(self._poll_publish_progress),
            args=(publish_id, binding_id, bot_id, owner_id, device_id, engine_type),
            daemon=True,
            name=f"poll-publish-{publish_id}",
        )
        thread.start()

    # ── device status query ──────────────────────────────────────────────

    def query_device_status(self, bot_uuid: str) -> dict[str, Any]:
        """查询 BaaS 设备状态。

        调用 BaaS API: GET /api/v1/bots/{bot_uuid}/device-status?tenant={tenant}

        Returns:
            BaaS 返回的 data 字典，通常包含 status、device_id 等信息。
            若请求失败则抛出 DesktopBotServiceError。
        """
        url = f"{self._baas_api_base}/api/v1/bots/{bot_uuid}/device-status"
        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.get(url, params={"tenant": self._tenant})
                response.raise_for_status()

                data = response.json()
                logger.info(
                    "[DesktopBotService.query_device_status] "
                    "bot_uuid=%s url=%s status_code=%s response=%s",
                    bot_uuid, url, response.status_code, data,
                )
                if data.get("code") != 0:
                    raise DesktopBotServiceError(
                        f"device-status API error: {data.get('message', 'Unknown')}"
                    )
                return data.get("data", {})

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                try:
                    body = e.response.json()
                    detail = body.get("detail") or {}
                    if isinstance(detail, dict) and detail.get("error_code") == "BOT_NOT_FOUND":
                        raise DesktopBotOrphanError(
                            f"BaaS confirmed bot not found: {bot_uuid}"
                        ) from e
                except (ValueError, AttributeError):
                    pass
            raise DesktopBotServiceError(
                f"device-status HTTP error: {e.response.status_code}"
            ) from e
        except (DesktopBotServiceError, DesktopBotOrphanError):
            raise
        except Exception as e:
            raise DesktopBotServiceError(f"device-status query failed: {e}") from e
