"""ConnInfoBuilder — resolver 内部按 provider 委托的 conn_info 计算器 Protocol。

resolver 不重写底层算法,委托 builder 复用现有 plugin/service 路径:
- ArcaConnInfoBuilder → 复用 ArcaDeviceAccessor._get_arca_conn_info
- BaasConnInfoBuilder → 复用 build_baas_conn_info_for_http
- TeclawConnInfoBuilder → 复用 build_baas_conn_info(engine_type="teclaw")
- LocalConnInfoBuilder → 复用 LocalDeviceService 路径
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from agentclaw.community.core.devices.repository.record import DeviceBindingRecord


@runtime_checkable
class ConnInfoBuilder(Protocol):
    """按 binding 算出该 provider 下的拨号 dict。"""

    def build(
        self,
        binding: DeviceBindingRecord,
        user_id: str,
        *,
        device_uuid: str | None = None,
    ) -> dict[str, Any]:
        """根据 binding 算出该 provider 下的拨号 dict。

        Args:
            binding: 目标设备绑定
            user_id: 操作者身份(内部用作 device_affinity)
            device_uuid: 可选设备 UUID,用于多实例场景锁定特定实例;
                不传则由 provider 自动选活跃实例(单实例 provider 忽略)

        Raises:
            ConnInfoBuildError: 底层调用(baas /http-info / arca proxy)失败
        """
        ...
