"""PaaS Health Provider抽象基类。

第二层Provider，负责执行平台特定的健康检查协议。
"""

from abc import ABC, abstractmethod

from secbaas.api.health_check.paas import (
    HealthCheckerStrategyResult,
    PaasHealthCheckerResult,
)


class PaaSHealthProvider(ABC):
    """平台健康检查抽象类。

    根据 provider_type 选择对应的健康检查实现。
    只负责健康检查，TTL操作由 PaasServiceFacade 统一处理。
    """

    @abstractmethod
    async def check_health(
        self,
        paas_device_id: str,
        checkers: list[str],
    ) -> "PaasHealthCheckerResult":
        """执行健康检查。

        Args:
            paas_device_id: PaaS 设备 ID (格式: "{sandbox_id}@{template_id}")
            checkers: 需要执行的检查器列表 (如 ["engine", "adapter", "gateway"])

        Returns:
            PaasHealthCheckerResult 包含各检查器的结果
        """
        ...

    async def check_alive(
        self,
        paas_device_id: str,
        minutes: int = 1440,
        checkers: list[str] | None = None,
    ) -> "HealthCheckerStrategyResult":
        """检查设备是否活跃（alive）。

        子类必须覆盖此方法以提供平台特定检查。

        Args:
            paas_device_id: PaaS 设备 ID
            minutes: 检查最近 N 分钟内是否有活跃会话
            checkers: 需要执行的检查器列表，必须由策略解析提供

        Returns:
            HealthCheckerStrategyResult 包含活跃检查结果

        Raises:
            NotImplementedError: 子类未实现时
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not implement check_alive"
        )
