"""Device Binding Query Service — 编排多步查询，替代 Repository 中的复杂 JOIN SQL。

将跨表查询拆解为简单的单表 Repository 调用 + Python JSON 解析。
Repository 只做 CRUD，编排逻辑在 Service。
"""

from typing import Any

from secbaas.core.repository.ac_bot import AcBotRepository
from secbaas.core.repository.ac_bot_publish import AcBotPublishRepository
from secbaas.core.repository.bot import BotRepository
from secbaas.core.repository.device import DeviceRepository
from secbaas.core.repository.device_binding import (
    DeviceBindingRecord,
    DeviceBindingRepository,
)
from secbaas.logger import get_logger

logger = get_logger("core-service")


class DeviceBindingQueryService:
    """编排跨表查询，替代 DeviceBindingRepository 中的复杂 JOIN / JSON_EXTRACT。

    依赖注入各单表 Repository，用 Python 串起多步查询逻辑。
    """

    def __init__(
        self,
        ac_bot_repo: AcBotRepository,
        ac_bot_publish_repo: AcBotPublishRepository,
        binding_repo: DeviceBindingRepository,
        bot_repo: BotRepository,
        device_repo: DeviceRepository,
    ) -> None:
        self._ac_bot_repo = ac_bot_repo
        self._ac_bot_publish_repo = ac_bot_publish_repo
        self._binding_repo = binding_repo
        self._bot_repo = bot_repo
        self._device_repo = device_repo

    # ── 分页查询 ──

    def list_all_active_bot_device(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        env: str = "prod",
        bot_type: str | None = None,
    ) -> tuple[int, list[dict[str, Any]]]:
        """分页查询所有活跃 Bot（替代原 2 表 JOIN SQL）。

        步骤：ac_bots(page, page_size, env, bot_type) → Python 构造 dict
        """
        total, bots = self._ac_bot_repo.list_active_bots(
            page=page, page_size=page_size, env=env, bot_type=bot_type
        )
        items = [
            {
                "bot_id": b.bot_id,
                "entity_id": b.entity_id,
                "binding_id": b.binding_id,
                "bot_type": b.bot_type,
                "status": b.status,
                "active_engine": b.active_engine,
            }
            for b in bots
        ]
        return total, items

    # ── Bot 级查询 ──

    def get_bot_binding(
        self,
        *,
        bot_id: str,
        entity_id: str,
        env: str = "prod",
    ) -> dict[str, Any] | None:
        """获取 Bot 的绑定信息（替代原 2 表 JOIN SQL）。

        步骤：ac_bots → ac_entity_device_binding
        """
        ac_bot = self._ac_bot_repo.get_active_by_entity_id_bot_id_env(
            entity_id=entity_id, bot_id=bot_id, env=env
        )
        if ac_bot is None:
            logger.info(
                "[get_bot_binding] Bot not found: bot_id=%s, entity_id=%s",
                bot_id,
                entity_id,
            )
            return None

        device_provider = None
        if ac_bot.binding_id is not None:
            binding = self._binding_repo.get_by_id(ac_bot.binding_id)
            if binding is not None:
                device_provider = binding.device_provider

        result = {
            "bot_id": ac_bot.bot_id,
            "entity_id": ac_bot.entity_id,
            "binding_id": ac_bot.binding_id,
            "bot_type": ac_bot.bot_type,
            "active_engine": ac_bot.active_engine,
            "status": ac_bot.status,
            "device_provider": device_provider,
        }
        logger.info(
            "[get_bot_binding] result: bot_type=%s, binding_id=%s",
            result["bot_type"],
            result["binding_id"],
        )
        return result

    def get_publish_binding(
        self,
        *,
        source_bot_id: str,
        status: str,
    ) -> int | None:
        """获取发布记录的 binding_id（已由 AcBotPublishRepository 在 Python 中解析 JSON）。"""
        return self._ac_bot_publish_repo.get_binding_id(
            source_bot_id=source_bot_id, status=status
        )

    # ── 设备列表查询 ──

    def list_paas_device_by_bot_personal(
        self,
        *,
        bot_id: str,
        binding_id: int,
    ) -> list[dict[str, Any]]:
        """personal 类型：查 binding 记录，根据 device_provider 分支查询。

        - arca: ac_entity_device_binding(id) → Python JSON 解析 device_props
        - baas: ac_entity_device_binding(id) → baas_bot(bot_uuid) → baas_device
        """
        binding = self._binding_repo.get_by_id(binding_id)
        if binding is None or binding.status != "ACTIVE":
            logger.info(
                "[list_paas_device_by_bot_personal] No ACTIVE binding: binding_id=%s",
                binding_id,
            )
            return []

        # baas provider: 走 baas 链路查询
        if binding.device_provider and binding.device_provider.lower() == "baas":
            devices = self._resolve_devices_from_binding(binding_id, "personal")
            logger.info(
                "[list_paas_device_by_bot_personal] baas provider: returned %s items for bot_id=%s, binding_id=%s",
                len(devices),
                bot_id,
                binding_id,
            )
            return devices

        # arca provider (默认): 从 device_props 解析
        props = binding.device_props or {}
        item = {
            "paas_device_id": props.get("sandbox_id") or "",
            "provider_type": binding.device_provider,
            "status": binding.status,
            "query_status": "personal",
            "ttl_expiration_time": props.get("ttl_expiration_time"),
            "ttl_expiration_timestamp": _parse_int(
                props.get("ttl_expiration_timestamp")
            ),
            "device_uuid": None,
            "source_table_id": binding_id,
            "source_table": "ac_binding",
            "refresh_fail_count": int(props.get("refresh_fail_count") or 0),
        }
        logger.info(
            "[list_paas_device_by_bot_personal] arca provider: returned 1 item for bot_id=%s",
            bot_id,
        )
        return [item]

    def list_paas_device_by_bot_service(
        self,
        *,
        bot_id: str,
        entity_id: str,
        statuses: list[str],
        env: str = "prod",
    ) -> list[dict[str, Any]]:
        """service 类型：按状态分步查询，Python 编排替代 5 表 JOIN。

        - draft: ac_bots.binding_id → ac_entity_device_binding
        - validating: ac_bot_publish → binding_id → baas_bot → list_by_bot_id → baas_device
        - online: 同 validating，只是 status 和 JSON 路径不同
        """
        items: list[dict[str, Any]] = []
        for s in statuses:
            if s == "draft":
                items.extend(self._query_service_devices_draft(bot_id, entity_id, env))
            elif s == "validating":
                items.extend(
                    self._query_service_devices_validating(bot_id, entity_id, env)
                )
            elif s == "online":
                items.extend(self._query_service_devices_online(bot_id, entity_id, env))
            else:
                logger.warning(
                    "[list_paas_device_by_bot_service] Unknown status: %s, skipping", s
                )

        logger.info(
            "[list_paas_device_by_bot_service] returned %s items for bot_id=%s, "
            "entity_id=%s, statuses=%s, env=%s",
            len(items),
            bot_id,
            entity_id,
            statuses,
            env,
        )
        return items

    def _query_service_devices_draft(
        self, bot_id: str, entity_id: str, env: str
    ) -> list[dict[str, Any]]:
        """草稿态：ac_bots.binding_id → ac_entity_device_binding

        对齐 0525 SQL: WHERE is_delete=0 AND status='ACTIVE'
        """
        ac_bot = self._ac_bot_repo.get_active_by_entity_id_bot_id_env(
            entity_id=entity_id, bot_id=bot_id, env=env
        )
        if ac_bot is None or ac_bot.binding_id is None:
            return []

        binding = self._binding_repo.get_by_id(ac_bot.binding_id)
        if binding is None or binding.status != "ACTIVE":
            return []

        props = binding.device_props or {}
        return [self._binding_to_device_dict(binding, props, "ac_binding", "draft")]

    def _query_service_devices_validating(
        self, bot_id: str, entity_id: str, env: str
    ) -> list[dict[str, Any]]:
        """验证态：ac_bot_publish.binding.verify → baas_bot → list_active_devices_by_bot_id → baas_device

        对齐 0525 SQL: 取所有匹配的 binding_id（去重），env 在此层过滤。
        """
        binding_ids = self._ac_bot_publish_repo.get_binding_ids(
            source_bot_id=bot_id, status="validating", owner_id=entity_id, env=env
        )
        if not binding_ids:
            return []
        items: list[dict[str, Any]] = []
        for bid in binding_ids:
            items.extend(self._resolve_devices_from_binding(bid, "validating"))
        return items

    def _query_service_devices_online(
        self, bot_id: str, entity_id: str, env: str
    ) -> list[dict[str, Any]]:
        """发布态：ac_bot_publish.binding.online → baas_bot → list_active_devices_by_bot_id → baas_device

        对齐 0525 SQL: 取所有匹配的 binding_id（去重），env 在此层过滤。
        """
        binding_ids = self._ac_bot_publish_repo.get_binding_ids(
            source_bot_id=bot_id, status="success", owner_id=entity_id, env=env
        )
        if not binding_ids:
            return []
        items: list[dict[str, Any]] = []
        for bid in binding_ids:
            items.extend(self._resolve_devices_from_binding(bid, "online"))
        return items

    def _resolve_devices_from_binding(
        self, binding_id: int, query_status: str
    ) -> list[dict[str, Any]]:
        """从 binding_id 查到 baas_device 列表（2 步查询）。

        对齐 0525 SQL:
          baas_bot b ON b.bot_uuid = eb.device_id AND b.status = 'ACTIVE'
          baas_device d ON d.device_uuid = r.device_uuid AND d.is_deleted = 0 AND d.status = 'ACTIVE'
        不再过滤 tenant/env，不再 fallback。
        """
        binding = self._binding_repo.get_by_id(binding_id)
        if binding is None:
            return []

        device_id = binding.device_id
        if not device_id:
            return []

        # baas_bot: binding.device_id 即 bot_uuid
        # 对齐 0525 SQL: 只按 bot_uuid + status='ACTIVE' 查询，不过滤 tenant/env/is_deleted
        bot = self._bot_repo.get_active_by_bot_uuid_only(
            bot_uuid=device_id,
        )
        if bot is None:
            # 对齐 0525 SQL: INNER JOIN 匹配不到即返回空
            return []

        # 对齐 0525 SQL: d.is_deleted=0 AND d.status='ACTIVE'，不过滤 tenant/env
        devices = self._device_repo.list_active_devices_by_bot_id(
            bot_id=bot.id,
        )

        return [self._device_to_dict(d, "baas_device", query_status) for d in devices]

    # ── 沙箱查询 ──

    def list_sandboxes_by_bot(
        self,
        *,
        bot_id: str,
        entity_id: str,
        env: str | None = None,
    ) -> tuple[dict[str, Any] | None, list[DeviceBindingRecord]]:
        """通过 bot_id 和 entity_id 查询对应的激活沙箱列表。

        步骤：ac_bots(bot_id, entity_id) → ac_entity_device_binding(device_id)
        """
        ac_bot = self._ac_bot_repo.get_by_entity_id_bot_id_env(
            entity_id=entity_id, bot_id=bot_id, env=env or "prod"
        )
        if ac_bot is None:
            logger.info(
                "[list_sandboxes_by_bot] Bot not found: bot_id=%s, entity_id=%s",
                bot_id,
                entity_id,
            )
            return None, []

        bot_info = {
            "bot_id": ac_bot.bot_id,
            "bot_name": ac_bot.bot_name,
            "bot_status": ac_bot.status,
            "active_engine": ac_bot.active_engine,
            "entity_id": ac_bot.entity_id,
            "entity_type": ac_bot.entity_type,
            "device_id": ac_bot.device_id,
            "created_at": ac_bot.gmt_create,
            "modified_at": ac_bot.gmt_modified,
        }

        device_id = ac_bot.device_id
        if not device_id:
            logger.info(
                "[list_sandboxes_by_bot] Bot has no device_id: bot_id=%s", bot_id
            )
            return bot_info, []

        sandboxes = self._binding_repo.list_by_device_id(
            device_id=device_id, status="ACTIVE", env=env
        )

        logger.info(
            "[list_sandboxes_by_bot] found %s sandboxes for bot_id=%s",
            len(sandboxes),
            bot_id,
        )
        return bot_info, sandboxes

    # ── TTL 更新 ──

    def update_baas_device_ttl(
        self,
        *,
        device_uuid: str,
        ttl_expiration_time: str,
        ttl_expiration_timestamp: int,
    ) -> None:
        """更新 baas_device.provider_device_props 中的 TTL 字段。

        步骤：baas_device(device_uuid) → Python JSON 解析 → update
        """
        device = self._device_repo.get_by_device_uuid_only(device_uuid)
        if device is None:
            logger.warning(
                "[update_baas_device_ttl] Device not found: device_uuid=%s",
                device_uuid,
            )
            return

        props = dict(device.provider_device_props or {})
        props["ttl_expiration_time"] = ttl_expiration_time
        props["ttl_expiration_timestamp"] = ttl_expiration_timestamp

        self._device_repo.update_device(
            device_id=device.id,
            tenant=device.tenant,
            env=device.env,
            provider_device_props=props,
        )
        logger.info(
            "[update_baas_device_ttl] device %s updated successfully", device_uuid
        )

    def update_baas_device_ttl_by_id(
        self,
        *,
        baas_device_id: int,
        ttl_expiration_time: str,
        ttl_expiration_timestamp: int,
        refresh_fail_count: int = 0,
    ) -> None:
        """按 baas_device.id 更新 TTL 字段（委托 binding_repo 的 text SQL 实现）。"""
        self._binding_repo.update_baas_device_ttl_by_id(
            baas_device_id=baas_device_id,
            ttl_expiration_time=ttl_expiration_time,
            ttl_expiration_timestamp=ttl_expiration_timestamp,
            refresh_fail_count=refresh_fail_count,
        )

    def update_baas_device_refresh_fail_count_by_id(
        self,
        *,
        baas_device_id: int,
        refresh_fail_count: int,
    ) -> None:
        """按 baas_device.id 更新 refresh_fail_count（委托 binding_repo 的 text SQL 实现）。"""
        self._binding_repo.update_baas_device_refresh_fail_count_by_id(
            baas_device_id=baas_device_id,
            refresh_fail_count=refresh_fail_count,
        )

    # ── 辅助方法 ──

    @staticmethod
    def _binding_to_device_dict(
        binding: DeviceBindingRecord,
        props: dict[str, Any],
        source_table: str,
        query_status: str | None = None,
    ) -> dict[str, Any]:
        """将 binding 记录转为设备信息字典（Python 解析 JSON）。"""
        return {
            "device_uuid": None,
            "paas_device_id": props.get("sandbox_id") or "",
            "provider_type": binding.device_provider,
            "status": binding.status,
            "query_status": query_status,
            "ttl_expiration_time": props.get("ttl_expiration_time"),
            "ttl_expiration_timestamp": _parse_int(
                props.get("ttl_expiration_timestamp")
            ),
            "source_table": source_table,
            "source_table_id": str(binding.id),
            "refresh_fail_count": int(props.get("refresh_fail_count") or 0),
        }

    @staticmethod
    def _device_to_dict(
        device: Any,
        source_table: str,
        query_status: str | None = None,
    ) -> dict[str, Any]:
        """将 DeviceRecord 转为设备信息字典（Python 解析 JSON）。"""
        props = device.provider_device_props or {}
        return {
            "device_uuid": device.device_uuid,
            "paas_device_id": device.provider_device_id or "",
            "provider_type": device.provider_type,
            "status": device.status,
            "query_status": query_status,
            "ttl_expiration_time": props.get("ttl_expiration_time"),
            "ttl_expiration_timestamp": _parse_int(
                props.get("ttl_expiration_timestamp")
            ),
            "source_table": source_table,
            "source_table_id": str(device.id),
            "refresh_fail_count": int(props.get("refresh_fail_count") or 0),
        }


def _parse_int(value: Any) -> int | None:
    """安全地将值转为 int，用于 TTL timestamp 解析。"""
    if value is None:
        return None
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return None
