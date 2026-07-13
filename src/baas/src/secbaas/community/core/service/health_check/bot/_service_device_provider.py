"""Service 类型设备的 Provider 实现。

从 baas_device 表获取设备信息，支持多设备。
"""

from datetime import datetime
from typing import TYPE_CHECKING

from secbaas.community.api.health_check.bot import (
    BotHealthCheckerConfig,
    DeviceProviderType,
    PaasDeviceInfo,
    TTLExtendResult,
    TTLInfo,
)
from secbaas.community.core.service.paas import PaasServiceFacade
from secbaas.community.logger import get_logger

from ._device_source_provider import DeviceSourceProvider

if TYPE_CHECKING:
    from secbaas.community.core.repository.device_binding import (
        DeviceBindingRepository,
    )
    from secbaas.community.core.service.device_binding_query import (
        DeviceBindingQueryService,
    )

logger = get_logger("core-service")


class ServiceDeviceProvider(DeviceSourceProvider):
    """Service 类型设备 Provider。

    Service Bot 可以有多个设备，绑定关系存储在 baas_device 表。
    支持三种状态：draft、validating、online。
    """

    def __init__(
        self,
        device_binding_repo: "DeviceBindingRepository",
        paas_facade: "PaasServiceFacade",
        config: BotHealthCheckerConfig | None = None,
        query_service: "DeviceBindingQueryService | None" = None,
    ):
        super().__init__(device_binding_repo, paas_facade, config, query_service)

    @property
    def provider_type(self) -> DeviceProviderType:
        return DeviceProviderType.BAAS

    async def list_paas_device_by_bot(
        self,
        bot_id: str,
        entity_id: str,
        statuses: list[str],
        env: str = "prod",
    ) -> list[PaasDeviceInfo]:
        """获取指定 Service Bot 的 PaaS 设备信息。

        根据状态查询不同链路：
        - draft: ac_bots.binding_id → ac_entity_device_binding
        - validating: ac_bot_publish.ext->'$.binding.verify' → baas_device
        - online: ac_bot_publish.ext->'$.binding.online' → baas_device

        Args:
            bot_id: Bot ID (ac_bots.bot_id 或 ac_bot_publish.source_bot_id)
            entity_id: 实体 ID（用于 ac_bots 和 ac_bot_publish 表过滤）
            statuses: 要查询的状态列表
            env: 环境参数

        Returns:
            PaasDeviceInfo 列表
        """
        if self._query_service is not None:
            devices = self._query_service.list_paas_device_by_bot_service(
                bot_id=bot_id,
                entity_id=entity_id,
                statuses=statuses,
                env=env,
            )
        else:
            devices = self._device_binding_repo.list_paas_device_by_bot_service(
                bot_id=bot_id,
                entity_id=entity_id,
                statuses=statuses,
            )

        return [
            PaasDeviceInfo(
                paas_device_id=device["paas_device_id"],
                device_uuid=device.get("device_uuid"),
                provider_type=device.get("provider_type"),
                status=device.get("status", "UNKNOWN"),
                query_status=device.get("query_status"),
                ttl_expiration_time=device.get("ttl_expiration_time"),
                ttl_expiration_timestamp=device.get("ttl_expiration_timestamp"),
                source_table=device.get("source_table"),
                source_table_id=device.get("source_table_id"),
                refresh_fail_count=device.get("refresh_fail_count", 0),
            )
            for device in devices
        ]

    async def extend_ttl_by_bot(
        self,
        bot_id: str,
        entity_id: str,
        binding_id: int | None = None,
    ) -> TTLExtendResult:
        """为 Service Bot 的所有设备延长 TTL。

        当剩余 TTL ≤ extend_when_remaining_hours 时才续期。
        续期后会更新聚合 TTL（多设备取最小值）到 ac_entity_device_binding.device_props。
        内部自动查询所有状态的设备（draft/validating/online）。

        Args:
            bot_id: Bot ID
            entity_id: 实体 ID（用于 ac_bots 和 ac_bot_publish 表过滤）
            binding_id: ac_entity_device_binding.id，用于更新聚合 TTL

        Returns:
            TTLExtendResult
        """
        # TTL 续期默认处理所有状态
        statuses = ["draft", "validating", "online"]

        # 1. 获取设备列表
        devices = await self.list_paas_device_by_bot(bot_id, entity_id, statuses)
        if not devices:
            return TTLExtendResult(
                bot_id=bot_id,
                bot_type="service",
                total_devices=0,
                extended_count=0,
                skipped_count=0,
                failed_count=0,
                details=[],
                error="No devices found",
            )

        # 2. 逐个判断并延长 TTL
        details: list[TTLInfo] = []
        extended_count = 0
        skipped_count = 0
        failed_count = 0

        for device in devices:
            # 跳过 paas_device_id 为空的设备
            if not device.paas_device_id:
                logger.warning(
                    f"[ServiceDeviceProvider] Skipping TTL extend: "
                    f"paas_device_id is empty, bot_id={bot_id}"
                )
                details.append(
                    TTLInfo(
                        paas_device_id=device.paas_device_id,
                        old_expiration_time=None,
                        new_expiration_time=None,
                        success=False,
                        skipped=True,
                        error="paas_device_id is empty",
                    )
                )
                skipped_count += 1
                continue

            # TTL 缺失时从 Arca SDK 刷新
            ttl_ts = device.ttl_expiration_timestamp
            if ttl_ts is None:
                ttl_ts = await self.refresh_device_ttl(device)

            # 判断是否需要续期
            should_extend = self._should_extend_ttl(ttl_ts)

            if not should_extend:
                # 剩余时间充足，跳过续期
                logger.info(
                    f"[ServiceDeviceProvider] Skipping TTL extend for {device.paas_device_id}, "
                    f"remaining time > {self._config.extend_when_remaining_hours}h"
                )
                details.append(
                    TTLInfo(
                        paas_device_id=device.paas_device_id,
                        old_expiration_time=(
                            datetime.fromtimestamp(ttl_ts / 1000) if ttl_ts else None
                        ),
                        new_expiration_time=(
                            datetime.fromtimestamp(ttl_ts / 1000) if ttl_ts else None
                        ),
                        success=False,
                        skipped=True,
                        error=None,
                    )
                )
                skipped_count += 1
                continue

            # 执行续期
            try:
                ttl_info = await self._paas_facade.update_device_ttl(
                    paas_device_id=device.paas_device_id
                )
                details.append(ttl_info)
                if ttl_info.success:
                    extended_count += 1
                    # 按 source_table 路由更新数据库中的 TTL（成功时重置 refresh_fail_count）
                    if (
                        ttl_info.new_expiration_time
                        and device.source_table
                        and device.source_table_id
                    ):
                        ttl_timestamp = int(
                            ttl_info.new_expiration_time.timestamp() * 1000
                        )
                        ttl_time_str = ttl_info.new_expiration_time.strftime(
                            "%Y-%m-%d %H:%M:%S"
                        )
                        self._update_device_ttl_to_db(
                            device=device,
                            ttl_expiration_timestamp=ttl_timestamp,
                            ttl_expiration_time=ttl_time_str,
                            refresh_fail_count=0,
                        )
                    logger.info(
                        f"[ServiceDeviceProvider] Extended TTL for {device.paas_device_id}: "
                        f"new_expire_at={ttl_info.new_expiration_time}"
                    )
                else:
                    failed_count += 1
                    device.refresh_fail_count += 1
                    self._update_device_refresh_fail_count_to_db(device)
                    logger.warning(
                        f"[ServiceDeviceProvider] Failed to extend TTL for {device.paas_device_id}: "
                        f"error={ttl_info.error}"
                    )
            except Exception as e:
                logger.error(
                    f"[ServiceDeviceProvider] Exception extending TTL for {device.paas_device_id}: {e}"
                )
                failed_count += 1
                device.refresh_fail_count += 1
                self._update_device_refresh_fail_count_to_db(device)
                details.append(
                    TTLInfo(
                        paas_device_id=device.paas_device_id,
                        old_expiration_time=(
                            datetime.fromtimestamp(ttl_ts / 1000) if ttl_ts else None
                        ),
                        new_expiration_time=None,
                        success=False,
                        skipped=False,
                        error=str(e),
                    )
                )

        return TTLExtendResult(
            bot_id=bot_id,
            bot_type="service",
            total_devices=len(devices),
            extended_count=extended_count,
            skipped_count=skipped_count,
            failed_count=failed_count,
            details=details,
            error=None if failed_count == 0 else f"{failed_count} devices failed",
        )
