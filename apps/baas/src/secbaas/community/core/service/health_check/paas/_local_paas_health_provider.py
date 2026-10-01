"""本地设备健康检查实现。"""

from secbaas.community.api.health_check.paas import PaasHealthCheckerResult

from ._paas_health_provider import PaaSHealthProvider


class LocalPaaSHealthProvider(PaaSHealthProvider):
    """本地设备健康检查实现。

    本地设备无需健康检查，直接返回健康状态。
    """

    async def check_health(
        self,
        paas_device_id: str,
        checkers: list[str],
    ) -> PaasHealthCheckerResult:
        """本地设备健康检查 - 直接返回健康。

        Args:
            paas_device_id: 本地设备 ID
            checkers: 检查器列表（本地设备忽略）

        Returns:
            PaasHealthCheckerResult，overall_healthy=True，checkers 为空
        """
        return PaasHealthCheckerResult(
            paas_device_id=paas_device_id,
            overall_healthy=True,
            checkers={},  # 本地设备无检查器
        )
