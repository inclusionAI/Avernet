"""Sigma 平台健康检查实现（预留）。"""

from typing import TYPE_CHECKING

from secbaas.api.health_check.paas import PaasHealthCheckerResult

from ._paas_health_provider import PaaSHealthProvider

if TYPE_CHECKING:
    pass


class SigmaPaaSHealthProvider(PaaSHealthProvider):
    """Sigma 平台健康检查实现（预留接口）。

    当前不支持，调用时抛出 NotImplementedError。
    """

    async def check_health(
        self,
        paas_device_id: str,
        checkers: list[str],
    ) -> PaasHealthCheckerResult:
        """Sigma 平台健康检查（预留）。

        Raises:
            NotImplementedError: 当前不支持 Sigma 平台健康检查
        """
        raise NotImplementedError("Sigma platform health check is not yet implemented")
