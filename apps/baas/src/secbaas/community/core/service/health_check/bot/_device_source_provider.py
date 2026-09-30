"""设备来源 Provider 抽象基类。

第一层Provider，负责根据设备类型路由到不同的数据源。
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import TYPE_CHECKING

from secbaas.community.api.health_check.bot import (
    BotHealthCheckerConfig,
    DeviceProviderType,
    PaasDeviceInfo,
    TTLExtendResult,
)
from secbaas.community.core.service.paas import PaasServiceFacade
from secbaas.community.logger import get_logger

logger = get_logger("core-service")

if TYPE_CHECKING:
    from secbaas.community.core.repository.device_binding import (
        DeviceBindingRepository,
    )
    from secbaas.community.core.service.device_binding_query import (
        DeviceBindingQueryService,
    )


class DeviceSourceProvider(ABC):
    """设备来源抽象类。

    根据 device_provider 类型选择不同的实现。
    负责从不同数据源获取设备信息。
    """

    def __init__(
        self,
        device_binding_repo: "DeviceBindingRepository",
        paas_facade: "PaasServiceFacade",
        config: BotHealthCheckerConfig | None = None,
        query_service: "DeviceBindingQueryService | None" = None,
    ):
        """初始化 Provider。

        Args:
            device_binding_repo: 设备绑定仓库（简单 CRUD）
            paas_facade: PaaS 服务门面
            config: 配置对象，为 None 时使用默认配置
            query_service: 跨表查询编排服务，推荐传入
        """
        self._device_binding_repo = device_binding_repo
        self._paas_facade = paas_facade
        self._config = config or BotHealthCheckerConfig()
        self._query_service = query_service

    @property
    @abstractmethod
    def provider_type(self) -> DeviceProviderType:
        """返回此 Provider 支持的设备来源类型。"""
        ...

    def _should_extend_ttl(self, ttl_timestamp: int | None) -> bool:
        """判断是否需要续期 TTL。

        当剩余时间 ≤ extend_when_remaining_hours 时才续期。

        Args:
            ttl_timestamp: TTL 过期时间戳（毫秒）

        Returns:
            True 表示需要续期
        """
        if ttl_timestamp is None:
            # 无 TTL 信息，需要续期
            return True

        now_ms = int(datetime.now().timestamp() * 1000)
        remaining_ms = ttl_timestamp - now_ms
        remaining_hours = remaining_ms / (1000 * 60 * 60)

        return remaining_hours <= self._config.extend_when_remaining_hours

    async def refresh_device_ttl(self, device: PaasDeviceInfo) -> int | None:
        """从 Arca SDK 刷新设备 TTL 信息并写入数据库，更新 device 对象。

        仅当 provider_type 为 ARCA 时执行刷新，非 ARCA 设备直接跳过。
        根据 device.source_table 路由到对应的数据库更新方法。
        刷新成功时重置 refresh_fail_count 为 0，失败时 +1。

        Args:
            device: PaaS 设备信息

        Returns:
            TTL 时间戳（毫秒）或 None
        """
        if not device.paas_device_id:
            return None

        # 非 ARCA 设备不支持 Arca SDK 刷新
        if device.provider_type and device.provider_type.upper() != "ARCA":
            return None

        try:
            device_info = await self._paas_facade.get_device_info(device.paas_device_id)
            if device_info and device_info.ttl_timestamp:
                ttl_timestamp = device_info.ttl_timestamp
                ttl_time_str = datetime.fromtimestamp(ttl_timestamp / 1000).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )

                # 更新 device 对象字段
                device.ttl_expiration_timestamp = ttl_timestamp
                device.ttl_expiration_time = ttl_time_str
                device.refresh_fail_count = 0

                # 写回数据库（成功时重置 refresh_fail_count）
                self._update_device_ttl_to_db(
                    device=device,
                    ttl_expiration_timestamp=ttl_timestamp,
                    ttl_expiration_time=ttl_time_str,
                    refresh_fail_count=0,
                )

                return ttl_timestamp

            # device_info 为空或 ttl_timestamp 为空，视为刷新失败
            device.refresh_fail_count += 1
            self._update_device_refresh_fail_count_to_db(device)

        except Exception as e:
            logger.warning(
                f"[DeviceSourceProvider] Failed to refresh TTL from Arca for {device.paas_device_id}: {e}"
            )
            device.refresh_fail_count += 1
            self._update_device_refresh_fail_count_to_db(device)

        return None

    def _update_device_ttl_to_db(
        self,
        device: PaasDeviceInfo,
        ttl_expiration_timestamp: int,
        ttl_expiration_time: str,
        refresh_fail_count: int = 0,
    ) -> None:
        """将 TTL 和 refresh_fail_count 写回数据库。"""
        if not device.source_table or not device.source_table_id:
            return

        if device.source_table == "ac_binding":
            self._device_binding_repo.update_device_props_ttl(
                binding_id=int(device.source_table_id),
                ttl_expiration_timestamp=ttl_expiration_timestamp,
                ttl_expiration_time=ttl_expiration_time,
                refresh_fail_count=refresh_fail_count,
            )
        elif device.source_table == "baas_device":
            if self._query_service is not None:
                self._query_service.update_baas_device_ttl_by_id(
                    baas_device_id=int(device.source_table_id),
                    ttl_expiration_time=ttl_expiration_time,
                    ttl_expiration_timestamp=ttl_expiration_timestamp,
                    refresh_fail_count=refresh_fail_count,
                )
            else:
                self._device_binding_repo.update_baas_device_ttl_by_id(
                    baas_device_id=int(device.source_table_id),
                    ttl_expiration_time=ttl_expiration_time,
                    ttl_expiration_timestamp=ttl_expiration_timestamp,
                    refresh_fail_count=refresh_fail_count,
                )

    def _update_device_refresh_fail_count_to_db(self, device: PaasDeviceInfo) -> None:
        """仅将 refresh_fail_count 写回数据库（TTL 无有效值时使用）。"""
        if not device.source_table or not device.source_table_id:
            return

        if device.source_table == "ac_binding":
            self._device_binding_repo.update_device_props_refresh_fail_count(
                binding_id=int(device.source_table_id),
                refresh_fail_count=device.refresh_fail_count,
            )
        elif device.source_table == "baas_device":
            if self._query_service is not None:
                self._query_service.update_baas_device_refresh_fail_count_by_id(
                    baas_device_id=int(device.source_table_id),
                    refresh_fail_count=device.refresh_fail_count,
                )
            else:
                self._device_binding_repo.update_baas_device_refresh_fail_count_by_id(
                    baas_device_id=int(device.source_table_id),
                    refresh_fail_count=device.refresh_fail_count,
                )

    @abstractmethod
    async def list_paas_device_by_bot(
        self,
        bot_id: str,
        entity_id: str,
        **kwargs,  # Personal: binding_id: int, Service: statuses: list[str]
    ) -> list[PaasDeviceInfo]:
        """获取指定 Bot 的所有 PaaS 设备信息。

        Personal: 传入 binding_id
        Service: 传入 entity_id 和 statuses

        Args:
            bot_id: Bot ID
            entity_id: 实体 ID

        Returns:
            PaasDeviceInfo 列表
        """
        ...

    @abstractmethod
    async def extend_ttl_by_bot(
        self,
        bot_id: str,
        entity_id: str,
        binding_id: int | None = None,
    ) -> TTLExtendResult:
        """为指定 Bot 的所有设备延长 TTL。

        内部自动处理所有状态的设备。

        Args:
            bot_id: Bot ID
            entity_id: 实体 ID（用于 ac_bots 和 ac_bot_publish 表过滤）
            binding_id: ac_entity_device_binding.id（仅 personal 类型需要）

        Returns:
            TTLExtendResult 包含延长结果
        """
        ...
