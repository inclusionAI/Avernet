"""多实例：实例列表 + 容器信息 + 健康四态（frontend-api-contract §1）。

从 ``device_service_router`` 抽出的一个内聚关注点：bot_id/binding_id 双入口
解析到同一 ``bot_uuid``，调 BaaS 查设备列表，逐条合成健康四态。

- 健康四态由 BaaS ``status`` + ``health`` 合成（§0.5），不落 BaaS 侧。
- bot_id 入口经 ``ext.binding.online`` 解析运行态 binding_id。
- engine_type 走运行态 binding 的 ``device_props.bolt_id`` → ``active_engine``。
"""
from __future__ import annotations

import json
from typing import Any

from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.devices.errors import (
    DeviceServiceError,
    InvalidDeviceStatusError,
)
from agentclaw.community.core.devices.models import DeviceBindingStatus, OperatorContext
from agentclaw.community.core.repository.protocols.devices import DeviceBindingRepository
from agentclaw.community.core.devices.repository.record import DeviceBindingRecord
from agentclaw.community.core.devices.services.device_service import (
    BAAS_DEVICE_PROVIDER,
    DeviceService,
)
from agentclaw.community.core.service_bot.services.deploy.provider_resolver import (
    TECLAW_DEVICE_PROVIDER,
)
from agentclaw.community.core.repository.protocols.publishing import BotPublishRepositoryProtocol
from agentclaw.community.log import get_logger
from agentclaw.community.utils import env_utils


logger = get_logger()

_DEFAULT_ENGINE_TYPE = "openclaw"
_RUNTIME_DEVICE_PROVIDERS = {BAAS_DEVICE_PROVIDER, TECLAW_DEVICE_PROVIDER}


class BotPublishNotFoundError(RuntimeError):
    """bot_id 无 success 发布单或 ext.binding.online 缺失。"""


class BindingNotFoundError(RuntimeError):
    """binding_id 无效或不支持实例查询。"""


class InstanceHealthStatus:
    """健康四态，映射 BaaS status + health → 前端展示用枚举串（英文，前端做文案映射）。"""

    RESTARTING = "RESTARTING"
    ACTIVE = "ACTIVE"
    ABNORMAL = "ABNORMAL"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def from_baas_fields(cls, status: str | None, health: str | None) -> str:
        """合成健康四态（frontend-api-contract §0.5）。

        status ∈ {PENDING,UPDATING} → RESTARTING
        否则 health=="true"        → ACTIVE
        否则 health=="false"       → ABNORMAL
        否则(health 缺失/降级)      → UNKNOWN
        """
        if status and status.upper() in ("PENDING", "UPDATING"):
            return cls.RESTARTING
        if health == "true":
            return cls.ACTIVE
        if health == "false":
            return cls.ABNORMAL
        return cls.UNKNOWN


class DeviceInstanceService:
    """双入口实例列表逻辑（无路由/分发，纯多实例读取）。

    由 ``DeviceServiceRouter`` 持有并委托。依赖同一份 binding 仓库与
    provider 字典，另加 publish/bot 仓库用于入口解析与 engine_type。
    """

    def __init__(
        self,
        *,
        repository: DeviceBindingRepository,
        providers: dict[str, DeviceService],
        publish_repo: BotPublishRepositoryProtocol | None,
        bot_repo: BotRepository | None,
    ) -> None:
        self._repo = repository
        self._providers = providers
        self._publish_repo = publish_repo
        self._bot_repo = bot_repo

    # ── 入口解析 ────────────────────────────────────────────────

    def _resolve_binding_id_by_bot_id(self, bot_id: str) -> int:
        """从 bot_id 解析运行态 binding_id（``ext.binding.online``）。

        查该 bot 最新一条 status=success 的发布单，取
        ``json.loads(ext)["binding"]["online"]``（int）= 运行态 binding_id。

        Raises:
            BotPublishNotFoundError: 无 success 发布单 / ext 缺失 / binding.online 缺失
        """
        if self._publish_repo is None:
            raise BotPublishNotFoundError(
                f"BotPublishRepository not available; cannot resolve bot_id={bot_id}"
            )

        env = env_utils.get_current_env()
        record = self._publish_repo.get_latest_success_by_source_bot_id(bot_id, env)
        if record is None:
            raise BotPublishNotFoundError(
                f"No success publish record found for bot_id={bot_id}, env={env}"
            )

        # BotPublishRecord.ext 已由 to_record() 解析为 dict；防御性兼容 str。
        ext = record.ext or {}
        if isinstance(ext, str):
            try:
                ext = json.loads(ext)
            except (json.JSONDecodeError, TypeError):
                raise BotPublishNotFoundError(
                    f"Failed to parse ext for publish_id={record.id}, bot_id={bot_id}"
                ) from None

        online_binding_id = (ext.get("binding") or {}).get("online")
        if not online_binding_id:
            raise BotPublishNotFoundError(
                f"ext.binding.online not found for publish_id={record.id}, bot_id={bot_id}"
            )

        logger.info(
            f"[_resolve_binding_id_by_bot_id] bot_id={bot_id} -> "
            f"binding_id={online_binding_id} (publish_id={record.id})"
        )
        return int(online_binding_id)

    def _validate_binding_for_instances(
        self, binding_id: int
    ) -> tuple[DeviceBindingRecord, str]:
        """校验 binding 有效性并返回 ``(record, bot_uuid)``。

        校验：存在 / device_provider=baas / status=ACTIVE / 同环境。

        Raises:
            BindingNotFoundError: 校验不通过
        """
        record = self._repo.get_by_id(binding_id)
        if record is None:
            raise BindingNotFoundError(f"Binding not found: binding_id={binding_id}")

        if record.device_provider != BAAS_DEVICE_PROVIDER:
            raise BindingNotFoundError(
                f"Binding {binding_id} is not baas provider: "
                f"provider={record.device_provider}"
            )

        env = env_utils.get_current_env()
        if record.env != env:
            raise BindingNotFoundError(
                f"Binding {binding_id} env mismatch: "
                f"binding.env={record.env}, current_env={env}"
            )

        # 运行态服务 BOT 的 binding 由发布成功置为 ACTIVE。实例的
        # PENDING/UPDATING 是 BaaS 实例级状态，不改 binding 记录状态。
        if record.status != DeviceBindingStatus.ACTIVE.value:
            raise BindingNotFoundError(
                f"Binding {binding_id} is not active: status={record.status}"
            )

        bot_uuid = record.device_id
        if not bot_uuid:
            raise BindingNotFoundError(
                f"Binding {binding_id} has no device_id (bot_uuid)"
            )

        return record, bot_uuid

    def _get_baas_service(self):
        """从 baas provider 拿到底层 BaasService 实例（无则 None）。"""
        baas_provider = self._providers.get(BAAS_DEVICE_PROVIDER)
        if baas_provider is None:
            return None
        # BaasDeviceService 持有 _baas_service。
        return getattr(baas_provider, "_baas_service", None)

    def _resolve_engine_type(self, record: DeviceBindingRecord) -> str:
        """由运行态 binding 的 ``device_props.bolt_id`` 解析 ``active_engine``。

        bolt_id(= ac_bots.bot_id) 由发布成功回写 binding.device_props。
        **不用** ``get_by_binding_id``：ac_bots.binding_id 存的是草稿 binding，
        运行态查不到。解析失败/缺失一律兜底 ``openclaw``。
        """
        engine_type = _DEFAULT_ENGINE_TYPE
        if self._bot_repo is None:
            return engine_type
        try:
            bolt_id = (record.device_props or {}).get("bolt_id")
            if bolt_id:
                bot = self._bot_repo.get_by_id(bolt_id)
                if bot and bot.get("active_engine"):
                    engine_type = bot["active_engine"]
        except Exception:
            logger.warning(
                "[_resolve_engine_type] Failed to resolve active_engine for "
                "binding device_props=%s, falling back to openclaw",
                getattr(record, "device_props", None),
            )
        return engine_type

    # ── 公开入口 ────────────────────────────────────────────────

    def get_instances(
        self,
        *,
        binding_id: int,
        health_check: bool = False,
    ) -> dict[str, Any]:
        """实例列表 + 容器信息 + 健康四态（binding_id 入口）。

        校验 binding → bot_uuid + engine_type → 调 BaaS 查设备列表 →
        逐条合成 health_status，透传 BaaS 原始 6 字段。

        Raises:
            BindingNotFoundError: binding 校验不通过
            DeviceServiceError: BaasService 不可用
        """
        record, bot_uuid = self._validate_binding_for_instances(binding_id)
        engine_type = self._resolve_engine_type(record)

        baas_service = self._get_baas_service()
        if baas_service is None:
            raise DeviceServiceError("BaasService not available for instances query")

        detail = baas_service.get_bot(
            bot_uuid, health_check=health_check, engine_type=engine_type
        )
        devices_raw = detail.get("devices", []) or []

        devices: list[dict[str, Any]] = []
        for d in devices_raw:
            health_status = InstanceHealthStatus.from_baas_fields(
                d.get("status"), d.get("health")
            )
            devices.append(
                {
                    # BaaS devices[] 原始 6 字段全量透传
                    "device_uuid": d.get("device_uuid", ""),
                    "status": d.get("status"),
                    "health": d.get("health"),
                    "provider_type": d.get("provider_type"),
                    "provider_device_id": d.get("provider_device_id"),
                    "gmt_create": d.get("gmt_create"),
                    # backend 合成/补充字段
                    "health_status": health_status,
                    "engine_type": engine_type,
                    "bot_uuid": bot_uuid,
                }
            )

        return {"bot_uuid": bot_uuid, "devices": devices}

    def list_devices_by_runtime_binding(
        self,
        *,
        binding_id: int,
        timeout: float | None = None,
    ) -> list[str]:
        """返回 BaaS 或 Teclaw 运行态 binding 下的 device_uuid 列表。

        校验 binding 与运行环境后取得 bot_uuid，再查询该 Bot 的运行设备。

        Raises:
            BindingNotFoundError: binding 不存在、状态不可用或 provider 不支持
            DeviceServiceError: BaasService 不可用
        """
        record = self._repo.get_by_id(binding_id)
        if record is None:
            raise BindingNotFoundError(f"Binding not found: binding_id={binding_id}")
        if record.device_provider not in _RUNTIME_DEVICE_PROVIDERS:
            raise BindingNotFoundError(
                f"Binding {binding_id} does not support runtime device query: "
                f"provider={record.device_provider}"
            )

        env = env_utils.get_current_env()
        if record.env != env:
            raise BindingNotFoundError(
                f"Binding {binding_id} env mismatch: "
                f"binding.env={record.env}, current_env={env}"
            )
        if record.status != DeviceBindingStatus.ACTIVE.value:
            raise BindingNotFoundError(
                f"Binding {binding_id} is not active: status={record.status}"
            )

        bot_uuid = record.device_id
        if not bot_uuid:
            raise BindingNotFoundError(
                f"Binding {binding_id} has no device_id (bot_uuid)"
            )

        baas_service = self._get_baas_service()
        if baas_service is None:
            raise DeviceServiceError(
                "BaasService not available for runtime device query"
            )

        if timeout is None:
            devices_raw = baas_service.list_devices_by_bot_uuid(bot_uuid)
        else:
            devices_raw = baas_service.list_devices_by_bot_uuid(
                bot_uuid,
                timeout=timeout,
            )

        devices: list[str] = []
        for d in devices_raw or []:
            device_uuid = d.get("device_uuid")
            if device_uuid:
                devices.append(str(device_uuid))

        return devices

    def get_instances_by_bot(
        self,
        *,
        bot_id: str,
        health_check: bool = False,
    ) -> dict[str, Any]:
        """实例列表 + 容器信息 + 健康四态（bot_id 入口，对话页下拉）。

        内部经 ``ext.binding.online`` 解析 binding_id，再复用 get_instances。

        Raises:
            BotPublishNotFoundError: bot_id 无 success 发布单 / ext.binding.online 缺失
            BindingNotFoundError: 解析出的 binding 校验不通过
        """
        binding_id = self._resolve_binding_id_by_bot_id(bot_id)
        return self.get_instances(binding_id=binding_id, health_check=health_check)

    # ── 实例重启（§2）────────────────────────────────────────────

    def restart_device(
        self,
        *,
        binding_id: int,
        device_uuid: str,
        operator: OperatorContext,
    ) -> dict[str, Any]:
        """指定设备重启（binding_id 入口，仅 owner）。

        校验 binding → owner 权限校验 → 取 bot_uuid → 调 PR1 已就绪的
        ``baas_service.restart_devices``（BaaS ``/update-devices``）。

        Args:
            binding_id: 设备绑定主键。
            device_uuid: 要重启的实例 uuid（来自实例列表 §1）。
            operator: 操作者上下文；仅 binding owner 可重启（admin 由 router 兜底）。

        Returns:
            ``{"publish_id": int}``：BaaS 发布工作流 ID。

        Raises:
            BindingNotFoundError: binding 校验不通过。
            InvalidDeviceStatusError: operator 非 owner。
            DeviceServiceError: BaasService 不可用。
        """
        record, bot_uuid = self._validate_binding_for_instances(binding_id)

        # 权限校验：仅 owner 可重启（admin 权限由 router 层兜底）。
        if record.entity_id != operator.staff_id:
            raise InvalidDeviceStatusError("仅 Bot 所有者可重启设备实例")

        baas_service = self._get_baas_service()
        if baas_service is None:
            raise DeviceServiceError("BaasService not available for device restart")

        # (#197) Deterministic, correlation-only request id (was uuid4): stable
        # across retries of the same logical restart so a BaaS log line traces
        # back to the exact binding + device. request_id is not a BaaS dedup key.
        # Must satisfy BaaS's request_id contract (32-64 chars, ^[A-Za-z0-9_-]$):
        # underscores, not dots — device_uuid ("BOT-"+32 hex) keeps it in range.
        request_id = f"restart_dev_b{binding_id}_{device_uuid}"
        data = baas_service.restart_devices(
            bot_uuid,
            device_uuids=[device_uuid],
            operator=operator.staff_id,
            request_id=request_id,
        )
        return {"publish_id": data.get("publish_id")}
