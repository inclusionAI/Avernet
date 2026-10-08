"""
Bot Health Checker Protocols

Pure Protocols extracted from BotHealthCheckerService and DeviceSourceProvider.
The implementation classes stay in core/service/health_check/bot/.
"""

from typing import Protocol, runtime_checkable

from secbaas.community.api.health_check.paas import PaasHealthCheckerResult

from ._enums import DeviceProviderType
from ._models import (
    BotAliveCheckResult,
    BotDeviceInfo,
    BotHealthCheckResult,
    PaasDeviceInfo,
    PaasDeviceListResponse,
    TTLExtendResult,
)


@runtime_checkable
class DeviceSourceProvider(Protocol):
    """设备来源协议。

    根据 device_provider 类型选择不同的实现。
    负责从不同数据源获取设备信息。
    """

    @property
    def provider_type(self) -> DeviceProviderType:
        """返回此 Provider 支持的设备来源类型。"""
        ...

    async def list_paas_device_by_bot(
        self,
        bot_id: str,
        entity_id: str,
        **kwargs,
    ) -> list["PaasDeviceInfo"]:
        """获取指定 Bot 的所有 PaaS 设备信息。"""
        ...

    async def extend_ttl_by_bot(
        self,
        bot_id: str,
        entity_id: str,
        binding_id: int | None = None,
    ) -> TTLExtendResult:
        """为指定 Bot 的所有设备延长 TTL。"""
        ...


@runtime_checkable
class BotHealthCheckerService(Protocol):
    """Bot 健康检查服务协议"""

    async def list_all_active_bot_device(
        self,
        page: int = 1,
        page_size: int = 20,
        bot_type: str | None = None,
        env: str = "prod",
    ) -> tuple[int, list[BotDeviceInfo]]:
        """获取所有活跃的 Bot 设备（分页）。
        支持按 bot_type 过滤 (personal/service)。
        """
        ...

    async def list_paas_device_by_bot(
        self,
        bot_id: str,
        entity_id: str,
        statuses: list[str] | None = None,
        env: str = "prod",
    ) -> "PaasDeviceListResponse":
        """获取指定 Bot 的所有 PaaS 设备信息。"""
        ...

    async def check_single_device(
        self,
        device: PaasDeviceInfo,
        active_engine: str | None = None,
    ) -> tuple[str, PaasHealthCheckerResult] | None:
        """对单个设备执行健康检查。

        Args:
            device: PaaS 设备信息
            active_engine: 引擎类型（可选，用于策略解析），默认 None 时使用 fallback 检查器

        Returns:
            (paas_device_id, PaasHealthCheckerResult) 或 None（跳过检查时）
        """
        ...

    async def check_health_by_bot(
        self,
        bot_id: str,
        entity_id: str,
        statuses: list[str] | None = None,
        env: str = "prod",
    ) -> BotHealthCheckResult:
        """对指定 Bot 的所有设备执行健康检查。"""
        ...

    async def check_alive_by_bot(
        self,
        bot_id: str,
        entity_id: str,
        minutes: int = 1440,
        env: str = "prod",
    ) -> BotAliveCheckResult:
        """检查指定 Bot 的所有设备是否活跃。"""
        ...

    async def extend_ttl_by_bot(
        self,
        bot_id: str,
        entity_id: str,
        env: str = "prod",
    ) -> TTLExtendResult:
        """为指定 Bot 的所有设备延长 TTL。"""
        ...

    async def get_sandbox_info(
        self,
        sandbox_id: str,
    ) -> dict[str, object] | None:
        """获取沙箱信息。"""
        ...
